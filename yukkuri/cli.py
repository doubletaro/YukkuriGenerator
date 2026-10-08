"""CLI: python -m yukkuri make --theme "..." [--source file] [--out dir]"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from yukkuri.config import PROJECT_ROOT, load_characters_config, load_render_config
from yukkuri.engines.avatar import PsdAvatarRenderer
from yukkuri.engines.llm_script_writer import GlmScriptWriter
from yukkuri.engines.publishers import LocalPublisher, TelegramPublisher
from yukkuri.engines.voicevox_tts import VoiceVoxTts
from yukkuri.models import PipelineContext
from yukkuri.pipeline import Pipeline
from yukkuri.stages.publish_stage import PublishStage
from yukkuri.stages.render_stage import RenderStage
from yukkuri.stages.script_stage import ScriptStage
from yukkuri.stages.tts_stage import TtsStage


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="yukkuri", description="ゆっくり動画自動生成")
    sub = p.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make", help="テーマから動画を生成")
    m.add_argument("--theme", required=True)
    m.add_argument("--source", default="", help="調査済み資料のテキストファイル")
    m.add_argument("--minutes", type=float, default=1.5)
    m.add_argument("--out", default="output")
    m.add_argument("--render-config", default=None)
    m.add_argument("--characters-config", default=None)
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = make_parser().parse_args(argv)

    render_cfg = load_render_config(args.render_config)
    char_cfg = load_characters_config(args.characters_config)
    source = ""
    if args.source:
        sp = Path(args.source)
        if sp.exists():
            source = sp.read_text(encoding="utf-8", errors="replace")
        else:  # ファイルでなければインライン資料テキストとして扱う
            source = args.source

    ctx = PipelineContext(theme=args.theme, source_text=source,
                          target_minutes=args.minutes, out_dir=args.out)

    writer = GlmScriptWriter(
        base_url=render_cfg.llm.base_url, model=render_cfg.llm.model,
        api_key=render_cfg.llm.api_key, temperature=render_cfg.llm.temperature,
        timeout_sec=render_cfg.llm.timeout_sec, retries=render_cfg.llm.retries,
        max_tokens=render_cfg.llm.max_tokens)
    tts = VoiceVoxTts(host=render_cfg.tts.host, port=render_cfg.tts.port)
    avatar = PsdAvatarRenderer(assets_dir=PROJECT_ROOT / "assets")

    publishers = [LocalPublisher(Path(args.out) / "final")]
    if sys.platform == "win32" or True:  # 環境変数が無ければ自動スキップ
        publishers.append(TelegramPublisher())

    stages = [
        ScriptStage(writer, render_cfg, char_cfg),
        TtsStage(tts, render_cfg, char_cfg),
        RenderStage(render_cfg, char_cfg, avatar, PROJECT_ROOT),
        PublishStage(publishers),
    ]
    ctx = Pipeline(stages).run(ctx)

    print("\n===== 完成 =====")
    print(f"タイトル : {ctx.dialogue.title}")
    print(f"尺       : {ctx.render.duration_sec:.1f}s")
    print(f"セリフ数 : {len(ctx.dialogue.lines)}")
    for pub in ctx.published:
        print(f"納品     : {pub}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
