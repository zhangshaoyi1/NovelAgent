"""连续性账本 · 结构化 delta 结算（竞品差距改进计划 P1-5，对标 inkos Reflector）。

问题：让 LLM 直接改写全量状态文件是状态破坏的主要风险源。本模块把"LLM 改状态"
变成可控的数据工程问题——模型只产出**增量 delta**（add/update/resolve/defer 操作
列表），代码层做严格校验后 immutable 应用：

- ``LedgerDelta``：一章的结算增量契约（facts / knowledge / loop_ops / handoff）。
- ``ContinuityLedgerStore.apply_delta``：校验 → 应用 → 原子落盘，返回 commit_id。
  - **拒绝未知字段**：所有 delta 模型 ``extra="forbid"``，LLM 幻觉出的字段直接报错；
  - **幂等重放**：同一 delta 重复应用结果一致（facts 按键覆盖、loop 状态机幂等、
    handoff 按章覆盖）；
  - **失败显式**：校验失败抛 ``LedgerDeltaError``（含原因），绝不静默半应用。

LLM 生产者接线约定（对标 inkos 数值结算）：结算提示词必须包含——
**期初值从账本读取（禁止凭记忆重算）；增量逐笔列出并注明来源（本章哪一段/哪个事件）；
期末 = 期初 + 增量 - 消耗，不得跳步**。本模块只产 delta、不产全量，故"期初"由
``ContinuityLedgerStore`` 在应用前投影提供，提示词侧只需声明"增量必须标注来源"。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from agent.core.continuity.models import (
    ContinuityFact,
    ContinuityHandoff,
    ContinuityKnowledge,
    LoopStatus,
)


class LedgerDeltaError(ValueError):
    """delta 校验/应用失败（显式错误，调用方负责处理而非吞掉）。"""


class LoopOp(BaseModel):
    """单个未闭环剧情线的状态操作（对标 inkos hookOps）。

    - ``advance``：推进（open → progressing），可补充 detail；
    - ``resolve``：闭环（必须给 resolved_in）；
    - ``defer``：暂缓（保持 open，可附 detail 说明为何推迟）；
    - ``abandon``：放弃闭环（必须给 reason，落在 detail）。
    """

    model_config = ConfigDict(extra="forbid")

    op: Literal["advance", "resolve", "defer", "abandon"]
    loop_id: str
    detail: str | None = None
    resolved_in: str | None = None
    source_commit_id: str

    @field_validator("loop_id", "source_commit_id")
    @classmethod
    def _nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("loop_id / source_commit_id 不可为空")
        return v.strip()

    @model_validator(mode="after")
    def _op_requirements(self) -> "LoopOp":
        if self.op == "resolve" and not (self.resolved_in or "").strip():
            raise ValueError(f"resolve 操作必须提供 resolved_in（loop_id={self.loop_id}）")
        if self.op == "abandon" and not (self.detail or "").strip():
            raise ValueError(f"abandon 操作必须提供 detail 说明放弃原因（loop_id={self.loop_id}）")
        if self.op in ("advance", "defer") and not (self.detail or "").strip():
            raise ValueError(f"{self.op} 操作建议提供 detail（loop_id={self.loop_id}）")
        return self


class LedgerDelta(BaseModel):
    """一章的结算增量（LLM 只产 delta，不产全量状态）。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    chapter: int
    facts: list[ContinuityFact] = []       # add/update：按 (domain, subject_id, field) 覆盖
    knowledge: list[ContinuityKnowledge] = []  # update：按 (subject_id, audience, audience_id) 覆盖
    loop_ops: list[LoopOp] = []            # resolve/defer/abandon/advance 操作列表
    handoff: ContinuityHandoff | None = None

    @field_validator("chapter")
    @classmethod
    def _positive_chapter(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("chapter 必须为正整数")
        return v


_STATUS_BY_OP: dict[str, LoopStatus] = {
    "advance": "progressing",
    "resolve": "resolved",
    "defer": "open",
    "abandon": "abandoned",
}


def apply_ledger_delta(ledger, delta: LedgerDelta, *, commit_id: str | None = None) -> str:
    """把 delta 应用到账本对象（**纯内存**，不落盘；落盘由调用方 save）。

    幂等：同一 delta 重复应用结果一致（覆盖语义 + 状态机目标态幂等）。
    校验失败抛 ``LedgerDeltaError``，账本保持原样（不做半应用）。

    Args:
        ledger: ``ContinuityLedger`` 实例（原地修改）。
        delta: 待应用的 ``LedgerDelta``。
        commit_id: 证据链锚；缺省用 ``str(delta.chapter)``。

    Returns:
        commit_id。
    """
    commit_id = commit_id or str(delta.chapter)
    cid = str(commit_id)

    # ---- 预检：所有 loop_ops 必须命中已有 loop（不凭空造/不静默跳过）----
    # 预检先行保证"应用阶段不再失败"，从根上杜绝半应用。
    loop_index = {lo.loop_id: i for i, lo in enumerate(ledger.open_loops)}
    for op in delta.loop_ops:
        if op.loop_id not in loop_index:
            raise LedgerDeltaError(
                f"loop_op 目标不存在：loop_id={op.loop_id}（op={op.op}）。"
                f"如需新增剧情线请在 facts/open_loops 增量中显式给出完整条目。"
            )

    # ---- 预检通过，开始应用（应用阶段不再失败）----
    fact_index = {(f.domain, f.subject_id, f.field): i for i, f in enumerate(ledger.facts)}
    for f in delta.facts:
        # 证据链锚统一收口为本 commit，防止 LLM 伪造他章证据
        f.source_commit_id = cid
        key = (f.domain, f.subject_id, f.field)
        if key in fact_index:
            ledger.facts[fact_index[key]] = f
        else:
            ledger.facts.append(f)
            fact_index[key] = len(ledger.facts) - 1

    k_index = {
        (k.subject_id, k.audience, k.audience_id): i
        for i, k in enumerate(ledger.knowledge)
    }
    for k in delta.knowledge:
        k.source_commit_id = cid
        key = (k.subject_id, k.audience, k.audience_id)
        if key in k_index:
            ledger.knowledge[k_index[key]] = k
        else:
            ledger.knowledge.append(k)
            k_index[key] = len(ledger.knowledge) - 1

    for op in delta.loop_ops:
        lo = ledger.open_loops[loop_index[op.loop_id]]
        new_status = _STATUS_BY_OP[op.op]
        # 终态不回退（幂等重放 + 防误操作）
        if lo.status in ("resolved", "abandoned") and new_status not in ("resolved", "abandoned"):
            continue
        # 不允许从 progressing 退回 open（defer 对已推进线保持 progressing）
        if lo.status == "progressing" and new_status == "open":
            continue
        if _STATUS_ORDER.get(new_status, 0) >= _STATUS_ORDER.get(lo.status, 0):
            lo.status = new_status
        if op.op == "resolve":
            lo.resolved_in = op.resolved_in
        if (op.detail or "").strip():
            note = f"[op:{op.op}@{cid}] {op.detail.strip()}"
            if note not in (lo.detail or ""):  # 幂等重放：同一条操作记录不重复追加
                lo.detail = f"{lo.detail}\n{note}" if lo.detail else note

    if delta.handoff is not None:
        ledger.handoffs = [h for h in ledger.handoffs if h.chapter != delta.chapter]
        ledger.handoffs.append(delta.handoff)
        ledger.handoffs.sort(key=lambda h: h.chapter)

    return cid


# 状态推进序（用于幂等/回退判断）
_STATUS_ORDER: dict[str, int] = {
    "open": 0,
    "progressing": 1,
    "resolved": 2,
    "abandoned": 2,
}

__all__ = [
    "LedgerDelta",
    "LedgerDeltaError",
    "LoopOp",
    "apply_ledger_delta",
]
