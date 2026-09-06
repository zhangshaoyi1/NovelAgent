"""集中自定义异常

所有领域异常统一在此定义，避免散落在各业务模块、并被 CLI 直接捕获而耦合。
此模块为叶子模块（不依赖 agent 包内其他模块），可安全被任意层导入。

下沉说明（2026-08-29）：``LLMError`` 已随 LLM 协议类型一并下沉至
``agent.base.llm``（消除 ``client→core`` 反向依赖）。
下沉说明（2026-09-06）：``FatalProviderError`` / ``is_fatal_provider_error``
同样下沉至 ``agent.base.llm``（同类 client→core 反向依赖），本模块保留
再导出以兼容既有调用点；新代码请直接从 ``agent.base.llm`` 导入。
"""

from __future__ import annotations

from typing import Any

from agent.base.llm import FatalProviderError, is_fatal_provider_error

__all__ = [
    "FrozenFieldError",
    "PreValidationBlocked",
    "FatalProviderError",
    "is_fatal_provider_error",
]


class FrozenFieldError(PermissionError):
    """尝试修改未解冻的冻结字段"""


class PreValidationBlocked(Exception):
    """E3 高严重度冲突，生成被前置门禁中断，需用户仲裁

    args:
        report: 冲突报告对象（ConflictReport）
    """

    def __init__(self, report: Any) -> None:
        self.report = report
        summary = getattr(report, "summary", "") or ""
        super().__init__(f"前置冲突检测拦截生成：{summary}")
