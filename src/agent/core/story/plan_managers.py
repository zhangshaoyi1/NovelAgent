"""管理者团队——确定性计划审计（长线一致性设计稿第二期·四管理者）

定位（§7 拍板）：**度量并报告，不规划**。本模块对批间复规划产出的剧情弧
做计划侧确定性审计（零 LLM），产出风险报告：

- 连续性管理者：弧线必须衔接当前进度、不重叠（BLOCK 级）；
- 结构管理者：弧线间隔空洞、超出全书总章数（WARN/BLOCK）；
- 债务管理者：进行中叙事线在本批弧线中零推进迹象（WARN）；
- 角色管理者：休眠实体（带未了义务长期未出场）在弧线目标中无安排（WARN）。

语义类判断（"这条弧线是否合理推进传承线"）留给 LLM 管理者（未落地）；
确定性部分先收口为 ``audit_plan``，BLOCK 在 ``batch_replan.maybe_replan``
里触发打回规划者修订（最多 1 次），审计报告落盘 ``.state/plan_audit.json``
显性留痕。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BLOCK = "block"
WARN = "warn"


@dataclass
class AuditFinding:
    """一条审计发现。"""

    level: str  # block | warn
    manager: str  # continuity | structure | debt | character
    message: str


@dataclass
class PlanAuditReport:
    """计划审计报告（BLOCK/WARN 分级，全量留痕）。"""

    current_chapter: int = 0
    findings: list[AuditFinding] = field(default_factory=list)
    audited_at: str = ""
    retried: bool = False  # 是否因 BLOCK 打回重排过

    @property
    def blocks(self) -> list[AuditFinding]:
        return [f for f in self.findings if f.level == BLOCK]

    @property
    def warns(self) -> list[AuditFinding]:
        return [f for f in self.findings if f.level == WARN]

    @property
    def passed(self) -> bool:
        return not self.blocks

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    def render(self) -> str:
        if not self.findings:
            return f"计划审计通过（{self.current_chapter} 章后弧线，无发现）"
        lines = [f"计划审计（当前第 {self.current_chapter} 章）：BLOCK {len(self.blocks)} / WARN {len(self.warns)}"]
        lines.extend(f"  [BLOCK][{f.manager}] {f.message}" for f in self.blocks)
        lines.extend(f"  [WARN][{f.manager}] {f.message}" for f in self.warns)
        return "\n".join(lines)


def audit_plan(project_dir: str | Path, arcs: list[Any], current_chapter: int) -> PlanAuditReport:
    """确定性审计剧情弧（arcs 为含 name/chapter_start/chapter_end/goal 属性的对象）。"""
    report = PlanAuditReport(current_chapter=current_chapter,
                             audited_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))

    # ---- 连续性管理者：衔接当前进度、不重叠 ----
    for a in arcs:
        if int(getattr(a, "chapter_start", 0)) <= current_chapter:
            report.findings.append(AuditFinding(
                BLOCK, "continuity",
                f"弧线「{getattr(a, 'name', '?')}」起始章 {getattr(a, 'chapter_start', 0)} "
                f"未越过当前进度 {current_chapter}（历史不可改写）",
            ))
            break
    sorted_arcs = sorted(arcs, key=lambda a: int(getattr(a, "chapter_start", 0)))
    for prev, nxt in zip(sorted_arcs, sorted_arcs[1:]):
        if int(getattr(nxt, "chapter_start", 0)) < int(getattr(prev, "chapter_end", 0)):
            report.findings.append(AuditFinding(
                BLOCK, "continuity",
                f"弧线「{getattr(prev, 'name', '?')}」与「{getattr(nxt, 'name', '?')}」重叠",
            ))
            break

    # ---- 结构管理者：空洞 + 越界 ----
    for prev, nxt in zip(sorted_arcs, sorted_arcs[1:]):
        gap = int(getattr(nxt, "chapter_start", 0)) - int(getattr(prev, "chapter_end", 0))
        if gap > 1:
            report.findings.append(AuditFinding(
                WARN, "structure",
                f"「{getattr(prev, 'name', '?')}」与「{getattr(nxt, 'name', '?')}」之间有 {gap - 1} 章空洞",
            ))
    try:
        total = _plan_total(project_dir)
        last_end = max((int(getattr(a, "chapter_end", 0)) for a in arcs), default=0)
        if total and last_end > total:
            report.findings.append(AuditFinding(
                BLOCK, "structure",
                f"弧线收尾章 {last_end} 超出全书总章数 {total}",
            ))
    except Exception:  # noqa: BLE001 - 总章数缺失时跳过越界检查
        pass

    # ---- 债务管理者：进行中叙事线在弧线目标中零推进迹象 ----
    try:
        from agent.core.story.entity_ledger import EntityLedgerStore

        threads = EntityLedgerStore(project_dir).load().open_threads()
        if threads:
            goals_text = " ".join(str(getattr(a, "goal", "") or "") for a in arcs)
            names_text = " ".join(str(getattr(a, "name", "") or "") for a in arcs)
            for t in threads:
                if t.name not in goals_text and t.name not in names_text and t.urgency == "high":
                    report.findings.append(AuditFinding(
                        WARN, "debt",
                        f"高 urgency 叙事线「{t.name}」在本批弧线中无任何推进迹象",
                    ))
    except Exception:  # noqa: BLE001 - 名册缺失跳过
        pass

    # ---- 角色管理者：休眠实体在弧线中无安排 ----
    try:
        from agent.core.story.entity_ledger import EntityLedgerStore

        dorm = EntityLedgerStore(project_dir).load().dormant_entities(current_chapter)
        if dorm:
            goals_text = " ".join(str(getattr(a, "goal", "") or "") + str(getattr(a, "name", "") or "") for a in arcs)
            for e in dorm[:3]:
                if e.name not in goals_text:
                    report.findings.append(AuditFinding(
                        WARN, "character",
                        f"休眠实体「{e.name}」（末见第{e.last_ch}章，义务：{e.open_obligations()[0].text[:30]}）"
                        "在本批弧线中无回归/收束安排",
                    ))
    except Exception:  # noqa: BLE001
        pass

    return report


def _plan_total(project_dir: Path) -> int:
    data = json.loads((Path(project_dir) / ".state" / "plan.json").read_text(encoding="utf-8"))
    return int(data.get("total_chapters") or 0)


def save_audit_report(project_dir: str | Path, report: PlanAuditReport) -> Path:
    path = Path(project_dir) / ".state" / "plan_audit.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


__all__ = [
    "BLOCK",
    "WARN",
    "AuditFinding",
    "PlanAuditReport",
    "audit_plan",
    "save_audit_report",
]
