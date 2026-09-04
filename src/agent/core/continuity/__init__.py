"""连续性账本子包（G15 P0-1）

对标 DeepWrite `long-ledger/record.ts`，把长篇一致性从「写后检查」升级为
「写前结构化事实账本 + 信息差 + 未闭环 + 章交接」，物化投影喂写手。

- models：ContinuityFact / Knowledge / OpenLoop / Handoff / Ledger / Proj
- ledger：ContinuityLedgerStore（持久化 + 按章 commit 归档）
- projection：物化投影（project / project_to_text）
- derive：open_loop 状态 / 逾期确定性推导

依赖规则：仅依赖 base（pydantic + validation），不依赖上层 —— 放置于引擎层 core/。
"""

from agent.core.continuity.derive import (
    derive_open_loop_status,
    expected_resolve_chapter,
    is_overdue,
)
from agent.core.continuity.ledger import ContinuityLedgerStore
from agent.core.continuity.models import (
    ContinuityFact,
    ContinuityHandoff,
    ContinuityKnowledge,
    ContinuityLedger,
    ContinuityOpenLoop,
    ContinuityProj,
)
from agent.core.continuity.projection import project, project_to_text

__all__ = [
    "ContinuityFact",
    "ContinuityKnowledge",
    "ContinuityOpenLoop",
    "ContinuityHandoff",
    "ContinuityLedger",
    "ContinuityProj",
    "ContinuityLedgerStore",
    "project",
    "project_to_text",
    "derive_open_loop_status",
    "expected_resolve_chapter",
    "is_overdue",
]