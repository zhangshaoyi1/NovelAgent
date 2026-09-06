"""Service 层（写/评业务门面，R2-B 收敛中）

对外暴露：
- ``AgentService``：进程内装配门面（LLMOps + Session + Catalog + Gateway + Memory），
  承载 autowrite / evaluate / rewrite 等业务方法；
- ``build_write_workflow`` / ``probe_write_lock``：写章唯一业务构造入口（R2-B）——
  CLI ``write`` 命令经此处构造写章工作流（含单写者锁检查），不再直接 import workflows。
"""

from __future__ import annotations

from agent.service.agent_service import (
    AgentService,
    build_write_workflow,
    probe_write_lock,
)

__all__ = ["AgentService", "build_write_workflow", "probe_write_lock"]
