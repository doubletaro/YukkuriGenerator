"""生成済みシーン動画を連結して完成映像を作る。

使い方:
  python concat_story.py output/momotaro --out output/momotaro_final.mp4 --title 桃太郎
- シーンは scene_NN.mp4 を番号順に連結(音声込み、再エンコードで統一)
- 先頭2.5sにタイトルカード、末尾3sにエンドカードを焼き込み(オプション)
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

FFMPEG = "ffmpeg"


def run(args: list[str], cwd: Path | None = None):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=3600)
    if r.returncode != 0:
        raise RuntimeError(" ".join(args[:6]) + "\n" + r.stderr[-1500:])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir", help="scene_NN.mp4 のあるディレクトリ")
    ap.add_argument("--out", default="output/momotaro_final.mp4")
    ap.add_argument("--title", default="桃太郎")
    ap.add_argument("--title-sec", type=float, default=2.5)
    ap.add_argument("--endcard-sec", type=float, default=3.0)
    ap.add_argument("--keep-title-endcard", action="store_true",
                    help="タイトル/エンドカードを付けず連結のみ")
    ap.add_argument("--no-endcard", action="store_true", help="エンドカードを付けない")
    ap.add_argument("--title-style", choices=["modern", "folktale"], default="modern",
                    help="folktale=和紙+縦書き筆文字の日本昔話風")
    args = ap.parse_args()

    d = Path(args.dir).resolve()
    out = Path(args.out).resolve()
    scenes = sorted(p for p in d.glob("scene_*.mp4") if "_old" not in p.name)
    if not scenes:
        raise SystemExit("scene_*.mp4 が見つかりません")
    for s in scenes:
        assert s.stat().st_size > 500_000, f"{s} が小さすぎます"

    # 1) 連結(再エンコードでパラメータ統一)
    lst = d / "concat_list.txt"
    with open(lst, "w", encoding="utf-8") as f:
        for s in scenes:
            f.write(f"file '{s.resolve().as_posix()}'\n")
    body = d / "body.mp4"
    run([FFMPEG, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst.name,
         "-c:v", "libx264", "-crf", "20", "-preset", "medium",
         "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", body.name], d)
    dur = 15.083333 * len(scenes)

    # 2) タイトル/エンドカード
    if args.keep_title_endcard:
        body.replace(out)
        print(f"done: {out} ({dur:.1f}s, {len(scenes)} scenes)")
        return

    from PIL import Image, ImageDraw, ImageFont

    def _font(size, serif=False):
        names = ("Yu-Mincho.ttc", "msmincho.ttc", "Yu-Mincho.ttc") if serif else (
            "meiryob.ttc", "meiryo.ttc", "YuGothB.ttc", "msgothic.ttc")
        if serif:
            names = ("Yu-Mincho.ttc", "msmincho.ttc", "meiryo.ttc")
        for name in names:
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        return ImageFont.load_default()

    def make_folktale_title(name: str, title: str, sub: str | None):
        """日本昔話風: 和紙ベージュ背景 + 縦書き筆文字 + 落款風の赤印。

        タイトルの文字数に応じてフォントサイズを自動縮小し、見切れを防ぐ。
        """
        import random
        rng = random.Random(42)
        W, H = 640, 480
        img = Image.new("RGB", (W, H), (245, 237, 216))
        dd = ImageDraw.Draw(img)
        # 和紙風のざらつき
        for _ in range(2600):
            x, y = rng.randrange(W), rng.randrange(H)
            g = rng.randint(210, 235)
            dd.point((x, y), fill=(g, g - 6, g - 18))
        # 巻物風の枠
        dd.rectangle([14, 14, W - 15, H - 15], outline=(90, 70, 50), width=4)
        dd.rectangle([22, 22, W - 23, H - 23], outline=(150, 125, 95), width=1)

        ink = (43, 37, 35)
        sub_zone_h = (64 + 24) if sub else 30        # サブテキスト領域+余白
        avail_h = H - 60 - sub_zone_h                 # 枠余白+サブ領域
        avail_w = W - 120
        # フォントサイズを自動調整: 高さ・幅の両方に収まるまで下げる
        f_title, heights, widths = _font(24, serif=True), [], []
        size = 24
        for size in range(96, 24, -4):
            f_title = _font(size, serif=True)
            heights, widths = [], []
            for ch in title:
                bb = dd.textbbox((0, 0), ch, font=f_title)
                heights.append(bb[3] - bb[1])
                widths.append(bb[2] - bb[0])
            if sum(heights) + 14 * (len(title) - 1) <= avail_h and max(widths) <= avail_w:
                break
        total_h = sum(heights) + 14 * (len(title) - 1)
        # タイトルは上寄りに配置し、サブテキスト領域(H-88以降)と確実に分離
        y = max(40, (H - sub_zone_h - total_h) // 2)
        for ch, w in zip(title, widths):
            bb = dd.textbbox((0, 0), ch, font=f_title)
            dd.text(((W - w) // 2 - bb[0], y), ch, font=f_title, fill=ink)
            y += (bb[3] - bb[1]) + 14
        # サブテキスト(下、横書き)
        if sub:
            f_sub = _font(22, serif=True)
            bb2 = dd.textbbox((0, 0), sub, font=f_sub)
            dd.text(((W - (bb2[2] - bb2[0])) // 2, H - 64), sub, font=f_sub,
                    fill=(90, 70, 50))
        # 落款風の赤印
        dd.rectangle([W - 96, H - 120, W - 56, H - 80], fill=(178, 34, 34))
        img.save(d / name)

    bg = d / "bg.jpg"
    if args.title_style == "modern":
        src_bg = Path("assets/background.jpg")
        if src_bg.exists():
            run([FFMPEG, "-y", "-v", "error", "-i", str(src_bg), "-vf",
                 "scale=640:480", str(bg)])
        else:
            run([FFMPEG, "-y", "-v", "error", "-f", "lavfi",
                 "-i", "color=c=0x101018:s=640x480", str(bg)])

    # カード画像は PIL で生成(folktale は独自の和紙背景を使うため bg 不要)
    bg_img = Image.open(bg).convert("RGB") if bg.exists() else None

    def make_card(name: str, main: str, sub: str | None):
        img = bg_img.copy()
        dd = ImageDraw.Draw(img)
        W, H = img.size
        f1, f2 = _font(34), _font(20)
        pw, ph = 560, 160
        px, py = (W - pw) // 2, (H - ph) // 2
        dd.rounded_rectangle([px, py, px + pw, py + ph], radius=20,
                             fill=(10, 10, 24, 230), outline="white", width=3)
        bb = dd.textbbox((0, 0), main, font=f1)
        dd.text(((W - (bb[2] - bb[0])) // 2, py + 45), main, font=f1, fill="white")
        if sub:
            bb2 = dd.textbbox((0, 0), sub, font=f2)
            dd.text(((W - (bb2[2] - bb2[0])) // 2, py + 112), sub, font=f2, fill=(180, 190, 200))
        img.save(d / name)

    if args.title_style == "folktale":
        make_folktale_title("title.jpg", args.title, "～昔ばなし～")
    else:
        make_card("title.jpg", args.title, "YukkuriGenerator")
    if not args.no_endcard:
        make_card("endcard.jpg", "ご視聴ありがとうございました", "YukkuriGenerator")

    def card_video(name: str, img: str, sec: float):
        run([FFMPEG, "-y", "-v", "error", "-loop", "1", "-t", f"{sec:.3f}", "-i", img,
             "-f", "lavfi", "-t", f"{sec:.3f}", "-i", "anullsrc=r=48000:cl=stereo",
             "-r", "24", "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "160k", "-shortest", name], d)

    card_video("title.mp4", "title.jpg", args.title_sec)
    if not args.no_endcard:
        card_video("endcard.mp4", "endcard.jpg", args.endcard_sec)

    # 音声: タイトルは無音、エンドカードは無音(BGM素材があれば入れる)
    total = args.title_sec + dur + (0 if args.no_endcard else args.endcard_sec)
    with open(d / "final_list.txt", "w", encoding="utf-8") as f:
        f.write("file 'title.mp4'\n")
        f.write("file 'body.mp4'\n")
        if not args.no_endcard:
            f.write("file 'endcard.mp4'\n")
    run([FFMPEG, "-y", "-v", "error", "-f", "concat", "-safe", "0",
         "-i", "final_list.txt", "-r", "24", "-t", f"{total:.3f}",
         "-c:v", "libx264", "-crf", "20", "-preset", "medium",
         "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", str(out)], d)
    print(f"done: {out} ({total:.1f}s, {len(scenes)} scenes)")


if __name__ == "__main__":
    main()
