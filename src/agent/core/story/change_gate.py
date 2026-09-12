"""变更准入门（ChangeGate，长线一致性设计稿第二期·管理者团队核心收口）

定位（2026-09-13 拍板，见设计稿 §7）：
- 裁决者是**写路径的收口**，不是平行真源——真数据仍在各账本 store，
  门只负责"批准 → 应用 → 记日志"或"驳回 → 记日志"。
- **否决权分级**：
  * 一致性矛盾（与账本当前状态/既有档案冲突）→ 可否决，尽量确定性判定；
  * 创作合理性（审美好坏）→ 只能附理由建议（``approved_with_warnings``），
    不得一票否决——红线是失败显性化，不是创作保守化。
- **簿记/叙事分流**：系统自动的确定性记账（出场登记等）不经过本门，
  只有叙事变更（创作者意图的状态改变）走这里。

协议（``propose_change``）：
1. ``deterministic_check(change) -> (ok, reason)``：账本约束校验（代码判定）；
   不过 → 驳回（BY_DETERMINISTIC），必须落日志。
2. ``arbiter(change) -> {"verdict": ..., "reason": ..., "warnings": [...]}``：
   语义/创作类 LLM 裁决（可注入，测试用假裁决）。调用异常 → 显性降级为
   ``approved_with_warnings``（既不静默放行也不阻塞写作）。
3. 批准 → ``apply(change)`` 应用到账本 store；无论批准/驳回 → 追加一条
   ``ChangeRecord``（append-only，驳回必须带理由）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from agent.core.story.change_log import (
    BY_ARBITER,
    BY_DETERMINISTIC,
    V_APPROVED,
    V_APPROVED_WARNINGS,
    V_REJECTED,
    ChangeLogStore,
    ChangeRecord,
)


@dataclass
class ProposedChange:
    """一次叙事变更提案（进入裁决前的载体）。"""

    kind: str
    subject: str
    before: str
    after: str
    reason: str
    chapter: int = 0
    evidence: str = ""


@dataclass
class ChangeVerdict:
    """裁决结果（含应用与否；驳回时 applied=False）。"""

    verdict: str
    decided_by: str
    reason: str
    applied: bool
    warnings: list[str] = field(default_factory=list)
    record_id: str = ""

    @property
    def approved(self) -> bool:
        return self.applied


# LLM 裁决者签名：接收提案，返回裁决 dict
ArbiterFn = Callable[[ProposedChange], dict[str, Any]]
# 确定性校验签名：返回 (是否通过, 理由)
DeterministicCheckFn = Callable[[ProposedChange], "tuple[bool, str]"]
# 应用签名：把变更写入账本 store
ApplyFn = Callable[[ProposedChange], None]


def propose_change(
    project_dir,
    change: ProposedChange,
    *,
    deterministic_check: DeterministicCheckFn | None = None,
    arbiter: ArbiterFn | None = None,
    apply: ApplyFn | None = None,
    log: ChangeLogStore | None = None,
) -> ChangeVerdict:
    """变更准入口：确定性校验 → [LLM 裁决] → 应用 + 落日志 / 驳回 + 落日志。"""
    log = log or ChangeLogStore(project_dir).load()
    warnings: list[str] = []

    # 1) 确定性校验（账本约束）：不过 → 驳回
    if deterministic_check is not None:
        ok, reason = deterministic_check(change)
        if not ok:
            rec = _record(change, BY_DETERMINISTIC, V_REJECTED, reason)
            log.append(rec)
            log.save()
            return ChangeVerdict(V_REJECTED, BY_DETERMINISTIC, reason, applied=False,
                                 record_id=rec.id)

    # 2) 语义/创作裁决（可选）：异常显性降级为附意见批准，不阻塞也不静默
    verdict, decided_by, reason = V_APPROVED, BY_DETERMINISTIC, ""
    if arbiter is not None:
        try:
            out = arbiter(change) or {}
            verdict = str(out.get("verdict") or V_APPROVED)
            if verdict not in (V_APPROVED, V_APPROVED_WARNINGS, V_REJECTED):
                raise ValueError(f"裁决者返回未知 verdict：{verdict!r}")
            decided_by = BY_ARBITER
            reason = str(out.get("reason") or "")
            warnings = [str(w) for w in (out.get("warnings") or [])]
        except Exception as e:  # noqa: BLE001 - 显性降级：裁决失明 ≠ 放行无痕
            from agent.core.infra.degrade import degrade

            degrade(
                "change_gate.arbiter",
                "LLM 裁决调用失败，本次变更降级为附意见批准（留痕可回溯）",
                e,
            )
            verdict = V_APPROVED_WARNINGS
            decided_by = BY_ARBITER
            reason = f"裁决失明降级：{e}"
            warnings = ["本次变更未经正常裁决，建议批间反思复核"]

    # 3) 应用 + 落日志 / 驳回落日志
    if verdict == V_REJECTED:
        rec = _record(change, decided_by, V_REJECTED, reason, warnings)
        log.append(rec)
        log.save()
        return ChangeVerdict(V_REJECTED, decided_by, reason, applied=False,
                             warnings=warnings, record_id=rec.id)

    if apply is not None:
        apply(change)
    rec = _record(change, decided_by, verdict, reason, warnings)
    log.append(rec)
    log.save()
    return ChangeVerdict(verdict, decided_by, reason, applied=True,
                         warnings=warnings, record_id=rec.id)


def _record(change: ProposedChange, decided_by: str, verdict: str, reason: str,
            warnings: list[str] | None = None) -> ChangeRecord:
    return ChangeRecord(
        id="",
        kind=change.kind,
        subject=change.subject,
        before=change.before,
        after=change.after,
        reason=change.reason,
        chapter=change.chapter,
        evidence=change.evidence,
        decided_by=decided_by,
        verdict=verdict,
        arbiter_reason=reason,
        warnings=list(warnings or []),
    )


__all__ = [
    "ProposedChange",
    "ChangeVerdict",
    "propose_change",
    "ArbiterFn",
    "DeterministicCheckFn",
    "ApplyFn",
]
