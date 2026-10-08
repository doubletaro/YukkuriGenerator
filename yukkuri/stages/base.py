"""パイプラインステージ群。各ステージは PipelineContext を受け取り一段階を実行する。

機能追加は Stage を継承したクラスを作り Pipeline に差し込むだけでよい。
"""
from __future__ import annotations

import abc

from yukkuri.models import PipelineContext


class Stage(abc.ABC):
    name = "stage"

    @abc.abstractmethod
    def run(self, ctx: PipelineContext) -> None: ...
