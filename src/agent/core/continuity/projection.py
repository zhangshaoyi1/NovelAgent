"""连续性账本 · 物化投影（G15 P0-1）

写手每章前收到的是这份**有界、去重**的投影，而非全文历史：

- facts：当前最新事实视图（覆盖后只剩最终态，天然去重、有界）；
- knowledge：当前信息差视图；
- open_loops：所有仍开放的剧情线；
- latest_handoff：最近一章交接（携带 must_carry / 约束）。

本轮不纳入检索/排序（与 RAG 解耦），仅透出账本当前视图。
"""

from __future__ import annotations

from agent.core.continuity.ledger import ContinuityLedgerStore
from agent.core.continuity.models import ContinuityProj


def project(
    store: ContinuityLedgerStore | None,
) -> ContinuityProj:
    """从账本组装有界物化投影。

    Args:
        store: 账本存储；None 或空账本 → 返回空投影（调用方降级为现状输入，不阻断）。
    """
    ledger = store.ledger if store is not None else None
    if ledger is None:
        return ContinuityProj(facts=[], knowledge=[], open_loops=[], latest_handoff=None)
    return ContinuityProj(
        facts=ledger.facts,
        knowledge=ledger.knowledge,
        open_loops=ledger.open_loops,
        latest_handoff=ledger.latest_handoff(),
    )


def project_to_text(proj: ContinuityProj, max_facts: int = 40) -> str:
    """生成写手可读的投影文本（有界）：用于注入写章上下文。

    限定 facts 条数上限（超过取**最近** N 条），保证注入体量可控。
    2026-09-26：原实现取**前** N 条——账本按章追加，前 40 条永远是最老的世界
    基础事实（早已沉淀进 world.md/角色档案），而**最近几章确立的**事实（才是
    写下一章最需要对照的）全被截掉。改为取末尾 N 条并保持旧→新顺序。
    """
    lines: list[str] = []
    lines.append("【连续性账本投影】")
    if proj.latest_handoff is not None:
        h = proj.latest_handoff
        lines.append(f"- 上一章({h.chapter})交接：{h.summary or '无'}")
        if h.must_carry:
            lines.append(f"  · 必带：{'；'.join(h.must_carry)}")
        if h.next_chapter_constraints:
            lines.append(f"  · 本章约束：{'；'.join(h.next_chapter_constraints)}")
    if proj.facts:
        recent = proj.facts[-max_facts:]
        lines.append(f"- 事实（共 {len(proj.facts)} 条，显示最近 {len(recent)} 条）:")
        for f in recent:
            lines.append(f"  · {f.domain}/{f.subject_id}.{f.field} = {f.value}")
    if proj.knowledge:
        recent_k = proj.knowledge[-max_facts:]
        lines.append(f"- 信息差（共 {len(proj.knowledge)} 条）:")
        for k in recent_k:
            lines.append(
                f"  · {k.subject_id} 被 {k.audience}:{k.audience_id} 已知度 = {k.level}"
            )
    if proj.open_loops:
        recent_loops = proj.open_loops[-max_facts:]
        lines.append(f"- 未闭环（{len(proj.open_loops)} 条）:")
        for lo in recent_loops:
            lines.append(f"  · [{lo.status}] {lo.detail}")
    if len(lines) == 1:
        lines.append("- （空账本）")
    return "\n".join(lines)


__all__ = ["project", "project_to_text"]