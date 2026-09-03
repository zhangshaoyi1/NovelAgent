"""Session 集成：原生 llmagent SessionManager（Phase 2 重构）

已将 AgentSessionManager wrapper 移除，直接导出 llmagent.SessionManager。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from llmagent.kernel.session import (
    Session,
    SessionContext,
    SessionManager,
    TaskContext,
    ChatContext,
)

# 直接导出 llmagent.SessionManager（AgentSessionManager 原为薄包装，已移除）
# 调用方 from agent.session import SessionManager 即可直接使用

__all__ = [
    "SessionManager",
    "Session",
    "SessionContext",
    "TaskContext",
    "ChatContext",
]