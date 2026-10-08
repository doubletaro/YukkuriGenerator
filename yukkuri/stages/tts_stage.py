"""音声合成ステージ。各行 wav + 音素タイムラインを生成し AudioTrack を構築。"""
from __future__ import annotations

import logging
from pathlib import Path

from yukkuri.config import CharactersConfig, RenderConfig
from yukkuri.engines.base import TtsEngine
from yukkuri.models import AudioTrack, LineAudio, PipelineContext
from yukkuri.stages.base import Stage

logger = logging.getLogger(__name__)


class TtsStage(Stage):
    name = "tts"

    def __init__(self, tts: TtsEngine, render_cfg: RenderConfig, char_cfg: CharactersConfig):
        self.tts = tts
        self.render_cfg = render_cfg
        self.char_cfg = char_cfg

    def run(self, ctx: PipelineContext) -> None:
        if not ctx.dialogue:
            raise RuntimeError("ScriptStage が先に実行されていません")
        if not self.tts.health() and not self.tts.ensure_running():
            raise RuntimeError("VOICEVOX が起動できません。手動起動を確認してください。")

        workdir = Path(ctx.out_dir) / "work" / "audio"
        track = AudioTrack(gap_sec=self.render_cfg.line_gap_sec)
        for i, line in enumerate(ctx.dialogue.lines):
            sp = self.char_cfg.get(line.speaker)
            wav = workdir / f"line_{i:03d}_{line.speaker}.wav"
            style_id = sp.style_for(line.emotion)
            phonemes = self.tts.synthesize(line.text, sp.speaker_id, wav,
                                           self.render_cfg.tts.speed_scale,
                                           style_id=style_id)
            # StockSeeker 方式: 音素durationは定数扱い(speedScale除算のみ)で、
            # 行の長さは wavヘッダの実長(_exact_dur)を基準にする。
            # 口パクフレームは音素どおりに敷き詰め、余りは最終フレームホールド、
            # 溢れは -t 切断と同等に切り捨てる(RenderStage.build_timeline)。
            import wave
            with wave.open(str(wav)) as w:
                wav_dur = w.getnframes() / w.getframerate()
            track.lines.append(LineAudio(line=line, wav_path=str(wav),
                                         phonemes=phonemes, wav_dur=wav_dur))
            logger.info("[tts] %2d/%2d %-14s %.1fs", i + 1, len(ctx.dialogue.lines),
                        sp.name, wav_dur)
        ctx.audio = track
        logger.info("[tts] 総尺 %.1fs", track.total_duration)
