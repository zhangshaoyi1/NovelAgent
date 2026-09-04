"""Agent 执行结果基类

统一结果结构，便于上层工作流统一处理。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AgentResult:
    """Agent 执行结果基类

    所有 Agent 输出结果都应该继承此类。
    """

    success: bool
    """执行是否成功"""

    message: str = ""
    """结果描述（可读信息）"""

    error: Optional[Exception] = None
    """如果失败，保存异常"""

    metadata: dict = field(default_factory=dict)
    """附加元数据"""

    @property
    def ok(self) -> bool:
        """快捷判断是否成功"""
        return self.success
