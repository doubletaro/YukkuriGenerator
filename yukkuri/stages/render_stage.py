"""動画合成ステージ。

- 口パク: 音素タイムライン → シーン(全キャラの感情/口の組)の変化点のみフレーム生成
- 字幕: ASS ファイルを ffmpeg subtitles フィルタで焼き込み
- 音声: セリフ wav を gap で連結、BGM を低音量ミックス
- エンコーダ: NVENC 自動検出 → 失敗時 libx264

同期変数は全て config/render.yaml 由来。
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

from yukkuri.config import CharactersConfig, RenderConfig
from yukkuri.engines.base import AvatarRenderer
from yukkuri.models import AudioTrack, PipelineContext, RenderResult
from yukkuri.stages.base import Stage

logger = logging.getLogger(__name__)

MOUTH_OF = {"a": "a", "i": "i", "u": "u", "e": "e", "o": "o",
            "closed": "closed", "pause": "pause"}


def find_ffmpeg() -> str:
    if which := shutil.which("ffmpeg"):
        return which
    candidates = [
        Path.home() / "AppData/Local/Microsoft/WinGet/Packages",
        Path.home() / "AppData/Local/hermes/tools",
    ]
    for base in candidates:
        if base.exists():
            hits = sorted(base.rglob("ffmpeg.exe"))
            if hits:
                return str(hits[0])
    return "ffmpeg"


def detect_encoder(ffmpeg: str, prefer: str, fallback: str) -> str:
    test = subprocess.run(
        [ffmpeg, "-hide_banner", "-v", "error", "-f", "lavfi",
         "-i", "color=c=black:s=320x240", "-frames:v", "3", "-c:v", prefer, "-f", "null", "-"],
        capture_output=True, timeout=60)
    if test.returncode == 0:
        return prefer
    logger.warning("エンコーダ %s が使えないため %s にフォールバック", prefer, fallback)
    return fallback


def _ass_time(sec: float) -> str:
    h = int(sec // 3600)
    m = int(sec % 3600 // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"


# 聞き手の反応表情(話者の感情 → 聞き手の感情)
LISTENER_REACTION = {
    "happy": "happy", "excited": "happy", "surprised": "surprised",
    "angry": "fail",            # ツッコまれた側は大げさにやられる
    "sad": "sad", "fail": "surprised", "shy": "happy",
    "smug": "normal", "whisper": "whisper", "think": "normal", "normal": "normal",
}


class RenderStage(Stage):
    name = "render"

    def __init__(self, render_cfg: RenderConfig, char_cfg: CharactersConfig,
                 avatar: AvatarRenderer, project_root: Path):
        self.cfg = render_cfg
        self.char_cfg = char_cfg
        self.avatar = avatar
        self.root = Path(project_root)
        self._scenes: dict[str, dict[str, tuple[str, str]]] = {}
        self._sprite_cache: dict[tuple[str, str, str, int], object] = {}  # (char,emo,mouth,av_h)→PIL
        self._known_emotions = set(getattr(char_cfg, "emotions", []) or []) | {"normal"}

    # ── タイムライン構築 ──────────────────────────────────
    @staticmethod
    def _phoneme_frames(phonemes, min_sec: float, scale: float = 1.0) -> list[tuple[str, float]]:
        """[(mouth, duration)] — 連続同一口を統合し、min_sec 未満は直前フレームへ吸収。

        scale: 口パク微調整係数(音素durationに乗算)。字幕は音声実長基準なので
        ここだけで映像側を調整する(StockSeeker の speedScale 補正と同発想)。
        """
        frames: list[tuple[str, float]] = []
        for ph in phonemes:
            mouth = MOUTH_OF.get(ph.phoneme, "closed")
            dur = ph.duration * scale
            if frames and frames[-1][0] == mouth:
                frames[-1] = (mouth, frames[-1][1] + dur)
            else:
                frames.append((mouth, dur))
        merged: list[tuple[str, float]] = []
        for mouth, dur in frames:
            if merged and dur < min_sec:
                merged[-1] = (merged[-1][0], merged[-1][1] + dur)
            else:
                merged.append((mouth, dur))
        # 2nd pass: 先頭フレーム由来の短フレームを次(末尾なら前)に吸収
        i = 0
        while len(merged) > 1 and i < len(merged):
            if merged[i][1] < min_sec:
                if i + 1 < len(merged):
                    merged[i + 1] = (merged[i + 1][0], merged[i + 1][1] + merged[i][1])
                else:
                    merged[i - 1] = (merged[i - 1][0], merged[i - 1][1] + merged[i][1])
                del merged[i]
            else:
                i += 1
        return [(m, d) for m, d in merged if d > 0]

    @staticmethod
    def _scene_key(scene: dict[str, tuple[str, str]]) -> str:
        return "scene_" + "__".join(f"{c}-{e}-{m}" for c, (e, m) in sorted(scene.items()))

    def build_timeline(self, track: AudioTrack) -> list[tuple[str, float]]:
        """[(scene_key, duration)] — title/idle_end は特殊キー。"""
        cfg = self.char_cfg
        state: dict[str, tuple[str, str]] = {c: ("normal", "closed") for c in cfg.characters}
        idle_key = self._scene_key(state)

        # 状態変化イベント: (絶対時刻, char, emotion, mouth)
        # StockSeeker render_lip_sync_to_file 方式:
        #   - 行の長さは wavヘッダ実長(la.duration)を厳密に使う
        #   - 音素フレームは定数のまま順に敷き詰め、余りは最終フレームを
        #     ホールド(duration 9999 相当)、溢れは -t 切断相当に切り捨てる
        events: list[tuple[float, str, str, str]] = []
        title_dur = self.cfg.title_duration_sec
        for i, la in enumerate(track.lines):
            emo = la.line.emotion if la.line.emotion in self._known_emotions else "normal"
            sp = la.line.speaker
            t = title_dur + track.offsets()[i]
            for c in cfg.characters:          # 行頭で話者以外は閉口(話者切替もここで処理)
                if c != sp:
                    # 聞き手は話者の感情に反応する(棒立ち防止)。プリセット未定義は normal
                    lst_emo = LISTENER_REACTION.get(emo, "normal")
                    if lst_emo not in self._known_emotions:
                        lst_emo = "normal"
                    events.append((t, c, lst_emo, "closed"))
            frames = self._phoneme_frames(la.phonemes, self.cfg.min_frame_sec,
                                          self.cfg.lipsync.phoneme_scale)
            if frames:
                residual = la.duration - sum(d for _, d in frames)
                if residual > 0:  # 音素合計 < wav実長: 最終フレームをホールド
                    frames[-1] = (frames[-1][0], frames[-1][1] + residual)
            remaining = la.duration
            for mouth, dur in frames:
                d = min(dur, remaining)       # 溢れは切り捨て(-t 切断相当)
                if d <= 1e-4:
                    break
                events.append((t, sp, emo, mouth))
                t += d
                remaining -= d
            events.append((t, sp, "normal", "closed"))
        events.sort(key=lambda x: x[0])

        end_time = title_dur + track.total_duration
        # 同一時刻のイベントを一括適用しながらセグメント化
        timeline: list[tuple[str, float]] = [("title", title_dur)]
        idx, n = 0, len(events)
        while idx < n:
            t0 = events[idx][0]
            while idx < n and abs(events[idx][0] - t0) < 1e-6:
                _, c, e, m = events[idx]
                state[c] = (e, m)
                idx += 1
            t1 = events[idx][0] if idx < n else end_time
            if t1 - t0 > 1e-6:
                timeline.append((self._scene_key(state), t1 - t0))
        timeline.append(("idle_end", self.cfg.endcard.duration_sec))

        # 連続重複マージ
        merged: list[tuple[str, float]] = []
        for key, dur in timeline:
            if merged and merged[-1][0] == key:
                merged[-1] = (key, merged[-1][1] + dur)
            else:
                merged.append((key, dur))

        # フレーム境界丸め + 誤差繰り越し(StockSeeker 0bda973 "frame-boundary
        # phoneme rounding" の移植)。各セグメントを独立に丸めると丸め誤差が
        # 蓄積して口パクが徐々に遅れるため、誤差を次セグメントへ繰り越す。
        frame_dur = 1.0 / self.cfg.fps
        quantized: list[tuple[str, float]] = []
        accum_err = 0.0
        for key, dur in merged:
            ideal = dur + accum_err
            n = max(1, round(ideal / frame_dur))
            q = n * frame_dur
            accum_err = ideal - q
            quantized.append((key, q))
        return quantized

    def register_scene(self, key: str) -> dict[str, tuple[str, str]]:
        """scene_key → {char: (emotion, mouth)} を復元して記録。"""
        if key not in self._scenes:
            scene: dict[str, tuple[str, str]] = {}
            for part in key[len("scene_"):].split("__"):
                c, e, m = part.split("-")
                scene[c] = (e, m)
            self._scenes[key] = scene
        return self._scenes[key]

    # ── フレーム合成 ─────────────────────────────────────
    def _load_font(self, size: int):
        from PIL import ImageFont
        for name in ("meiryob.ttc", "meiryo.ttc", "YuGothB.ttc", "YuGothM.ttc", "msgothic.ttc"):
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        return ImageFont.load_default()

    def _compose(self, bg_img, sprites: dict, key: str, scene: dict | None,
                 title: str = ""):
        from PIL import Image, ImageDraw

        W, H = self.cfg.resolution
        img = bg_img.copy()
        d = ImageDraw.Draw(img)

        if key == "title":
            f = self._load_font(72)
            bb = d.textbbox((0, 0), title, font=f)
            tw_ = bb[2] - bb[0]
            d.rounded_rectangle([(W - tw_) // 2 - 40, 140, (W + tw_) // 2 + 40,
                                 140 + (bb[3] - bb[1]) + 60],
                                radius=24, fill=(10, 10, 18, 200), outline="white", width=3)
            d.text(((W - tw_) // 2, 150), title, font=f, fill="white")
            return img

        if key == "idle_end":
            ec = self.cfg.endcard
            f1 = self._load_font(56)
            f2 = self._load_font(28)
            panel_w, panel_h = 1200, 260
            px, py = (W - panel_w) // 2, (H - panel_h) // 2 - 40
            d.rounded_rectangle([px, py, px + panel_w, py + panel_h],
                                radius=28, fill=(10, 10, 18, 220),
                                outline=(255, 255, 255, 255), width=3)
            bb1 = d.textbbox((0, 0), ec.title_text, font=f1)
            d.text(((W - (bb1[2] - bb1[0])) // 2, py + 62), ec.title_text,
                   font=f1, fill="white")
            if ec.sub_text:
                bb2 = d.textbbox((0, 0), ec.sub_text, font=f2)
                d.text(((W - (bb2[2] - bb2[0])) // 2, py + 160), ec.sub_text,
                       font=f2, fill=(180, 190, 200, 255))
            return img

        assert scene is not None
        for char, (emo, mouth) in scene.items():
            cfg_c = self.char_cfg.get(char)
            char_sprites = sprites.get(char, {})
            emo_map = char_sprites.get(emo) or char_sprites.get("normal") or {}
            src = emo_map.get(mouth)
            if not src:
                continue
            av_h = int(H * self.cfg.avatar_height_ratio)
            ck = (char, emo, mouth, av_h)
            if ck not in self._sprite_cache:
                sp = Image.open(src).convert("RGBA")
                w = int(sp.width * (av_h / sp.height))
                sp = sp.resize((w, av_h), Image.BILINEAR)
                if cfg_c.flip:
                    sp = sp.transpose(Image.FLIP_LEFT_RIGHT)
                self._sprite_cache[ck] = sp
            sp = self._sprite_cache[ck]
            w = sp.width
            x = 60 if cfg_c.position == "left" else W - w - 60
            img.paste(sp, (x, H - av_h), sp)
            f = self._load_font(36)
            bb = d.textbbox((0, 0), cfg_c.name, font=f)
            px = x + w // 2 - (bb[2] - bb[0]) // 2
            py = H - av_h - 60
            d.rounded_rectangle([px - 18, py - 8, px + (bb[2] - bb[0]) + 18, py + bb[3] + 12],
                                radius=12, fill=(10, 10, 18, 210),
                                outline=cfg_c.accent_color, width=3)
            d.text((px, py), cfg_c.name, font=f, fill=cfg_c.accent_color)
        return img

    # ── ffmpeg 実行 ──────────────────────────────────────
    @staticmethod
    def _run(args: list[str], cwd: Path):
        r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg失敗: {' '.join(map(str, args[:8]))}...\n{r.stderr[-2000:]}")
        return r

    def run(self, ctx: PipelineContext) -> None:
        if not (ctx.dialogue and ctx.audio):
            raise RuntimeError("Script/TTS ステージが先に実行されていません")
        work = Path(ctx.out_dir) / "work"
        frames_dir = work / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        ffmpeg = find_ffmpeg()

        from PIL import Image
        bg = Image.open(self.root / self.cfg.background).convert("RGBA")
        bg = bg.resize(self.cfg.resolution, Image.LANCZOS)
        try:
            sprites = {k: self.avatar.sprites(k) for k in self.char_cfg.characters}
        except Exception as e:  # noqa: BLE001 — PSD無しでもフォールバックで完走させる
            logger.warning("PSD描画に失敗、フォールバック描画へ: %s", e)
            from yukkuri.engines.avatar import SimpleAvatarRenderer
            fallback = SimpleAvatarRenderer(*self.cfg.resolution)
            sprites = {k: fallback.sprites(k) for k in self.char_cfg.characters}

        timeline = self.build_timeline(ctx.audio)

        # 1) ユニークシーンのフレームを合成
        for key, _ in timeline:
            scene = None if key in ("title", "idle_end") else self.register_scene(key)
            self._compose(bg, sprites, key, scene, ctx.dialogue.title).save(
                frames_dir / f"{key}.png")

        # 2) ffconcat + ASS 字幕
        with open(frames_dir / "frames.ffconcat", "w", encoding="utf-8") as f:
            f.write("ffconcat version 1.0\n")
            for key, dur in timeline:
                f.write(f"file '{key}.png'\nduration {max(dur, 0.04):.3f}\n")
            f.write(f"file '{timeline[-1][0]}.png'\n")

        sub = self.cfg.subtitle
        ass = [
            "[Script Info]", "ScriptType: v4.00+",
            f"PlayResX: {self.cfg.resolution[0]}", f"PlayResY: {self.cfg.resolution[1]}", "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, "
            "Bold, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            (f"Style: Main,{sub.font},{sub.font_size},&H00FFFFFF,&H00000000,&H80000000,1,"
             f"1,{sub.outline},2,2,120,120,{sub.margin_v},0"), "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]
        for la, off in zip(ctx.audio.lines, ctx.audio.offsets()):
            start = self.cfg.title_duration_sec + off
            end = start + la.duration + 0.2
            ass.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Main,,0,0,0,,"
                       f"{la.line.text}")
        subs_path = work / "subs.ass"
        subs_path.write_text("\n".join(ass), encoding="utf-8")

        # 3) マスター音声(タイトル分の先行無音 + セリフ連結 + gap 無音)
        #    動画タイムラインは title_duration_sec の後に第1セリフが来るため、
        #    音声側にも同長の先行無音を挿入しないと音声が先行する
        def _silence(name: str, sec: float) -> Path:
            p = work / name
            self._run([ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i",
                       "anullsrc=r=24000:cl=mono", "-t", f"{sec:.3f}", "-c:a", "pcm_s16le",
                       p.name], work)
            return p

        lead = _silence(f"lead_{self.cfg.title_duration_sec:.2f}.wav",
                        self.cfg.title_duration_sec)
        gap = self.cfg.line_gap_sec
        silence = _silence(f"silence_{gap:.2f}.wav", gap)
        with open(work / "audio_list.txt", "w", encoding="utf-8") as f:
            f.write(f"file '{lead.resolve().as_posix()}'\n")
            for i, la in enumerate(ctx.audio.lines):
                f.write(f"file '{Path(la.wav_path).resolve().as_posix()}'\n")
                if i < len(ctx.audio.lines) - 1:
                    f.write(f"file '{silence.resolve().as_posix()}'\n")
            # エンドカード分の無音(映像だけの終端に音声が切れないように)
            tail = _silence(f"tail_{self.cfg.endcard.duration_sec:.2f}.wav",
                            self.cfg.endcard.duration_sec)
            f.write(f"file '{tail.resolve().as_posix()}'\n")
        master = work / "master.wav"
        self._run([ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0",
                   "-i", "audio_list.txt", "-c:a", "pcm_s16le", master.name], work)

        # 4) 本体エンコード
        encoder = detect_encoder(ffmpeg, self.cfg.encoder.prefer, self.cfg.encoder.fallback)
        duration = sum(d for _, d in timeline)
        args = [ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0",
                "-i", "frames/frames.ffconcat", "-i", master.name]
        if self.cfg.bgm.enabled and (self.root / self.cfg.bgm.path).exists():
            args += ["-i", (self.root / self.cfg.bgm.path).resolve().as_posix()]
            afilter = f"amix=inputs=2:duration=first:weights='1 {self.cfg.bgm.volume}'[amix]"
        else:
            afilter = "[1:a]anull[amix]"
        # StockSeeker video_engine.py と同じ音声再同期: 映像クロックに音声PTSを合わせ、
        # セリフ連結の累積ずれによる「口パクが徐々に遅れる」現象を吸収する
        afilter += ";[amix]aresample=async=1000:min_hard_comp=0.010000:first_pts=0[a]"
        args += ["-filter_complex",
                 f"[0:v]subtitles={subs_path.name}[v];{afilter}",
                 "-map", "[v]", "-map", "[a]", "-t", f"{duration:.3f}",
                 "-c:v", encoder, "-crf", str(self.cfg.encoder.crf),
                 "-preset", "p4", "-r", str(self.cfg.fps),
                 "-c:a", "aac", "-b:a", "192k", "video_raw.mp4"]
        self._run(args, work)

        ctx.render = RenderResult(mp4_path=str(work / "video_raw.mp4"), duration_sec=duration,
                                  frame_count=len(timeline))
        meta = {"title": ctx.dialogue.title, "theme": ctx.theme,
                "duration_sec": round(duration, 2), "lines": len(ctx.dialogue.lines),
                "encoder": encoder, "slug": "video"}
        (work / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
        logger.info("[render] 完了: %.1fs / %d 変化点 / %s", duration, len(timeline), encoder)
