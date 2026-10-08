"""公開/納品ステージ。複数 Publisher を順に実行し、結果を ctx.published に記録。"""
from __future__ import annotations

import logging

from yukkuri.engines.base import Publisher
from yukkuri.models import PipelineContext
from yukkuri.stages.base import Stage

logger = logging.getLogger(__name__)


class PublishStage(Stage):
    name = "publish"

    def __init__(self, publishers: list[Publisher]):
        self.publishers = publishers

    def run(self, ctx: PipelineContext) -> None:
        if not (ctx.render and ctx.dialogue):
            raise RuntimeError("Render ステージが先に実行されていません")
        meta = {"title": ctx.dialogue.title, "theme": ctx.theme, "slug": "video",
                "duration_sec": ctx.render.duration_sec}
        for p in self.publishers:
            try:
                handle = p.publish(ctx.render.mp4_path, meta)
                ctx.published.append(f"{p.name}: {handle}")
                logger.info("[publish] %s -> %s", p.name, handle)
            except Exception as e:  # noqa: BLE001 — 一つの公開先が失敗しても他は続ける
                logger.error("[publish] %s 失敗: %s", p.name, e)
                ctx.published.append(f"{p.name}: FAILED ({e})")
