"""VOICEVOX TTS エンジン。StockSeeker backend/video_generator/voice_engine.py を移植・一般化。

- /audio_query で speedScale 設定 → /synthesis で wav 取得
- accent_phrases から音素(母音+子音+ポーズ)タイミングを抽出し speedScale で補正
- 未起動なら vv-engine を自動起動(StockSeeker backend/services/voicevox_starter.py 相当)
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import time
import unicodedata
from pathlib import Path

import requests

from yukkuri.engines.base import TtsEngine
from yukkuri.models import Phoneme

logger = logging.getLogger(__name__)

# VOICEVOX 読み間違い防止辞書(StockSeeker から厳選移植。追加はここへ)
TTS_DICT = {
    "AI": "エーアイ", "LLM": "エルエルエム", "GPU": "ジーピーユー",
    "CUDA": "クーダ", "API": "エーピーアイ", "DGX": "ディージーエックス",
    "VOICEVOX": "ボイスボックス", "YouTube": "ユーチューブ",
    "ChatGPT": "チャットジーピーティー", "Claude": "クロード",
    "Hermes": "ヘルメス", "DeepSeek": "ディープシーク",
    "GLM": "ジーエルエム", "GitHub": "ギットハブ",
    "USD": "ドル", "JPY": "円", "ETF": "イーティーエフ", "GDP": "ジーディーピー",
    "CPI": "シーピーアイ", "VIX": "ビックス", "S&P500": "エスアンドピーごひゃく",
}

_EXE_CANDIDATES = [
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "VOICEVOX", "vv-engine", "run.exe"),
    r"C:\Program Files\VOICEVOX\vv-engine\run.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "VOICEVOX", "VOICEVOX.exe"),
    r"C:\Program Files\VOICEVOX\VOICEVOX.exe",
]


def normalize_for_tts(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    for eng, jpn in sorted(TTS_DICT.items(), key=lambda x: -len(x[0])):
        text = re.sub(re.escape(eng), jpn, text, flags=re.IGNORECASE)
    return text


class VoiceVoxTts(TtsEngine):
    def __init__(self, host: str = "127.0.0.1", port: int = 50021):
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"

    def health(self) -> bool:
        try:
            return requests.get(f"{self.base_url}/version", timeout=3).status_code == 200
        except Exception:  # noqa: BLE001
            return False

    def ensure_running(self, wait_sec: int = 60) -> bool:
        if self.health():
            return True
        for cand in _EXE_CANDIDATES:
            if cand and os.path.exists(cand):
                logger.info("VOICEVOX を起動します: %s", cand)
                subprocess.Popen([cand, "--host", "127.0.0.1", "--port", str(self.port)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                deadline = time.time() + wait_sec
                while time.time() < deadline:
                    if self.health():
                        return True
                    time.sleep(2)
                break
        return self.health()

    def _query(self, text: str, speaker_id: int) -> dict:
        for param in ("speaker", "style_id"):
            r = requests.post(f"{self.base_url}/audio_query",
                              params={"text": text, param: speaker_id}, timeout=30)
            if r.status_code == 200:
                return r.json()
        r.raise_for_status()
        return {}

    def synthesize(self, text: str, speaker_id: int, out_path: Path,
                   speed_scale: float = 1.1, style_id: int | None = None) -> list[Phoneme]:
        text = normalize_for_tts(text)
        sid = style_id if style_id is not None else speaker_id
        query = self._query(text, sid)
        query["speedScale"] = speed_scale

        phonemes: list[Phoneme] = []
        for phrase in query.get("accent_phrases", []):
            for mora in phrase.get("moras", []):
                if mora.get("consonant"):
                    phonemes.append(Phoneme("closed", mora["consonant_length"] / speed_scale))
                v = mora.get("vowel")
                dur = mora["vowel_length"] / speed_scale
                phonemes.append(Phoneme(v if v in "aiueo" else "closed", dur))
            pm = phrase.get("pause_mora")
            if pm:
                phonemes.append(Phoneme("pause", pm.get("vowel_length", 0.0) / speed_scale))

        # 前後の無音(premPhonemeLength/postPhonemeLength)も音素に含めないと
        # 音素合計 < wav実長 となり、行ごとに累積ずれが発生する
        pre = query.get("prePhonemeLength", 0.0) / speed_scale
        post = query.get("postPhonemeLength", 0.0) / speed_scale
        phonemes = ([Phoneme("pause", pre)] if pre > 0 else []) + phonemes
        if post > 0:
            phonemes.append(Phoneme("pause", post))

        r = requests.post(f"{self.base_url}/synthesis",
                          params={"speaker": sid}, json=query, timeout=120)
        r.raise_for_status()
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(r.content)
        return phonemes
