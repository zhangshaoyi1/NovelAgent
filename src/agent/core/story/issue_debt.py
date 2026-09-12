"""问题债务登记簿（issue debt registry，2026-09-12）

背景：写时/章后发现的"问题不大"的一致性缺陷（WARN 级冲突、门禁告警留章、
离场角色再次出场等）此前只写进 console / chapter_quality_flags.json 后即成
死数据——没有任何机制阻止同类问题在后续章节反复出现；等到几十章后想修，
重写代价已经不可接受。

本模块提供最小闭环：
1. **登记**：确认的问题写入 ``<project>/.state/issue_debts.json``，条目带
   kind / subject / constraint / scope / status；
2. **写时消费**：``render_constraints()`` 产出的约束文本由写章管线注入
   writer（agentic_write character_constraints 注入点）；
3. **门禁执行**：``kind="presence_ban"`` 的条目由 ConsistencyChecker 的
   presence_conflict 规则强制执行——被禁主体在本章正文出现即 BLOCK；
4. **销账**：``resolve()`` 显性销账（人工 CLI 或自动修复流程），超期未销账
   条目在写时注入时标注账龄，防止债务静默积累。

纯文件读写 + 数据类，无 LLM、无 IO 副作用（除落盘），所有失败显性降级。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional


#: 债务种类
KIND_PRESENCE_BAN = "presence_ban"  # 主体禁出场（死亡/离场/封印等），门禁 BLOCK
KIND_WATCH = "watch"                # 观察项：写时注入提醒，门禁不拦
KIND_GATE_SKIPPED = "gate_skipped"  # 门禁基础设施故障放行的章，批末查漏

_VALID_KINDS = frozenset({KIND_PRESENCE_BAN, KIND_WATCH, KIND_GATE_SKIPPED})


class IssueDebtError(Exception):
    """登记簿操作违规（未知 kind / 未知 id 等）。"""


@dataclass
class IssueDebt:
    """一条确认待解决的问题债务。"""

    id: str
    kind: str  # presence_ban | watch | gate_skipped
    constraint: str  # 约束/问题描述（人读，写时原样注入）
    subject: str = ""  # 主体（角色名/设定名）；presence_ban 必填
    scope: str = ""  # 适用范围（章节范围/地图/支线），空 = 全书
    registered_ch: int = 0  # 登记时章节号
    status: str = "open"  # open | resolved
    resolve_note: str = ""  # 销账说明

    def validate(self) -> None:
        if self.kind not in _VALID_KINDS:
            raise IssueDebtError(f"未知债务 kind：{self.kind!r}（合法：{sorted(_VALID_KINDS)}）")
        if not self.constraint.strip():
            raise IssueDebtError("constraint 不能为空")
        if self.kind == KIND_PRESENCE_BAN and not self.subject.strip():
            raise IssueDebtError("presence_ban 类债务必须指定 subject（被禁主体名）")


class IssueDebtStore:
    """登记簿存取（仿 setting_canon 的 load/save 模式）。"""

    def __init__(self, project_dir: str | Path) -> None:
        self.project_dir = Path(project_dir)
        self.path = self.project_dir / ".state" / "issue_debts.json"
        self.debts: list[IssueDebt] = []

    def load(self) -> "IssueDebtStore":
        if not self.path.exists():
            self.debts = []
            return self
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise IssueDebtError(f"登记簿文件损坏（{self.path}）：{e}") from e
        items = raw.get("debts", []) if isinstance(raw, dict) else []
        self.debts = [IssueDebt(**{k: v for k, v in it.items() if k in IssueDebt.__dataclass_fields__})
                      for it in items if isinstance(it, dict)]
        return self

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"debts": [asdict(d) for d in self.debts]}
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # ------ 操作 ------
    def add(
        self,
        kind: str,
        constraint: str,
        *,
        subject: str = "",
        scope: str = "",
        registered_ch: int = 0,
        debt_id: str = "",
    ) -> IssueDebt:
        """登记一条债务；id 缺省自动生成（DEBT-递增序号）。"""
        debt = IssueDebt(
            id=debt_id or self._next_id(),
            kind=kind,
            constraint=constraint.strip(),
            subject=subject.strip(),
            scope=scope.strip(),
            registered_ch=int(registered_ch or 0),
        )
        debt.validate()
        # 同 id 去重：已存在则更新而非重复登记
        for i, old in enumerate(self.debts):
            if old.id == debt.id:
                self.debts[i] = debt
                break
        else:
            self.debts.append(debt)
        return debt

    def resolve(self, debt_id: str, note: str = "") -> bool:
        """销账：置 resolved；id 不存在返回 False（调用方显性处理）。"""
        for d in self.debts:
            if d.id == debt_id and d.status != "resolved":
                d.status = "resolved"
                d.resolve_note = note.strip()
                return True
        return False

    def open_items(self, kinds: "list[str] | None" = None) -> list[IssueDebt]:
        """未销账条目（可按 kind 过滤），按登记章号升序。"""
        out = [d for d in self.debts if d.status == "open"
               and (kinds is None or d.kind in kinds)]
        out.sort(key=lambda d: (d.registered_ch, d.id))
        return out

    def _next_id(self) -> str:
        nums = [int(m.group(1)) for d in self.debts
                if (m := re.fullmatch(r"DEBT-(\d+)", d.id))]
        return f"DEBT-{(max(nums) + 1) if nums else 1:04d}"


def render_constraints(project_dir: str | Path, current_ch: int = 0) -> str:
    """把未销账债务渲染成写时约束文本（注入 writer 的 character_constraints）。

    presence_ban → 硬约束措辞；watch/gate_skipped → 提醒措辞并标注账龄
    （登记章距今越远越要显性），空 = 无债务返回空串。
    """
    try:
        store = IssueDebtStore(project_dir).load()
    except IssueDebtError as e:
        # 登记簿损坏：宁缺毋滥地降级为空（写时不能因债务文件挂掉），但错误必须显性
        from agent.core.infra.degrade import degrade

        degrade("issue_debt.render", "问题债务登记簿损坏，本轮约束注入为空", e)
        return ""
    lines: list[str] = []
    for d in store.open_items():
        age = (current_ch - d.registered_ch) if (current_ch and d.registered_ch) else 0
        age_tag = f"（自第{d.registered_ch}章登记，已挂账{age}章）" if age > 0 else (
            f"（第{d.registered_ch}章登记）" if d.registered_ch else ""
        )
        if d.kind == KIND_PRESENCE_BAN:
            lines.append(f"【硬性禁令】{d.constraint}{age_tag}")
        else:
            lines.append(f"【待确认问题·注意规避】{d.constraint}{age_tag}")
    if not lines:
        return ""
    return "\n【问题债务登记（此前确认未解决的问题，本章必须遵守）】\n" + "\n".join(
        f"- {ln}" for ln in lines
    )


__all__ = [
    "IssueDebt",
    "IssueDebtError",
    "IssueDebtStore",
    "render_constraints",
    "KIND_PRESENCE_BAN",
    "KIND_WATCH",
    "KIND_GATE_SKIPPED",
]
