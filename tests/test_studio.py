"""スタジオの設定検証・説明文生成のテスト(モックのみ)。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from folktale_studio import build_desc, validate_config  # noqa: E402


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
