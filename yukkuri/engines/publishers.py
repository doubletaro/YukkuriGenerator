"""公開/納品エンジン。"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import requests

from yukkuri.engines.base import Publisher


class LocalPublisher(Publisher):
    """out_dir へ確定コピー。最低限の納品先。"""
    name = "local"

    def __init__(self, out_dir: str | Path):
        self.out_dir = Path(out_dir)

    def publish(self, mp4_path: Path, meta: dict) -> str:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        dest = self.out_dir / f"{meta.get('slug', 'video')}.mp4"
        shutil.copy2(mp4_path, dest)
        meta_path = self.out_dir / f"{meta.get('slug', 'video')}.meta.json"
        import json
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(dest)


class TelegramPublisher(Publisher):
    """Telegram Bot API へ mp4 を送信。

    環境変数 YUKKURI_TG_TOKEN / YUKKURI_TG_CHAT_ID が無い場合は何もせず
    "skipped" を返す(cron から呼ぶときのみ有効化)。
    """
    name = "telegram"

    def publish(self, mp4_path: Path, meta: dict) -> str:
        token = os.environ.get("YUKKURI_TG_TOKEN", "")
        chat_id = os.environ.get("YUKKURI_TG_CHAT_ID", "")
        if not token or not chat_id:
            return "skipped: YUKKURI_TG_TOKEN / YUKKURI_TG_CHAT_ID 未設定"
        caption = f"🎬 {meta.get('title', 'ゆっくり動画')}\nテーマ: {meta.get('theme', '')}"
        with open(mp4_path, "rb") as f:
            r = requests.post(
                f"https://api.telegram.org/bot{token}/sendVideo",
                data={"chat_id": chat_id, "caption": caption},
                files={"video": f}, timeout=600,
            )
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram送信失敗: {data}")
        return f"telegram message_id={data['result']['message_id']}"
