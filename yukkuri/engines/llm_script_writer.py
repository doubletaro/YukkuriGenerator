"""GLM(OpenAI互換API)による対話台本生成。StockSeeker ScriptEngine の一般化版。"""
from __future__ import annotations

import json
import logging
import re

import requests

from yukkuri.engines.base import ScriptWriter
from yukkuri.models import Dialogue, ScriptLine

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """あなたは「ゆっくり解説動画」の台本作家です。与えられたキャラクター2名の掛け合いで、テーマを面白おかしく、しかし正確に解説する台本を書いてください。

要件:
- 出力は JSON のみ(前置き・後書き・コードフェンス禁止)
- 形式: {{"title": "動画タイトル", "lines": [{{"speaker": "...", "text": "...", "emotion": "...", "gesture": "..."}}]}}
- speaker は必ず指定されたキャラクター名(キー)のみ使用
- 交互に喋り、最初の1行は解説役(先頭キャラ)の導入、最後の1行は締めの挨拶
- 1行は {max_line_chars} 文字以内。全体で約 {target_lines} 行
- emotion は {emotions} のみ。喜怒哀楽を明確に付け、内容に合わない感情は避ける
  (目安: 驚き=surprised / 思索中=think / ツッコミ=angry / 失敗ギャグ=fail / 照れ=shy / 皮肉=smug)
- gesture は省略可({gestures} から選ぶ)。無い場合は感情に合ったデフォになる
- 資料が与えられたらその内容を優先し、数字や固有名詞を勝手に捏造しない"""


class GlmScriptWriter(ScriptWriter):
    GESTURES = ["point", "raise", "think", "akimbo", "snack", "whisper", "mic"]

    def __init__(self, base_url: str, model: str, api_key: str = "none",
                 temperature: float = 0.8, timeout_sec: int = 240, retries: int = 2,
                 max_tokens: int = 8000):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.timeout_sec = timeout_sec
        self.retries = retries
        self.max_tokens = max_tokens

    def _prompt(self, theme: str, source_text: str, target_minutes: float,
                character_prompts: dict[str, str], emotions: list[str],
                target_lines: int, max_line_chars: int) -> str:
        char_block = "\n".join(f"- {k}: {p}" for k, p in character_prompts.items())
        src = (f"\n\n--- 調査済み資料(優先して使う) ---\n{source_text}\n--- 資料ここまで ---"
               if source_text else "")
        return (
            f"テーマ: {theme}\n"
            f"目標尺: 約{target_minutes}分(1行あたり読み上げることを意識)\n"
            f"キャラクター(キー: 性格):\n{char_block}\n"
            f"使用可能な speaker キー: {', '.join(character_prompts)}\n"
            f"使用可能な emotion: {', '.join(emotions)}\n"
            f"{src}"
        )

    def _parse(self, raw: str, valid_speakers: list[str], emotions: list[str]) -> Dialogue:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            raise ValueError("LLM出力にJSONが含まれません")
        data = json.loads(m.group(0))
        title = str(data.get("title") or "ゆっくり解説")
        lines: list[ScriptLine] = []
        for ln in data.get("lines") or []:
            sp = str(ln.get("speaker") or "")
            tx = str(ln.get("text") or "").strip()
            if sp not in valid_speakers or not tx:
                continue
            emo = ln.get("emotion") if ln.get("emotion") in emotions else "normal"
            lines.append(ScriptLine(speaker=sp, text=tx, emotion=emo))
        if len(lines) < 4:
            raise ValueError(f"有効な行が少なすぎます: {len(lines)}")
        return Dialogue(title=title, lines=lines)

    def write(self, theme: str, source_text: str, target_minutes: float,
              character_prompts: dict[str, str], emotions: list[str],
              target_lines: int, max_line_chars: int) -> Dialogue:
        system = SYSTEM_PROMPT.format(max_line_chars=max_line_chars,
                                      target_lines=target_lines,
                                      emotions=", ".join(emotions),
                                      gestures=", ".join(self.GESTURES))
        user = self._prompt(theme, source_text, target_minutes, character_prompts,
                            emotions, target_lines, max_line_chars)
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key and self.api_key != "none":
            headers["Authorization"] = f"Bearer {self.api_key}"

        last_err: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                r = requests.post(f"{self.base_url}/chat/completions", json=body,
                                  headers=headers, timeout=self.timeout_sec)
                r.raise_for_status()
                content = r.json()["choices"][0]["message"]["content"]
                return self._parse(content, list(character_prompts), emotions)
            except Exception as e:  # noqa: BLE001 — リトライして最後に失敗させる
                last_err = e
                logger.warning("台本生成リトライ %d/%d: %s", attempt + 1, self.retries + 1, e)
        raise RuntimeError(f"台本生成に失敗: {last_err}") from last_err
