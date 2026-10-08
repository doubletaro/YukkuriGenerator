"""PSD 立ち絵レンダラ。StockSeeker backend/video_generator/avatar.py を移植+拡張。

- レシピA(StockSeeker 6感情)をベースに、PSD実レイヤー名で検証した11感情プリセット
- 記号(汗/涙/アヒルちゃん)・枝豆萎え も感情と連動
- PSDが無い/失敗する場合は呼び出し側(RenderStage)が SimpleAvatarRenderer にフォールバック
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from yukkuri.engines.base import AvatarRenderer

logger = logging.getLogger(__name__)

# ── ずんだもん 11感情プリセット(zundamon.2.3.psd 実レイヤー名) ──
# arms_r/arms_l: [親グループ, 腕グループ, レイヤー] のスコープ付きパス
ZUNDAMON_PRESETS = {
    "normal": {
        "eyes": ["*目セット", "*普通白目", "!黒目", "*カメラ目線"],
        "brow": ["*普通眉"], "face": ["*ほっぺ"], "symbols": [],
        "edamame": "*枝豆通常",
        "arms_r": ["*服装1", "!右腕", "*基本"], "arms_l": ["*服装1", "!左腕", "*基本"],
    },
    "happy": {
        "eyes": ["*にっこり"], "brow": ["*上がり眉"], "face": ["*ほっぺ赤め"], "symbols": [],
        "edamame": "*枝豆通常",
        "arms_r": ["*服装1", "!右腕", "*基本"], "arms_l": ["*服装1", "!左腕", "*基本"],
    },
    "excited": {
        "eyes": ["*にっこり2"], "brow": ["*上がり眉"], "face": ["*ほっぺ赤め"], "symbols": [],
        "edamame": "*枝豆通常",
        "arms_r": ["*服装1", "!右腕", "*手を挙げる"], "arms_l": ["*服装1", "!左腕", "*手を挙げる"],
    },
    "surprised": {
        "eyes": ["*目セット", "*見開き白目", "!黒目", "*カメラ目線"],
        "brow": ["*上がり眉"], "face": ["*ほっぺ2"], "symbols": ["汗1"],
        "edamame": "*枝豆通常",
        "arms_r": ["*服装1", "!右腕", "*手を挙げる"], "arms_l": ["*服装1", "!左腕", "*口元"],
    },
    "think": {
        "eyes": ["*目セット", "*普通白目", "!黒目", "*目逸らし"],
        "brow": ["*普通眉"], "face": ["かげり"], "symbols": [],
        "edamame": "*枝豆通常",
        "arms_r": ["*服装1", "!右腕", "*基本"], "arms_l": ["*服装1", "!左腕", "*考える"],
    },
    "angry": {
        "eyes": ["*><"], "brow": ["*怒り眉"], "face": ["*ほっぺ赤め"], "symbols": ["汗2"],
        "edamame": "*枝豆通常",
        "arms_r": ["*服装1", "!右腕", "*腰"], "arms_l": ["*服装1", "!左腕", "*腰"],
    },
    "sad": {
        "eyes": ["*><"], "brow": ["*困り眉1"], "face": ["*青ざめ"], "symbols": ["涙"],
        "edamame": "*枝豆萎え",
        "arms_r": ["*服装1", "!右腕", "*苦しむ"], "arms_l": ["*服装1", "!左腕", "*苦しむ"],
    },
    "shy": {
        "eyes": ["*目セット", "*普通白目", "!黒目", "*目逸らし"],
        "brow": ["*上がり眉"], "face": ["*ほっぺ赤め"], "symbols": ["汗2"],
        "edamame": "*枝豆通常",
        "arms_r": ["*服装1", "!右腕", "*基本"], "arms_l": ["*服装1", "!左腕", "*口元"],
    },
    "smug": {
        "eyes": ["*ジト目", "*目逸らし"],
        "brow": ["*普通眉"], "face": ["*ほっぺ"], "symbols": [],
        "edamame": "*枝豆通常",
        "arms_r": ["*服装1", "!右腕", "*腰"], "arms_l": ["*服装1", "!左腕", "*基本"],
    },
    "fail": {
        "eyes": ["*ぐるぐる"], "brow": ["*困り眉2"], "face": ["*青ざめ"],
        "symbols": ["アヒルちゃん"],
        "edamame": "*枝豆萎え",
        "arms_r": ["*服装1", "!右腕", "*苦しむ"], "arms_l": ["*服装1", "!左腕", "*苦しむ"],
    },
    "whisper": {
        "eyes": ["*目セット", "*普通白目", "!黒目", "*目逸らし"],
        "brow": ["*困り眉1"], "face": ["*ほっぺ"], "symbols": [],
        "edamame": "*枝豆通常",
        "arms_r": ["*服装1", "!右腕", "*口元"], "arms_l": ["*服装1", "!左腕", "*ひそひそ"],
    },
}

ZUNDAMON_MOUTHS = {
    "a": ["*んあー", "*ほあー"], "i": ["*△", "*んへー"], "u": ["*んへー", "*お"],
    "e": ["*はへえ", "*んへー"], "o": ["*お", "*ほー"],
    "closed": ["*んへー", "*△"], "pause": ["*むふ", "*んー"],
}

# ── 四国めたん 11感情プリセット(metan.2.1.psd 実レイヤー名) ──
METAN_PRESETS = {
    "normal": {
        "eyes": ["*目セット", "*普通白目", "!黒目", "*カメラ目線"],
        "brow": ["*太眉ごきげん"], "face": ["*普通2"], "symbols": [],
        "arms_r": ["*白ロリ服", "!右腕", "*指差す"], "arms_l": ["*白ロリ服", "!左腕", "*マイク"],
    },
    "happy": {
        "eyes": ["*目セット", "*普通白目", "!黒目", "*カメラ目線"],
        "brow": ["*太眉ごきげん"], "face": ["*普通2"], "symbols": [],
        "arms_r": ["*白ロリ服", "!右腕", "*普通"], "arms_l": ["*白ロリ服", "!左腕", "*抱える"],
    },
    "excited": {
        "eyes": ["*目セット", "*普通白目", "!黒目", "*カメラ目線"],
        "brow": ["*ごきげん"], "face": ["*赤面"], "symbols": [],
        "arms_r": ["*白ロリ服", "!右腕", "*手をかざす"], "arms_l": ["*白ロリ服", "!左腕", "*抱える"],
    },
    "surprised": {
        "eyes": ["*目セット", "*見開き白目", "!黒目", "*カメラ目線"],
        "brow": ["*ごきげん"], "face": ["*普通2"], "symbols": ["汗"],
        "arms_r": ["*白ロリ服", "!右腕", "*手をかざす"], "arms_l": ["*白ロリ服", "!左腕", "*普通"],
    },
    "think": {
        "eyes": ["*目セット", "*普通白目", "!黒目", "*目そらし"],
        "brow": ["*ややおこ"], "face": ["*普通2"], "symbols": [],
        "arms_r": ["*白ロリ服", "!右腕", "*普通"], "arms_l": ["*白ロリ服", "!左腕", "*口元に指"],
    },
    "angry": {
        "eyes": ["*目セット", "*普通白目", "!黒目", "*カメラ目線"],
        "brow": ["*太眉おこ"], "face": ["*赤面"], "symbols": [],
        "arms_r": ["*白ロリ服", "!右腕", "*指差す"], "arms_l": ["*白ロリ服", "!左腕", "*抱える"],
    },
    "sad": {
        "eyes": ["*><"], "brow": ["*太眉こまり"], "face": ["*青ざめ"], "symbols": ["涙"],
        "arms_r": ["*白ロリ服", "!右腕", "*普通"], "arms_l": ["*白ロリ服", "!左腕", "*抱える"],
    },
    "shy": {
        "eyes": ["*目セット", "*普通白目", "!黒目", "*目そらし2"],
        "brow": ["*太眉ごきげん"], "face": ["*赤面"], "symbols": [],
        "arms_r": ["*白ロリ服", "!右腕", "*普通"], "arms_l": ["*白ロリ服", "!左腕", "*口元に指"],
    },
    "smug": {
        "eyes": ["*目セット", "*普通白目", "!黒目", "*目そらし"],
        "brow": ["*ややおこ"], "face": ["*普通2"], "symbols": [],
        "arms_r": ["*白ロリ服", "!右腕", "*普通"], "arms_l": ["*白ロリ服", "!左腕", "*抱える"],
    },
    "fail": {
        "eyes": ["*ぐるぐる"], "brow": ["*太眉こまり"], "face": ["*青ざめ"], "symbols": ["汗"],
        "arms_r": ["*白ロリ服", "!右腕", "*普通"], "arms_l": ["*白ロリ服", "!左腕", "*抱える"],
    },
    "whisper": {
        "eyes": ["*目閉じ"], "brow": ["*太眉こまり"], "face": ["*普通2"], "symbols": [],
        "arms_r": ["*白ロリ服", "!右腕", "*普通"], "arms_l": ["*白ロリ服", "!左腕", "*ひそひそ"],
    },
}

# レシピC(YMM4標準): めたんの closed は「ほほえみ」系
METAN_MOUTHS = {
    "a": ["*わあー"], "i": ["*いー"], "u": ["*うえー"],
    "e": ["*ゆ"], "o": ["*お"], "closed": ["*ほほえみ", "*△"], "pause": ["*もむー"],
}

PSD_MAPPINGS = {
    "zundamon.2.3.psd": {"presets": ZUNDAMON_PRESETS, "mouths": ZUNDAMON_MOUTHS,
                         "base_show": ["*服装1", "*いつもの服"],
                         "base_hide": ["!右腕", "!左腕", "!目", "!眉", "!顔色", "!枝豆", "*記号など"]},
    "metan.2.1.psd": {"presets": METAN_PRESETS, "mouths": METAN_MOUTHS,
                      "base_show": ["*白ロリ服"],
                      "base_hide": ["!右腕", "!左腕", "!目", "!眉", "!顔色", "*記号など"]},
}


class PsdAvatarRenderer(AvatarRenderer):
    """PSD から emotion × mouth の透明PNGをレンダリングしキャッシュする。"""

    def __init__(self, assets_dir: str | Path, cache_dir: str | Path | None = None):
        self.assets_dir = Path(assets_dir)
        self.cache_dir = Path(cache_dir) if cache_dir else self.assets_dir / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._psd = None
        self._psd_path: Path | None = None

    def _load(self, psd_filename: str):
        if self._psd is not None and self._psd_path == psd_filename:
            return self._psd
        from psd_tools import PSDImage  # 重いので遅延 import
        path = self.assets_dir / psd_filename
        if not path.exists():
            raise FileNotFoundError(f"PSDが見つかりません: {path}")
        self._psd = PSDImage.open(path)
        self._psd_path = Path(psd_filename)
        return self._psd

    # ── PSD レイヤー操作(StockSeeker avatar.py と同じ発想) ──
    @staticmethod
    def _find_layer(node, name: str):
        if node.name == name:
            return node
        if node.is_group():
            for child in node:
                found = PsdAvatarRenderer._find_layer(child, name)
                if found:
                    return found
        return None

    def _show_layer(self, psd, name: str) -> bool:
        layer = self._find_layer(psd, name)
        if not layer:
            logger.warning("PSDレイヤー '%s' が見つかりません", name)
            return False
        layer.visible = True
        parent = layer.parent
        while parent and type(parent).__name__ != "PSDImage":
            parent.visible = True
            parent = parent.parent
        return True

    def _hide_all_in_group(self, psd, group_name: str):
        group = self._find_layer(psd, group_name)
        if group and group.is_group():
            for child in group:
                child.visible = False

    def _show_layer_in_group(self, psd, path: list) -> bool:
        """スコープ付きレイヤー表示。path: [親グループ, グループ, レイヤー] or [グループ, レイヤー]。

        同名レイヤーが複数ある場合(!右腕/*基本 と !左腕/*基本 等)に、
        全PSD検索だと先に見つかったものだけが表示されるため、必ず指定グループ内で検索する。
        """
        if len(path) == 3:
            parent = self._find_layer(psd, path[0])
            search_root = parent if parent else psd
            group = self._find_layer(search_root, path[1])
        elif len(path) == 2:
            group = self._find_layer(psd, path[0])
        else:
            logger.warning("不正なパス形式: %s", path)
            return False
        if not (group and group.is_group()):
            logger.warning("グループ '%s' が見つかりません", path[-2] if len(path) >= 2 else path)
            return False
        for child in group:
            child.visible = False
        layer = self._find_layer(group, path[-1])
        if not layer:
            logger.warning("レイヤー '%s' がグループ '%s' 内に見つかりません", path[-1], group.name)
            return False
        layer.visible = True
        group.visible = True
        parent = group.parent
        while parent and type(parent).__name__ != "PSDImage":
            parent.visible = True
            parent = parent.parent
        return True

    def sprites(self, character_key: str) -> dict[str, dict[str, str]]:
        from yukkuri.config import load_characters_config
        cfg = load_characters_config().get(character_key)
        mapping = PSD_MAPPINGS.get(Path(cfg.psd).name)
        if not mapping:
            raise FileNotFoundError(f"PSDマッピング未定義: {cfg.psd}")
        psd = self._load(cfg.psd)
        mouth_group = self._find_layer(psd, "!口")

        out: dict[str, dict[str, str]] = {}
        for emo, preset in mapping["presets"].items():
            # 顔まわり・記号をリセット
            for g in ("!目", "!眉", "!顔色", "!枝豆", "*記号など"):
                self._hide_all_in_group(psd, g)
            for show in mapping["base_show"]:
                self._show_layer(psd, show)
            if "edamame" in preset:
                self._show_layer(psd, preset["edamame"])
            for group_name in ("eyes", "brow", "face"):
                for name in preset.get(group_name, []):
                    self._show_layer(psd, name)
            for name in preset.get("symbols", []):
                self._show_layer(psd, name)
            self._show_layer_in_group(psd, preset["arms_r"])
            self._show_layer_in_group(psd, preset["arms_l"])

            out[emo] = {}
            for mouth, candidates in mapping["mouths"].items():
                cache = self.cache_dir / f"{character_key}_{emo}_{mouth}.png"
                if not cache.exists():
                    if mouth_group:
                        for child in mouth_group:
                            child.visible = False
                    for cand in candidates:
                        # 口レイヤーは !口 グループ内でスコープ検索(同名レイヤー誤爆防止)
                        layer = (self._find_layer(mouth_group, cand)
                                 if mouth_group else self._find_layer(psd, cand))
                        if layer:
                            layer.visible = True
                            if mouth_group:
                                mouth_group.visible = True
                                parent = mouth_group.parent
                                while parent and type(parent).__name__ != "PSDImage":
                                    parent.visible = True
                                    parent = parent.parent
                            break
                    img = psd.composite()
                    img.save(cache)
                out[emo][mouth] = str(cache)
        return out


class SimpleAvatarRenderer(AvatarRenderer):
    """PSD無し環境のフォールバック: 単色シルエット+名前プレートの PNG を生成。"""

    def __init__(self, w: int, h: int, colors: dict[str, str] | None = None):
        self.w, self.h = w, h
        self.colors = colors or {}
        self._cache: dict[str, dict[str, dict[str, str]]] = {}

    def sprites(self, character_key: str) -> dict[str, dict[str, str]]:
        if character_key in self._cache:
            return self._cache[character_key]
        from PIL import Image, ImageDraw, ImageFont
        from yukkuri.config import load_characters_config
        cfg = load_characters_config().get(character_key)
        color = self.colors.get(character_key, "#556677")
        out: dict[str, dict[str, str]] = {}
        tmp = Path(os.environ.get("TMPDIR", ".")) / "yukkuri_sprites"
        tmp.mkdir(parents=True, exist_ok=True)
        for emo in ("normal", "happy", "cry"):
            out[emo] = {}
            for mouth in ("a", "i", "u", "e", "o", "closed", "pause"):
                p = tmp / f"{character_key}_{emo}_{mouth}.png"
                if not p.exists():
                    img = Image.new("RGBA", (self.w // 3, self.h // 2), (0, 0, 0, 0))
                    d = ImageDraw.Draw(img)
                    d.rounded_rectangle([20, 20, self.w // 3 - 20, self.h // 2 - 20],
                                        radius=40, fill=color + "CC")
                    try:
                        f = ImageFont.truetype("meiryo.ttc", 48)
                    except OSError:
                        f = ImageFont.load_default()
                    d.text((60, 80), cfg.name, font=f, fill="white")
                    d.text((60, 160), {"a": "あ", "i": "い", "u": "う", "e": "え",
                                       "o": "お", "closed": "ー", "pause": "ω"}[mouth],
                           font=f, fill="white")
                    img.save(p)
                out[emo][mouth] = str(p)
        self._cache[character_key] = out
        return out
