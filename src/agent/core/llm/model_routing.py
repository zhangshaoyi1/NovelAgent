"""动态模型路由（向后兼容适配器）

⚠ 此文件仅用于向后兼容，新代码应直接使用 ``from agent.client import ModelRouter``。

所有实现已迁移到 ``agent/client/`` 目录。
"""

from __future__ import annotations

import warnings

warnings.warn(
    "agent.core.model_routing 已废弃，请使用 agent.client 替代。",
    DeprecationWarning,
    stacklevel=2,
)

from agent.client import ModelRouter, RouteCandidate, RouteDecision  # noqa: F401, E501

__all__ = [
    "ModelRouter",
    "RouteCandidate",
    "RouteDecision",
]