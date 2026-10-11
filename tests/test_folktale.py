"""昔ばなしモード共通ロジックのテスト(モックのみ・外部依存に触れない)。"""
import json
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from yukkuri.folktale import (  # noqa: E402
    ABOUT_SCENE_RULES,
    SCENE_DETAIL_RULES,
    STYLE_FREE_SENTENCE,
    STYLE_SENTENCE,
    build_style_sentence,
    build_system_prompt,
    default_out,
    extract_json,
    validate_script,
    verify_sheet,
)


def test_build_style_sentence_variants():
    assert build_style_sentence() == STYLE_SENTENCE
    assert "水彩風" in STYLE_SENTENCE and STYLE_SENTENCE.startswith("1990年代の日本の劇場アニメ風セルアニメ")
    free = build_style_sentence(style_free=True)
    assert free == STYLE_FREE_SENTENCE
    assert "自分で決め" in free
    note = build_style_sentence(style_note="砂漠の金と宝石の豪華絢爛な色調。")
    assert "1990年代の日本の劇場アニメ風セルアニメ" in note  # ベースは不変
    assert "砂漠の金と宝石" in note
    with pytest.raises(ValueError):
        build_style_sentence(style_note="x", style_free=True)  # 併用禁止


def test_build_system_prompt_about_mode():
    sysp = build_system_prompt({"おじいさん": "白髭の老農夫。"}, ["あらすじ1", "あらすじ2"],
                               250, 350, detail="about")
    assert ABOUT_SCENE_RULES in sysp
    assert SCENE_DETAIL_RULES not in sysp
    assert "簡素な描写" in sysp and "250〜350字" in sysp
    det = build_system_prompt({"おじいさん": "白髭の老農夫。"}, ["あ", "b"], 550, 700)
    assert SCENE_DETAIL_RULES in det and "高密度描写" in det


def test_build_system_prompt_style_override():
    sysp = build_system_prompt({"x": "y。"}, ["あ", "い"], style_sentence="スタイルテスト。")
    assert "「スタイルテスト。」を毎回同一文" in sysp


def _scene(i: int, total: int = 600, cont: bool = False) -> dict:
    head = ("【スタイル】1990年代の日本の劇場アニメ風。【キャラ】おじいさん=「白髭の老農夫」。"
            "【シーン】")
    tail = "【音声】女性ナレーション「むかしむかし。」"
    p = head + "雪" * max(0, total - len(head) - len(tail)) + tail
    return {"id": i, "title": "t", "duration": 15, "prompt": p, "continue": cont}


def _script(n: int = 12, plen: int = 600) -> dict:
    return {"scenes": [_scene(i, plen) for i in range(1, n + 1)]}


def test_validate_pass():
    assert validate_script(_script()) == []


def test_validate_length_bounds():
    errs = validate_script(_script(plen=100))
    assert any("prompt長" in e for e in errs)
    # ±15%の猶予内なら合格
    assert validate_script(_script(plen=550)) == []
    assert validate_script(_script(plen=790)) == []


def test_validate_scene_count_and_fields():
    errs = validate_script(_script(n=11))
    assert any("シーン数" in e for e in errs)
    s = _script()["scenes"][0]
    s2 = dict(s, duration=10, prompt=s["prompt"].replace("【音声】", ""),
              id=99, **{"continue": "yes"})
    errs = validate_script({"scenes": [s2] + _script()["scenes"][1:]})
    assert any("duration" in e for e in errs)
    assert any("欠損【音声】" in e for e in errs)
    assert any("continue" in e for e in errs)
    assert any("id" in e for e in errs)
    s4 = dict(s, prompt="本文のみ")
    errs4 = validate_script({"scenes": [s4]})
    assert any("欠損【スタイル】" in e for e in errs4)
    assert any("ナレーション" in e for e in errs4)


def test_extract_json_fences_and_noise():
    raw = "前置きです。\n```json\n" + json.dumps({"scenes": []}) + "\n```"
    assert extract_json(raw) == {"scenes": []}
    assert extract_json('noise {"scenes": [1]} trailing') == {"scenes": [1]}


def test_default_out_uses_dir_name(tmp_path):
    d = tmp_path / "output" / "019_kintaro"
    d.mkdir(parents=True)
    assert default_out(d) == tmp_path / "output" / "019_kintaro_final.mp4"


def _sheet_image(tmp_path, n_views: int, name: str = "sheet.png") -> Path:
    """n_views 体の縦長図形を白背景に描く三面図モック。"""
    im = Image.new("L", (1024, 1024), 255)
    px = im.load()
    for i in range(n_views):
        x0 = 40 + i * (944 // max(n_views, 1))
        for x in range(x0, min(x0 + 200, 1010)):
            for y in range(100, 930):
                px[x, y] = 60
    p = tmp_path / name
    im.save(p)
    return p


def test_verify_sheet_three_views_ok(tmp_path):
    ok, detail = verify_sheet(_sheet_image(tmp_path, 3))
    assert ok, detail


def test_verify_sheet_front_only_fails(tmp_path):
    ok, detail = verify_sheet(_sheet_image(tmp_path, 1))
    assert not ok and "視点塊数" in detail
