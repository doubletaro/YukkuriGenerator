"""YouTube アップロード — StockSeeker youtube_uploader.py 方式(YoutubeUploader クラス)を使う CLI。

初回: ブラウザで「AI昔話」チャンネルのアカウントを選び承認(token_aifolktale.json に保存)。
以降: token 自動更新でブラウザ不要。

使い方:
  python yt_upload.py output/momotaro_final.mp4 --title "..." --desc-file desc.txt --public
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, r"C:\wk\StockSeeker")
sys.path.insert(0, r"C:\wk\StockSeeker\backend")

from backend.video_generator.youtube_uploader import YouTubeUploader  # noqa: E402

PROJECT = Path(__file__).resolve().parent
SECRETS = r"C:\wk\StockSeeker\backend\video_generator\assets\client_secrets.json"
TOKEN = str(PROJECT / "assets" / "token_aifolktale.json")

CHANNEL_ID = "UCTCodKUZ2XEJ2GkFdY-tXQQ"  # AI昔話


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video", help="mp4 パス")
    ap.add_argument("--title", required=True)
    ap.add_argument("--desc-file", required=True, help="説明文テキストファイル")
    ap.add_argument("--tags", default="桃太郎,昔話,AIアニメ,子供向け,おとぎ話,日本昔ばなし,きょうの昔話")
    ap.add_argument("--thumbnail", default="")
    ap.add_argument("--privacy", choices=["private", "unlisted", "public"], default="public")
    ap.add_argument("--category", default="24")  # 24 = エンタメ(昔話アニメ向け)
    args = ap.parse_args()

    description = Path(args.desc_file).read_text(encoding="utf-8")
    uploader = YouTubeUploader(client_secrets_path=SECRETS, token_path=TOKEN)
    video_id = uploader.upload_video(
        mp4_path=args.video,
        title=args.title,
        description=description,
        thumbnail_path=args.thumbnail or None,
        tags=[t.strip() for t in args.tags.split(",") if t.strip()],
        privacy=args.privacy,
        category_id=args.category,
    )
    if video_id:
        print(f"https://youtu.be/{video_id}")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
