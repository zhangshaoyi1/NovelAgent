"""FailureHandler 适配器：直接使用 llmagent FailureHandler（Phase 3 重构）

已移除 FailureHandlerAdapter 和 DefaultFailureHandler 包装。
业务代码直接使用 ``llmagent.kernel.failure.FailureHandler``。
"""

from __future__ import annotations

from llmagent.kernel.failure import (
    FailureAction,
    FailureContext,
    FailureHandler,
    FailurePolicy,
    PolicyResolver,
)
from llmagent.kernel.task import TaskRun, TaskStatus


__all__ = [
    "FailureHandler",
    "FailureAction",
    "FailureContext",
    "FailurePolicy",
    "PolicyResolver",
]