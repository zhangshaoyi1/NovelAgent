"""Service 层（Phase 3 · 接口预留）

对外暴露：AgentService（进程内自主写作服务接口，供 CLI / 未来 FastAPI 共用）。
"""

from __future__ import annotations

from agent.service.agent_service import AgentService

__all__ = ["AgentService"]
