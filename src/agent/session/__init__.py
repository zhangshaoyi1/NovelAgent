"""Session 集成：原生 llmagent SessionManager

直接导出 llmagent.SessionManager，调用方 ``from agent.session import SessionManager``。
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

__all__ = [
    "SessionManager",
    "Session",
    "SessionContext",
    "TaskContext",
    "ChatContext",
]