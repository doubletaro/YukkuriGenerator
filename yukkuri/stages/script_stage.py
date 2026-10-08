"""台本生成ステージ。"""
from __future__ import annotations

import logging

from yukkuri.config import CharactersConfig, RenderConfig
from yukkuri.engines.base import ScriptWriter
from yukkuri.models import PipelineContext
from yukkuri.stages.base import Stage

logger = logging.getLogger(__name__)


class ScriptStage(Stage):
    name = "script"

    def __init__(self, writer: ScriptWriter, render_cfg: RenderConfig,
                 char_cfg: CharactersConfig):
        self.writer = writer
        self.render_cfg = render_cfg
        self.char_cfg = char_cfg

    def run(self, ctx: PipelineContext) -> None:
        prompts = {k: c.prompt for k, c in self.char_cfg.characters.items()}
        logger.info("[script] 生成中: %s (目標 %s行)", ctx.theme, self.render_cfg.llm.target_lines)
        ctx.dialogue = self.writer.write(
            theme=ctx.theme, source_text=ctx.source_text,
            target_minutes=ctx.target_minutes, character_prompts=prompts,
            emotions=self.char_cfg.emotions,
            target_lines=self.render_cfg.llm.target_lines,
            max_line_chars=self.render_cfg.llm.max_line_chars,
        )
        logger.info("[script] 完了: %s / %d行", ctx.dialogue.title, len(ctx.dialogue.lines))
