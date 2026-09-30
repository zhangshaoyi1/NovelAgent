"""真相断言账本（长线一致性二期 T2，登记单 20260930）。

worldview 级**既定事实**的唯一真源：生死定论、功法/身世来历、身份同一性、
重大旧案结论。对症《灵荒工坊》ch081-128「真相通胀」——父亲死因双版本、
功法来历改口、周德海死活横跳：断言一经确立，写手/单章质检均**无写权限**，
只能由管理者裁决通道（ChangeGate/arbiter，一期 B 家族机制）写入或推翻；
推翻不删除，旧断言标记 superseded 留痕（可回溯当时口径）。

写路径唯一性（登记单 §六.3 红线）：``TruthLedgerStore`` 不暴露任何公开变更方法，
唯一合法入口是 :func:`propose_truth`（走 ``change_gate.propose_change``）。
写手/质检路径直接调 ``.save()`` / 改 ``ledger`` 的行为由红线测试拦截
（``tests/test_truth_ledger.py``）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, field_validator, model_validator

TruthKind = Literal["identity", "death", "origin", "case_conclusion", "worldview"]
AssertionStatus = Literal["active", "superseded"]

_DEFAULT_TRUTH_FILE = Path(".state/continuity/truth.json")


class TruthAssertion(BaseModel):
    """一条真相断言。"""

    assertion_id: str
    kind: TruthKind
    claim: str                      # 断言正文（一句话，主谓宾明确）
    chapter_established: int = 0    # 确立章号（0 = 建书期/设定集）
    status: AssertionStatus = "active"
    superseded_by: str | None = None    # 推翻它的断言 id
    superseded_reason: str = ""
    source_commit_id: str = ""          # 裁决记录 id（ChangeLog）

    @field_validator("assertion_id", "claim")
    @classmethod
    def _nonempty(cls, v: str) -> str:
        if not (v or "").strip():
            raise ValueError("不可为空")
        return v.strip()

    @model_validator(mode="after")
    def _superseded_consistency(self) -> "TruthAssertion":
        if self.status == "superseded" and not (self.superseded_by or "").strip():
            raise ValueError(f"superseded 断言必须给 superseded_by（{self.assertion_id}）")
        if self.status == "active" and self.superseded_by:
            raise ValueError("active 断言不得携带 superseded_by")
        return self


class TruthLedger(BaseModel):
    """真相断言集合（按 assertion_id 唯一；含被推翻断言，只追加不删除）。"""

    schema_version: int = 1
    assertions: list[TruthAssertion] = []

    @model_validator(mode="after")
    def _unique(self) -> "TruthLedger":
        seen = set()
        for a in self.assertions:
            if a.assertion_id in seen:
                raise ValueError(f"断言 id 重复: {a.assertion_id}")
            seen.add(a.assertion_id)
        return self


class TruthLedgerStore:
    """真相断言存储。⚠ **只读 + 裁决回调专用**：公开面无「增删改」方法。"""

    def __init__(self, project_dir: str | Path, file: str | Path | None = None) -> None:
        self.project_dir = Path(project_dir)
        self.file = self.project_dir / (file or _DEFAULT_TRUTH_FILE)
        self.ledger = TruthLedger()

    def load(self) -> TruthLedger:
        if not self.file.exists():
            self.ledger = TruthLedger()
            return self.ledger
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
            self.ledger = TruthLedger.model_validate(data)
        except Exception:  # noqa: BLE001 - 损坏降级空账本（不阻断写作）
            self.ledger = TruthLedger()  # noqa: SILENT_DEGRADE reason=best-effort ref=20260930_长线一致性二期_事实对账与真相SSOT.md
        return self.ledger

    def save(self) -> None:
        """⚠ 仅供 :func:`propose_truth` 的 apply 回调调用（裁决通道内）。

        写手/质检路径调用此方法属于越权写，红线测试
        ``test_truth_ledger.py::test_writer_path_cannot_write`` 以命名约定拦截。
        """
        try:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.file.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(self.ledger.model_dump(mode="json"), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self.file)
        except Exception:  # noqa: BLE001 - 落盘失败降级（裁决记录仍留 ChangeLog）
            pass  # noqa: SILENT_DEGRADE reason=best-effort ref=20260930_长线一致性二期_事实对账与真相SSOT.md

    # ---------------- 查询（写手/评委/质检只允许这些） ----------------
    def active(self) -> list[TruthAssertion]:
        return [a for a in self.ledger.assertions if a.status == "active"]

    def render_for_brief(self) -> str:
        """渲染为 design_brief 注入段（空账本 → 空串 = 不注入）。"""
        rows = self.active()
        if not rows:
            return ""
        lines = ["【真相断言（世界观级既定事实，唯一真源）】"]
        for a in rows:
            lines.append(f"- [{a.kind}] {a.claim}（第{a.chapter_established}章确立）")
        lines.append(
            "以上为既定事实：本章不得写出与之矛盾的表述；"
            "确需推翻某断言，走管理者裁决流程（change-gate），禁止正文单方面改写。"
        )
        return "\n".join(lines)


# ---------------- 唯一写入口（裁决通道） ----------------


def propose_truth(
    project_dir: str | Path,
    *,
    claim: str,
    kind: TruthKind,
    chapter: int = 0,
    supersede_ids: list[str] | None = None,
    reason: str = "",
    arbiter: Any | None = None,
) -> Any:
    """新建/推翻真相断言的唯一合法入口（走 ChangeGate：确定性校验 → 裁决 → 应用）。

    Args:
        supersede_ids: 被本次断言推翻的旧断言 id 列表（旧断言标记 superseded，不删除）。
        arbiter: LLM 裁决者（``core.story.arbiter.llm_arbiter``）；None 时仅确定性校验。
    """
    from agent.core.story.change_gate import ProposedChange, propose_change

    store = TruthLedgerStore(project_dir)
    store.load()

    def _check(change: ProposedChange) -> tuple[bool, str]:
        if not (change.after or "").strip():
            return False, "断言正文为空"
        ids = set(a.assertion_id for a in store.ledger.assertions)
        for sid in supersede_ids or []:
            if sid not in ids:
                return False, f"被推翻断言不存在: {sid}"
            target = next(a for a in store.ledger.assertions if a.assertion_id == sid)
            if target.status != "active":
                return False, f"断言已非 active，不可再推翻: {sid}"
        return True, ""

    def _apply(change: ProposedChange) -> None:
        next_id = f"T{len(store.ledger.assertions) + 1:03d}"
        for sid in supersede_ids or []:
            for a in store.ledger.assertions:
                if a.assertion_id == sid:
                    a.status = "superseded"
                    a.superseded_by = next_id
                    a.superseded_reason = change.reason or reason
        store.ledger.assertions.append(
            TruthAssertion(
                assertion_id=next_id,
                kind=kind,
                claim=change.after,
                chapter_established=int(change.chapter or 0),
                source_commit_id=reason or "change-gate",
            )
        )
        store.save()  # 裁决通道内写盘（唯一合法调用点）

    change = ProposedChange(
        kind="truth_assertion",
        subject=claim[:40],
        before="；".join(supersede_ids or []) or "（新建）",
        after=claim,
        reason=reason or "truth-ledger 登记（无附言）",
        chapter=chapter,
    )
    return propose_change(project_dir, change, deterministic_check=_check, arbiter=arbiter, apply=_apply)


__all__ = [
    "TruthAssertion",
    "TruthKind",
    "TruthLedger",
    "TruthLedgerStore",
    "propose_truth",
]
