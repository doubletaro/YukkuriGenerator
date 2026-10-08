"""YukkuriGenerator — パイプライン共通データモデル."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ScriptLine:
    speaker: str          # config/characters.yaml のキー
    text: str
    emotion: str = "normal"


@dataclass
class Dialogue:
    title: str
    lines: list[ScriptLine] = field(default_factory=list)


@dataclass
class Phoneme:
    """VOICEVOX の accent_phrases から抽出した音素タイミング。

    phoneme は母音 a/i/u/e/o か、closed(子音)/pause(ポーズ)。
    """
    phoneme: str
    duration: float


@dataclass
class LineAudio:
    line: ScriptLine
    wav_path: str
    phonemes: list[Phoneme] = field(default_factory=list)
    wav_dur: float = 0.0   # wavヘッダの実長(StockSeeker _exact_dur 相当)。0 なら音素合計を使う

    @property
    def duration(self) -> float:
        if self.wav_dur > 0:
            return self.wav_dur
        return sum(p.duration for p in self.phonemes)


@dataclass
class AudioTrack:
    """全セリフの音声と、動画上の開始オフセット(同期の中核)。"""
    lines: list[LineAudio] = field(default_factory=list)
    gap_sec: float = 0.45

    def offsets(self) -> list[float]:
        """各行の音声開始時刻(秒)。行間は gap_sec。"""
        out, cursor = [], 0.0
        for la in self.lines:
            out.append(cursor)
            cursor += la.duration + self.gap_sec
        return out

    @property
    def total_duration(self) -> float:
        if not self.lines:
            return 0.0
        return self.offsets()[-1] + self.lines[-1].duration


@dataclass
class RenderResult:
    mp4_path: str
    duration_sec: float
    frame_count: int


@dataclass
class PipelineContext:
    """ステージ間を受け渡すコンテキスト。機能追加はフィールド(または stages 間 dict)拡張で対応。"""
    theme: str
    source_text: str = ""
    target_minutes: float = 1.5
    out_dir: str = "output"

    dialogue: Dialogue | None = None
    audio: AudioTrack | None = None
    render: RenderResult | None = None
    published: list[str] = field(default_factory=list)
