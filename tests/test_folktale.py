"""昔ばなしモード共通ロジックのテスト(モックのみ・外部依存に触れない)。"""
import json
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from yukkuri.folktale import (  # noqa: E402
    default_out,
    extract_json,
    validate_script,
    verify_sheet,
)


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
