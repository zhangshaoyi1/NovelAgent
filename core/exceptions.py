"""集中自定义异常

所有领域异常统一在此定义，避免散落在各业务模块、并被 CLI 直接捕获而耦合。
此模块为叶子模块（不依赖 agent 包内其他模块），可安全被任意层导入。
"""

from __future__ import annotations

from typing import Any


class LLMError(Exception):
    """LLM 调用错误（配置缺失、重试后仍失败、双 Provider 均不可用等）"""


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
