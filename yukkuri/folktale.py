"""昔ばなしモード共通ロジック(品質の均一化)。

- 固定【スタイル】文・詳細描写ルールを一元化し、作品ごとにブレないようにする
- 台本の自動検証(validate_script)
- キャラシート三面図の自動検証(verify_sheet)
- 連結時の安全なデフォルト出力名(default_out)

ネットワーク access は含まない(呼び出し側 CLI が担う)。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# 毎回同一文で固定するスタイル宣言(これを変えると作品間で画風がブレる)
STYLE_SENTENCE = ("1990年代の日本の劇場アニメ風セルアニメ、水彩風の美術背景、"
                  "温かみのある色調、16:9。ディズニーを含む欧米アニメのキャラクターデザインは使用しない。")

REQUIRED_SECTIONS = ["【スタイル】", "【キャラ】", "【シーン】", "【音声】"]

# 15秒で完結する1イベントの必須描写(5点)。昔ばなしモードの品質基準。
SCENE_DETAIL_RULES = """- 【シーン】: 15秒で完結する1イベントを、**以下すべて含めて具体的に描く**:
  1) キャラの感情と顔の変化(例: 「痛みに羽を震わせ」「瞳を潤ませて」「戸惑いを隠せず」)
  2) 具体的なカメラワーク(開始画→被写体→終了画の3点。例: 「雪の結晶のクローズアップから始まり、引いて山道の二人の全身、暮れ空の嶺で終わる」)
  3) 背景の小物と光(例: 「囲炉裏の橙色の火」「雪洞の白い透光」「機織り小屋の藍色の影」)
  4) 手や体の動きの詳細(「震える指で」「そっと両手で包み」「緯糺を打つ細かい手つき」)
  5) 季節・天候・時間帯の空気感(雪の質、風、光の角度、呼吸の白さ)と環境音の層(雪の軋み、遠い風、機織りの音)"""


def build_system_prompt(chars: dict[str, str], outline: list[str],
                        min_chars: int = 550, max_chars: int = 700) -> str:
    """キャラ固定文+12シーン構成から system プロンプトを組み立てる。

    chars: {キャラ名: 固定描写文}、outline: 各シーンの1行あらすじ。
    """
    char_block = "\n".join(f"{n}=「{d}」" for n, d in chars.items())
    outline_block = "\n".join(f"{i}: {line}" for i, line in enumerate(outline, 1))
    return f"""あなたはAI動画生成モデル用のシナリオライターです。MiniMax H3(15秒以内の動画+ネイティブ音声生成)用のプロンプトを書きます。

出力はJSONのみ。前置き・後書き・コードフェンス禁止。形式: {{"scenes":[{{"id":N,"title":"...","duration":15,"prompt":"...","continue":true|false}}]}}

promptの書き方(各{min_chars}〜{max_chars}字の高密度描写):
- 冒頭【スタイル】: 「{STYLE_SENTENCE}」を毎回同一文
- 【キャラ】: 登場者の外見を毎回同一文で固定。以下の記述をそのまま使う(全員登場時は複数並記):
{char_block}
{SCENE_DETAIL_RULES}
- **continue:true のシーンは、前シーンの最後の1秒の構図を次シーンの開始と一致させる**
- 【音声】: 女性ナレーション(物語語り口1〜2文)を必ず入れる。キャラのセリフは最大1文。効果音・BGMの指定も書く
- 字幕・テロップ禁止。暴力的描写は抑える。

{len(outline)}シーン構成:
{outline_block}
"""


def build_user_prompt(n_scenes: int, min_chars: int = 550, max_chars: int = 700) -> str:
    return (f"上記の{n_scenes}シーン構成どおりに書いてください。continue フラグも指示どおりに"
            f"(場面転換・時間経過はfalse、直後の同一時点のみtrue)。"
            f"各promptは{min_chars}〜{max_chars}字の高密度描写にすること。\n")


def extract_json(raw: str) -> dict:
    """LLM応答からJSONを取り出す(コードフェンス・前置き耐性)。"""
    s = raw.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", s, re.S)
    if m:
        s = m.group(1).strip()
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end > start:
        s = s[start:end + 1]
    return json.loads(s)


def validate_script(data, n_scenes: int = 12, min_chars: int = 550,
                    max_chars: int = 700, duration: int = 15) -> list[str]:
    """台本JSONを検証し、エラー文のリストを返す(空なら合格)。"""
    errs: list[str] = []
    scenes = data.get("scenes") if isinstance(data, dict) else data
    if not isinstance(scenes, list):
        return ["scenes がリストではない"]
    # 字数は±15%の猶予を設ける(境界ギリギリで再生成コストを払わない)
    lo, hi = int(min_chars * 0.85), int(max_chars * 1.15)
    for i, sc in enumerate(scenes, 1):
        tag = f"scene{i:02d}"
        p = sc.get("prompt", "") if isinstance(sc, dict) else ""
        if not isinstance(sc, dict):
            errs.append(f"{tag} オブジェクトでない")
            continue
        if sc.get("duration") != duration:
            errs.append(f"{tag} duration={sc.get('duration')}(期待{duration})")
        if not lo <= len(p) <= hi:
            errs.append(f"{tag} prompt長={len(p)}(許容{lo}〜{hi})")
        for k in REQUIRED_SECTIONS:
            if k not in p:
                errs.append(f"{tag} 欠損{k}")
        if "ナレーション" not in p:
            errs.append(f"{tag} ナレーションなし")
        if not isinstance(sc.get("continue"), bool):
            errs.append(f"{tag} continue が真偽値でない")
        if sc.get("id") != i:
            errs.append(f"{tag} id={sc.get('id')}(期待{i})")
    if n_scenes and len(scenes) != n_scenes:
        errs.append(f"シーン数={len(scenes)}(期待{n_scenes})")
    return errs


def verify_sheet(path: str | Path) -> tuple[bool, str]:
    """キャラシート三面図の機械検証(白背景・3視点・分離・描画あり)。

    vision 検証がタイムアウトしがちなためのピクセル代替。true=合格。
    """
    from PIL import Image

    im = Image.open(path).convert("L")
    w, h = im.size
    px: Any = im.load()
    corners: list[int] = [int(px[x, y]) for x in (5, w - 6) for y in (5, h - 6)]
    bg = min(corners)
    thr = min(235, bg - 15)
    reasons: list[str] = []
    if bg < 200:
        reasons.append(f"背景が白でない(bg輝度={bg})")
    col_ink = [any(int(px[x, y]) < thr for y in range(0, h, 4)) for x in range(w)]
    figs, start = [], None
    for x in range(w):
        if col_ink[x] and start is None:
            start = x
        elif not col_ink[x] and start is not None:
            figs.append((start, x))
            start = None
    if start is not None:
        figs.append((start, w))
    figs = [f for f in figs if f[1] - f[0] > 15]
    if len(figs) != 3:
        reasons.append(f"視点塊数={len(figs)}(3必需)")
    else:
        gaps = [figs[i + 1][0] - figs[i][1] for i in range(2)]
        if min(gaps) < 15:
            reasons.append(f"視点の分離不足 gap={gaps}")
    for i in range(3):
        x0, x1 = w * i // 3, w * (i + 1) // 3
        ink = sum(1 for y in range(0, h, 3) for x in range(x0, x1, 3) if px[x, y] < thr)
        rate = ink / (((x1 - x0) // 3) * (h // 3))
        if not 0.01 <= rate <= 0.60:
            reasons.append(f"帯{i + 1} ink率={rate:.2f}")
    return (not reasons, "; ".join(reasons) or "OK")


def default_out(scene_dir: str | Path) -> Path:
    """連結デフォルト出力名。inputディレクトリと同じ親に <名前>_final.mp4。"""
    d = Path(scene_dir).resolve()
    return d.parent / f"{d.name}_final.mp4"
