"""基础抽象层

提供所有上层组件依赖的基础抽象：
- Agent 基类（统一接口）
- 消息协议定义
- 结果基类
- 公共类型定义
- 配置基类工具

遵循依赖规则：base 不依赖任何上层（client/core/agents/workflows），所有上层依赖 base。
"""

from __future__ import annotations

from agent.base.agent import Agent
from agent.base.config import BaseConfig
from agent.base.message import Message, Role
from agent.base.result import AgentResult
from agent.base.types import (
    AsyncCallback,
    JsonDict,
    ModelName,
    TokenCount,
)

__all__ = [
    "Agent",
    "BaseConfig",
    "Message",
    "Role",
    "AgentResult",
    "AsyncCallback",
    "JsonDict",
    "ModelName",
    "TokenCount",
]
