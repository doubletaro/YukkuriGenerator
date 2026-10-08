"""設定読み込み(config/*.yaml)。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class CharacterConfig:
    key: str
    name: str
    speaker_id: int
    position: str            # left / right
    flip: bool
    psd: str
    accent_color: str
    prompt: str
    voice_styles: dict[str, int] = field(default_factory=dict)  # emotion → VOICEVOX style id

    def style_for(self, emotion: str) -> int:
        """感情に応じた声スタイル(レシピB)。未定義はノーマル。"""
        return self.voice_styles.get(emotion, self.speaker_id)


@dataclass
class CharactersConfig:
    characters: dict[str, CharacterConfig]
    speaking_order: list[str]
    emotions: list[str]

    def get(self, key: str) -> CharacterConfig:
        return self.characters[key]


@dataclass
class TtsConfig:
    host: str = "127.0.0.1"
    port: int = 50021
    speed_scale: float = 1.1


@dataclass
class LlmConfig:
    base_url: str = "http://192.168.1.21:8888/v1"
    model: str = "GLM-5.3-Flash-EXL3"
    api_key: str = "none"
    temperature: float = 0.8
    target_lines: int = 14
    max_line_chars: int = 90
    timeout_sec: int = 240
    retries: int = 2
    max_tokens: int = 8000   # thinkingモデルなので推論分込みで大きめに


@dataclass
class SubtitleConfig:
    font: str = "Meiryo"
    font_size: int = 44
    margin_v: int = 40
    outline: int = 3


@dataclass
class BgmConfig:
    enabled: bool = True
    path: str = "assets/bgm.mp3"
    volume: float = 0.08


@dataclass
class EncoderConfig:
    prefer: str = "h264_nvenc"
    fallback: str = "libx264"
    crf: int = 20


@dataclass
class LipsyncConfig:
    phoneme_scale: float = 1.0   # 口パク音素durationの微調整係数(遅れる<1/進む>1)


@dataclass
class EndcardConfig:
    duration_sec: float = 3.0
    title_text: str = "ご視聴ありがとうございました"
    sub_text: str = "YukkuriGenerator"


@dataclass
class RenderConfig:
    resolution: tuple[int, int] = (1920, 1080)
    fps: int = 30
    avatar_height_ratio: float = 0.85
    line_gap_sec: float = 0.45
    title_duration_sec: float = 2.5
    min_frame_sec: float = 0.08
    background: str = "assets/background.jpg"
    tts: TtsConfig = field(default_factory=TtsConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)
    subtitle: SubtitleConfig = field(default_factory=SubtitleConfig)
    bgm: BgmConfig = field(default_factory=BgmConfig)
    encoder: EncoderConfig = field(default_factory=EncoderConfig)
    lipsync: LipsyncConfig = field(default_factory=LipsyncConfig)
    endcard: EndcardConfig = field(default_factory=EndcardConfig)


def load_render_config(path: str | os.PathLike | None = None) -> RenderConfig:
    p = Path(path) if path else PROJECT_ROOT / "config" / "render.yaml"
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    tts = TtsConfig(**(raw.get("tts") or {}))
    llm = LlmConfig(**(raw.get("llm") or {}))
    sub = SubtitleConfig(**(raw.get("subtitle") or {}))
    bgm = BgmConfig(**(raw.get("bgm") or {}))
    enc = EncoderConfig(**(raw.get("encoder") or {}))
    return RenderConfig(
        resolution=tuple(raw.get("resolution") or (1920, 1080)),
        fps=int(raw.get("fps", 30)),
        avatar_height_ratio=float(raw.get("avatar_height_ratio", 0.85)),
        line_gap_sec=float(raw.get("line_gap_sec", 0.45)),
        title_duration_sec=float(raw.get("title_duration_sec", 2.5)),
        min_frame_sec=float(raw.get("min_frame_sec", 0.08)),
        background=raw.get("background", "assets/background.jpg"),
        tts=tts, llm=llm, subtitle=sub, bgm=bgm, encoder=enc,
        lipsync=LipsyncConfig(**(raw.get("lipsync") or {})),
        endcard=EndcardConfig(**(raw.get("endcard") or {})),
    )


def load_characters_config(path: str | os.PathLike | None = None) -> CharactersConfig:
    p = Path(path) if path else PROJECT_ROOT / "config" / "characters.yaml"
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    chars = {}
    for key, c in (raw.get("characters") or {}).items():
        chars[key] = CharacterConfig(
            key=key, name=c["name"], speaker_id=int(c["speaker_id"]),
            position=c.get("position", "left"), flip=bool(c.get("flip", False)),
            psd=c.get("psd", ""), accent_color=c.get("accent_color", "#4169E1"),
            prompt=c.get("prompt", ""),
            voice_styles={k: int(v) for k, v in (c.get("voice_styles") or {}).items()},
        )
    return CharactersConfig(
        characters=chars,
        speaking_order=raw.get("speaking_order") or list(chars),
        emotions=raw.get("emotions") or ["normal", "happy", "cry"],
    )
