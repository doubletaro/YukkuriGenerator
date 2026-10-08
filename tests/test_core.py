"""モックベースのユニットテスト。外部依存(VOICEVOX/ffmpeg/LLM/PSD)には触れない。"""
import pytest

from yukkuri.config import load_characters_config, load_render_config
from yukkuri.models import AudioTrack, LineAudio, Phoneme, ScriptLine
from yukkuri.stages.render_stage import RenderStage, _ass_time
from yukkuri.config import RenderConfig


def _track():
    la1 = LineAudio(ScriptLine("shikoku_metan", "こんにちは"), "a.wav",
                    [Phoneme("closed", 0.05), Phoneme("a", 0.12), Phoneme("closed", 0.03),
                     Phoneme("o", 0.2), Phoneme("pause", 0.3)])
    la2 = LineAudio(ScriptLine("zundamon", "なのだ"), "b.wav",
                    [Phoneme("i", 0.3)])
    return AudioTrack(lines=[la1, la2], gap_sec=0.45)


def _stage(min_frame=0.08, gap=0.45, title=2.5) -> RenderStage:
    rs = RenderStage.__new__(RenderStage)
    rs.cfg = RenderConfig(title_duration_sec=title, min_frame_sec=min_frame, line_gap_sec=gap)
    rs.char_cfg = load_characters_config()
    rs._scenes = {}
    rs._sprite_cache = {}
    rs._known_emotions = set(rs.char_cfg.emotions) | {"normal"}
    return rs


def test_audio_track_offsets():
    t = _track()
    assert t.offsets()[0] == 0.0
    assert t.offsets()[1] == pytest.approx(sum(p.duration for p in t.lines[0].phonemes) + 0.45)
    assert t.total_duration == pytest.approx(t.offsets()[1] + 0.3)


def test_ass_time_format():
    assert _ass_time(0) == "0:00:00.00"
    assert _ass_time(3725.5) == "1:02:05.50"


def test_timeline_structure():
    rs = _stage()
    tl = rs.build_timeline(_track())
    assert tl[0] == ("title", 2.5)
    keys = [k for k, _ in tl]
    # 話者・非話者のシーンフレームが存在する
    assert any("shikoku_metan-normal-a" in k for k in keys)
    assert any(k.startswith("scene_") for k in keys)
    assert keys[-1] == "idle_end"
    # 連続重複なし・全フレームに正の長さ
    assert all(keys[i] != keys[i + 1] for i in range(len(keys) - 1))
    assert all(d > 0.039 for _, d in tl)


def test_scene_roundtrip():
    rs = _stage()
    key = rs._scene_key({"shikoku_metan": ("happy", "a"), "zundamon": ("normal", "closed")})
    scene = rs.register_scene(key)
    assert scene == {"shikoku_metan": ("happy", "a"), "zundamon": ("normal", "closed")}


def test_phoneme_frames_merges_short():
    phs = [Phoneme("a", 0.02), Phoneme("a", 0.02), Phoneme("i", 0.3)]
    out = RenderStage._phoneme_frames(phs, 0.08)
    # 短いa(0.02+0.02)は次のiに吸収される
    assert out == [("i", pytest.approx(0.34))]
    assert all(d >= 0.08 for _, d in out)


def test_config_load():
    rc = load_render_config()
    assert rc.resolution == (1920, 1080)
    assert rc.llm.max_tokens == 8000
    cc = load_characters_config()
    assert cc.get("zundamon").speaker_id == 3
    assert cc.get("shikoku_metan").position == "left"
