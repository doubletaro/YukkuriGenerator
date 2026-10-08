"""パイプライン本体。Stage の直列実行 + 失敗時の明確なエラー報告。"""
from __future__ import annotations

import logging
import time

from yukkuri.models import PipelineContext
from yukkuri.stages.base import Stage

logger = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, stages: list[Stage], stop_on_error: bool = True):
        self.stages = stages
        self.stop_on_error = stop_on_error

    def run(self, ctx: PipelineContext) -> PipelineContext:
        t0 = time.time()
        for stage in self.stages:
            logger.info("==== stage: %s ====", stage.name)
            try:
                stage.run(ctx)
            except Exception:
                logger.exception("stage '%s' で失敗", stage.name)
                if self.stop_on_error:
                    raise
        logger.info("パイプライン完了: %.1fs", time.time() - t0)
        return ctx
