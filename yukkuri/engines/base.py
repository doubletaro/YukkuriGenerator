"""エンジン基底インターフェース。差し替え可能な外部依存はすべてここ経由。

機能追加例:
  - AQUESTALK ゆっくり声  -> TtsEngine を実装して差し替え
  - YouTube 公開          -> Publisher を実装して stages に追加
  - 別LLM(ローカル/クラウド) -> ScriptWriter を実装して差し替え
"""
from __future__ import annotations

import abc
from pathlib import Path

from yukkuri.models import Dialogue, Phoneme


class ScriptWriter(abc.ABC):
    @abc.abstractmethod
    def write(self, theme: str, source_text: str, target_minutes: float,
              character_prompts: dict[str, str], emotions: list[str],
              target_lines: int, max_line_chars: int) -> Dialogue:
        """テーマと資料から対話台本を生成する。"""


class TtsEngine(abc.ABC):
    @abc.abstractmethod
    def health(self) -> bool: ...

    @abc.abstractmethod
    def synthesize(self, text: str, speaker_id: int, out_path: Path,
                   speed_scale: float = 1.1) -> list[Phoneme]:
        """音声を out_path に書き出し、音素タイミング(秒, speed補正済み)を返す。"""


class AvatarRenderer(abc.ABC):
    """character_key ごとに {emotion: {mouth: png_path}} を返す。

    mouth は a/i/u/e/o/closed/pause。実装不可時は空 dict を返して
    呼び出し側(RenderStage)がフォールバック描画する。
    """

    @abc.abstractmethod
    def sprites(self, character_key: str) -> dict[str, dict[str, str]]: ...


class Publisher(abc.ABC):
    name = "publisher"

    @abc.abstractmethod
    def publish(self, mp4_path: Path, meta: dict) -> str:
        """公開/保存し、確認可能なハンドル(URLやパス)を返す。"""
