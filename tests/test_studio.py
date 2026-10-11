"""スタジオの設定検証・説明文生成のテスト(モックのみ)。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from folktale_studio import build_desc, validate_config  # noqa: E402
from yukkuri.folktale import (  # noqa: E402
    DEFAULT_STYLE_BASE,
    NO_DISNEY,
    build_style_sentence,
)


def _cfg(**over):
    base = {"title": "つるの恩返し", "slug": "018_tsuru", "detail": "detailed",
            "outline": ["あ", "い"], "chars_key": "tsuru"}
    base.update(over)
    return base


def test_validate_config_ok():
    assert validate_config(_cfg()) == []


def test_validate_config_errors():
    errs = validate_config({"title": "", "slug": "bad slug!", "outline": [],
                            "detail": "detailed", "style_note": "x", "style_free": True})
    joined = ";".join(errs)
    assert "title" in joined and "slug" in joined and "あらすじ" in joined and "併用" in joined


def test_build_desc_contains_no_and_synopsis():
    cfg = _cfg(publish={"video_no": 18, "youtube_title": "T", "tags": "x",
                        "privacy": "public", "intro": "むかしむかし",
                        "synopsis": ["a", "b", "c", "d"]})
    d = build_desc(cfg)
    assert "No.018" in d and "① a" in d and "④ d" in d and "T" in d
    assert "GLM-5.3-Flash-EXL3" in d


def test_build_desc_video_no_zero_pads():
    d = build_desc(_cfg(publish={"video_no": 5}))
    assert "No.005" in d


def test_validate_config_char_limits():
    assert validate_config(_cfg(min_chars=550, max_chars=700)) == []
    errs = validate_config(_cfg(min_chars=700, max_chars=550))
    assert any("min_chars" in e for e in errs)
    errs = validate_config(_cfg(min_chars=50))
    assert any("min_chars" in e for e in errs)


def test_build_style_sentence_base_editable():
    # デフォルトベースは従来の固定文と一致
    assert build_style_sentence() == DEFAULT_STYLE_BASE + NO_DISNEY
    # ベースを編集するとそれが使われ、句点は自動補完
    custom = build_style_sentence(base="1990年代の日本の劇場アニメ風セルアニメ、墨と金の和風美術、16:9")
    assert custom.startswith("1990年代の日本の劇場アニメ風セルアニメ、墨と金の和風美術、16:9。")
    assert custom.endswith(NO_DISNEY)
    # ベース編集 + 追加指定の併存
    note = build_style_sentence(style_note="砂漠の色調。", base="1990年代の日本の劇場アニメ風セルアニメ、16:9。")
    assert "砂漠の色調。" in note and note.endswith(NO_DISNEY)
