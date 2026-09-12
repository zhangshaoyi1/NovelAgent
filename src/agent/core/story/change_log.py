"""变更元数据底座（change log，长线一致性设计稿第二期·管理者团队数据基座）

设计原则（2026-09-12/13 拍板，见设计稿 §7）：
- **唯一真源在账本 store**，变更元数据只记录"谁在何时把什么从 A 改成 B、
  为什么、谁批的"——日志是事件流，store 是状态，两者不重复持有真数据。
- **追加写（append-only）**：驳回也留痕。"为什么第 200 章性格变了"和
  "为什么某个方案被否了"都必须可回溯。
- 所有叙事变更（创作者意图：性格/关系/伏笔改道/设定新增）经
  ``change_gate.propose_change`` 准入后落一条记录；簿记变更（系统自动
  维护：出场登记、账龄、指纹增量）**不经过本模块**——它们是确定性记账，
  不是叙事决定（设计稿 §7"簿记/叙事分流"）。

纯文件读写，无 LLM；所有失败显性。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


class ChangeLogError(Exception):
    """变更日志操作违规（文件损坏 / 未知 verdict 等）。"""


# 裁决结论
V_APPROVED = "approved"                        # 批准并已应用
V_APPROVED_WARNINGS = "approved_with_warnings"  # 附意见批准（创作判断类，不否决）
V_REJECTED = "rejected"                        # 驳回（一致性矛盾类，未应用）

_VALID_VERDICTS = frozenset({V_APPROVED, V_APPROVED_WARNINGS, V_REJECTED})

# 裁决方
BY_DETERMINISTIC = "deterministic"  # 确定性校验（账本约束，代码判定）
BY_ARBITER = "arbiter"              # LLM 裁决者（语义/创作类）


@dataclass
class ChangeRecord:
    """一条叙事变更的元数据（追加写，永不修改历史字段）。"""

    id: str
    kind: str  # 变更类别：disposition（心性）/ relation / setting / foreshadow / thread ...
    subject: str  # 变更主体（角色名/实体名/设定键）
    before: str  # 变更前快照（人读）
    after: str  # 变更后快照
    reason: str  # 提出方的理由（规划者/写作者为什么想改）
    chapter: int = 0  # 发生章节
    evidence: str = ""  # 支撑证据（正文引用/账本事实）
    decided_by: str = ""  # deterministic | arbiter
    verdict: str = ""  # approved | approved_with_warnings | rejected
    arbiter_reason: str = ""  # 裁决理由（尤其驳回时必须给）
    warnings: list[str] = field(default_factory=list)  # 附带意见（不否决，但留痕）
    created_at: str = ""

    def validate(self) -> None:
        if not self.kind.strip() or not self.subject.strip():
            raise ChangeLogError("变更记录必须指定 kind 与 subject")
        if not self.reason.strip():
            raise ChangeLogError("变更记录必须携带提出方理由（reason）——无理由的变更本身就是红线")
        if self.verdict and self.verdict not in _VALID_VERDICTS:
            raise ChangeLogError(f"未知 verdict：{self.verdict!r}（合法：{sorted(_VALID_VERDICTS)}）")


class ChangeLogStore:
    """变更日志存取（append-only；``.state/continuity/change_log.json``）。"""

    def __init__(self, project_dir: str | Path) -> None:
        self.project_dir = Path(project_dir)
        self.path = self.project_dir / ".state" / "continuity" / "change_log.json"
        self.records: list[ChangeRecord] = []

    def load(self) -> "ChangeLogStore":
        if not self.path.exists():
            self.records = []
            return self
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise ChangeLogError(f"变更日志文件损坏（{self.path}）：{e}") from e
        self.records = [
            ChangeRecord(**{k: v for k, v in it.items() if k in ChangeRecord.__dataclass_fields__})
            for it in raw.get("records", [])
            if isinstance(it, dict)
        ]
        return self

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"records": [asdict(r) for r in self.records]}
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def append(self, record: ChangeRecord) -> ChangeRecord:
        """追加一条记录（自动补 id/时间戳；已带 id 的按原 id 入册用于重放测试）。"""
        record.validate()
        if not record.id:
            record.id = self._next_id()
        if not record.created_at:
            record.created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if record.verdict == V_REJECTED and not record.arbiter_reason.strip():
            raise ChangeLogError("驳回必须给出理由（arbiter_reason）——无声拒绝是红线")
        self.records.append(record)
        return record

    def query(
        self,
        *,
        subject: str = "",
        kind: str = "",
        verdict: str = "",
    ) -> list[ChangeRecord]:
        """按主体/类别/结论查询（多条件 AND，按 id 升序）。"""
        out = [
            r
            for r in self.records
            if (not subject or r.subject == subject)
            and (not kind or r.kind == kind)
            and (not verdict or r.verdict == verdict)
        ]
        out.sort(key=lambda r: r.id)
        return out

    def latest_applied(self, subject: str, kind: str) -> Optional[ChangeRecord]:
        """某主体某类别最近一条已批准的变更（决策查询"最新数据从哪来"）。"""
        applied = [
            r
            for r in self.records
            if r.subject == subject and r.kind == kind and r.verdict in (V_APPROVED, V_APPROVED_WARNINGS)
        ]
        return applied[-1] if applied else None

    def _next_id(self) -> str:
        nums = [int(m.group(1)) for r in self.records if (m := re.fullmatch(r"CHG-(\d+)", r.id))]
        return f"CHG-{(max(nums) + 1) if nums else 1:04d}"


__all__ = [
    "ChangeLogError",
    "ChangeRecord",
    "ChangeLogStore",
    "V_APPROVED",
    "V_APPROVED_WARNINGS",
    "V_REJECTED",
    "BY_DETERMINISTIC",
    "BY_ARBITER",
]
