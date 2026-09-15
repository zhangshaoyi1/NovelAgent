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
    #: 来源规则 id（一致性规则如 ``relation_conflict``）。2026-09-15 新增：
    #: 债务此前只把规则名写进自由文本 ``constraint``，没有任何结构化出处，
    #: 于是「规则被修复后债务是否还有效」无从判定 —— 债务只进不出。
    #: 有它之后 ``reverify()`` 才能重跑来源规则并自动销账。
    rule_id: str = ""
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
        rule_id: str = "",
    ) -> IssueDebt:
        """登记一条债务；id 缺省自动生成（DEBT-递增序号）。"""
        debt = IssueDebt(
            id=debt_id or self._next_id(),
            kind=kind,
            constraint=constraint.strip(),
            rule_id=rule_id.strip(),
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


#: 历史债务的规则标签格式（2026-09-12 起由 ``agentic_pipeline`` 固定拼装：
#: ``f"第{ch}章一致性警告（{rule_id}）：{description}"``）。rule_id 本来就写在
#: 文本里，只是没被结构化——回填只认这个**机器生成的固定格式**，不猜语义。
_DEBT_RULE_TAG = re.compile(r"一致性警告（([a-z_][a-z0-9_]*)）")


def backfill_rule_ids(project_dir: str | Path) -> int:
    """给 ``rule_id`` 为空的历史债务回填来源规则（存量迁移，2026-09-15）。

    只处理能**确定**出处的条目：
    - ``constraint`` 命中 :data:`_DEBT_RULE_TAG`（登记时自动拼装的固定格式）；
    - 标签里的规则名**当前真实存在**（在 ``ConsistencyChecker`` 的内置规则表里）。

    其余保持 ``rule_id=""``（来源不可判定，交给 :func:`reverify` 记 skipped）。
    返回本次回填条数；无变化不落盘。
    """
    from agent.core.quality.consistency.checker import ConsistencyChecker  # 延迟导入避免环

    known = {r.id for r in ConsistencyChecker(project_dir)._builtin_rules()}
    store = IssueDebtStore(project_dir).load()
    n = 0
    for d in store.debts:
        if d.rule_id:
            continue
        m = _DEBT_RULE_TAG.search(d.constraint)
        if m and m.group(1) in known:
            d.rule_id = m.group(1)
            n += 1
    if n:
        store.save()
    return n


#: 复查销账说明模板（主体已不再命中，见 :func:`_subject_still_hit`）。
REVERIFY_NOTE_SUBJECT = (
    "复查销账：来源规则 {rule_id} 仍命中，但第{ch}章所指主体「{subject}」已不再命中"
)

#: 从债务约束文本里取**主体名**（登记格式用「」包住角色名，如
#: 「关系网(graph.md)显示「林凡」存在互动型关系…」）。
_SUBJECT_TOKEN = re.compile(r"「([^」]+)」")


def _subject_token(constraint: str) -> str:
    """取约束文本里的第一个主体名；取不到返回空串。"""
    m = _SUBJECT_TOKEN.search(constraint or "")
    return m.group(1) if m else ""


def _subject_still_hit(debt: "IssueDebt", hits: "list[Any]") -> "bool | None":
    """本条债务所指的**具体主体**是否仍在命中列表里。

    Returns:
        ``True``  该主体仍被命中 ⇒ 债务有效；
        ``False`` 规则命中的是别的对象 ⇒ 本条所指冲突已消失；
        ``None``  约束文本里定位不到主体 ⇒ **保守**，由调用方按"仍有效"处理。

    为什么需要它：只判"规则是否还命中任何东西"是不够的。实测 ch18 的死亡断言
    在主体锚定修复后**归因换了人**（林凡 → 丹王衍），规则仍返回 1 条命中，
    于是挂在账上的 3 条「林凡/沈长风」债务被误判为"继续有效"——写手每章仍被
    提醒去规避一个不存在的矛盾。判据必须落到"同一条冲突"上，而不是"同一条规则"。
    """
    token = _subject_token(debt.constraint)
    if not token:
        return None
    return any(token in str(getattr(c, "description", "")) for c in hits)


#: 自动销账说明模板（:func:`reverify` 用）。必须写清"因何而销"——
#: 销账是隐形的失效，理由不写等于把「复查通过」和「复查没做成」混为一谈。
REVERIFY_NOTE = "复查销账：来源规则 {rule_id} 在登记章 ch{ch} 原文上已不再命中"


@dataclass
class ReverifyReport:
    """复查结果：**销账的**与**没能复查的**必须分开列。

    ``skipped`` 不是错误汇总，而是"本条债务未被证明失效"的显性记录——
    调用方据此知道哪些债务还在生效、因为什么没被销掉。
    """

    resolved: list[tuple[str, str]] = field(default_factory=list)   # (id, note)
    skipped: list[tuple[str, str]] = field(default_factory=list)    # (id, why)

    @property
    def saved(self) -> bool:
        return bool(self.resolved)


def reverify(
    project_dir: str | Path,
    *,
    kinds: tuple[str, ...] = (KIND_WATCH,),
) -> ReverifyReport:
    """复查带 ``rule_id`` 的问题债务：来源规则不再命中 ⇒ 自动销账（2026-09-15）。

    为什么要它
    ----------
    ``watch`` 债务由 ``render_constraints`` **每章原样注入 writer**，并被
    ``batch_replan`` 喂给规划者。但债务此前**只进不出**：规则本身修好之后，
    历史误报仍挂在账上，持续占用规划与写作的注意力。实测灵荒薪传
    ``relation_conflict`` 主体锚定修复（2026-09-15 10:39）后，8 条
    "林凡/沈长风已故"误报仍为 open，且已成为批间复规划裁决的写作焦点
    （``batch_directive.focus``）——一个不存在的矛盾被当成待办写进了正文计划。

    判定纪律
    --------
    - 只销 ``watch`` / ``gate_skipped``；``presence_ban`` 是人工作出的禁令，
      规则修不修都不该被自动解除（默认 :data:`kinds` 已排除）。
    - 只销**带 rule_id** 的条目；历史条目来源不可判定 → 记 ``skipped``，不猜。
    - 复查**必须真的跑成功**：规则不存在 / 原文缺失 / 规则抛错 ⇒ ``skipped``。
      **绝不把"没能复查"当成"不再命中"**（同族纪律：失败必须显性化）。
    """
    from agent.core.quality.consistency.checker import recheck_rule  # 延迟导入避免环

    store = IssueDebtStore(project_dir).load()
    report = ReverifyReport()
    for d in store.open_items(list(kinds)):
        if not d.rule_id:
            report.skipped.append((d.id, "无 rule_id（历史条目，来源规则不可判定）"))
            continue
        if d.registered_ch <= 0:
            report.skipped.append((d.id, "无登记章号，定位不到复查原文"))
            continue
        hits = recheck_rule(project_dir, d.rule_id, d.registered_ch)
        if hits is None:
            report.skipped.append((
                d.id,
                f"无法复查（规则 {d.rule_id} 不存在或 ch{d.registered_ch} 原文缺失），"
                "保持未销账",
            ))
            continue
        if hits:
            same = _subject_still_hit(d, hits)
            if same is False:
                # 规则仍命中，但命中的**主体换了人**：本条债务所指的具体冲突
                # 已不存在（实测：ch18 的死亡断言修复后归因到「丹王衍」，
                # 而挂在账上的 3 条是「林凡」/「沈长风」——留着就会每章继续
                # 提醒写手规避一个不存在的矛盾）。
                note = REVERIFY_NOTE_SUBJECT.format(
                    rule_id=d.rule_id, ch=d.registered_ch,
                    subject=_subject_token(d.constraint) or "?",
                )
                if store.resolve(d.id, note):
                    report.resolved.append((d.id, note))
                continue
            report.skipped.append((d.id, f"复查仍命中 {len(hits)} 项，债务继续有效"))
            continue
        note = REVERIFY_NOTE.format(rule_id=d.rule_id, ch=d.registered_ch)
        if store.resolve(d.id, note):
            report.resolved.append((d.id, note))
    if report.saved:
        store.save()
    return report


__all__ = [
    "IssueDebt",
    "IssueDebtError",
    "IssueDebtStore",
    "ReverifyReport",
    "REVERIFY_NOTE",
    "REVERIFY_NOTE_SUBJECT",
    "backfill_rule_ids",
    "render_constraints",
    "reverify",
    "KIND_PRESENCE_BAN",
    "KIND_WATCH",
    "KIND_GATE_SKIPPED",
]
