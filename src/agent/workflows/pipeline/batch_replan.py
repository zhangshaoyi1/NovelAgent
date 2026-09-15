"""批间复规划接线（长线一致性设计稿第一期·B，2026-09-12）

背景：``PlannerAgent`` 此前只在首轮产出 MasterPlan 后即退场，批内章号/支线由
确定性状态机（``core/progress.py``）机械推进——剧情弧中段易漂移。本模块把
「批间复规划」接到 autowrite 批前：每批开写前，把批级进展摘要（记忆/体检教训/
问题债务/实体名册休眠预警/进行中叙事线）喂给 ``PlannerAgent.replan_batch``，
由规划者重排剩余剧情弧并裁决下一批方向（``.state/batch_directive.json``）。

失败语义（红线对齐）：复规划是增强不是门槛——失败必须 ``degrade()`` 显性留痕
后继续写（规划者缺席时状态机兜底），但绝不允许静默吞掉；摘要装配同样只降级
对应段落，不让单源故障拖垮整体。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.console import Console

from agent.core.infra.degrade import degrade


def count_chapters(project_dir: str | Path) -> int:
    """当前已写章数（实时数 chapters/ch*.md，与 autowrite --batch 换算同口径）。"""
    ch_dir = Path(project_dir) / "chapters"
    if not ch_dir.exists():
        return 0
    return len(list(ch_dir.glob("ch*.md")))


def build_batch_summary(project_dir: str | Path) -> str:
    """确定性装配批级进展摘要（规划者复规划的输入）。单源失败降级为占位行。"""
    project_dir = Path(project_dir)
    parts: list[str] = []

    # 1) 体检教训（最近一次不达标的维度与问题）
    try:
        from agent.core.quality.eval_lessons import load_eval_lessons_text

        lessons = load_eval_lessons_text(project_dir)
        parts.append(lessons if lessons else "上一轮体检：通过（无顽固问题）。")
    except Exception as e:  # noqa: BLE001
        degrade("batch_replan.summary.lessons", "体检教训读取失败，摘要缺该段", e)
        parts.append("上一轮体检：（读取失败）")

    # 2) 问题债务（未销账 watch/presence_ban 概览）
    try:
        from agent.core.story.issue_debt import IssueDebtStore, backfill_rule_ids, reverify

        # 2a) 先复查销账（2026-09-15）：规则被修复后历史误报会变成"永久待办"，
        #     每批开写前重跑来源规则，失效的当场销账——规划者只看有效债务，
        #     避免"销账一个早就不存在的矛盾"占满 batch_directive.focus。
        try:
            backfill_rule_ids(project_dir)  # 存量迁移：老条目从约束文本回填来源规则
            _rep = reverify(project_dir)
            if _rep.resolved:
                parts.append(
                    "【本轮复查销账】"
                    + "、".join(f"{i}（{why}）" for i, why in _rep.resolved)
                )
        except Exception as e:  # noqa: BLE001 - 销账失败不阻断复规划
            degrade("batch_replan.reverify", "问题债务复查销账失败，债务按原状注入", e)

        debts = IssueDebtStore(project_dir).load().open_items()
        if debts:
            lines = [f"- [{d.kind}] {d.constraint}" for d in debts[:5]]
            more = f"（等共 {len(debts)} 条）" if len(debts) > 5 else ""
            parts.append("【未销账问题债务】\n" + "\n".join(lines) + more)
    except Exception as e:  # noqa: BLE001
        degrade("batch_replan.summary.debts", "问题债务读取失败，摘要缺该段", e)

    # 3) 实体名册：休眠预警 + 升卡建议 + 进行中叙事线
    try:
        from agent.core.story.entity_ledger import EntityLedgerStore

        st = EntityLedgerStore(project_dir).load()
        current = count_chapters(project_dir)
        dorm = st.dormant_entities(current)
        if dorm:
            parts.append(
                "【实体休眠预警（长期未出现且带未了义务）】\n"
                + "\n".join(f"- {e.name}（末见第{e.last_ch}章，义务：{e.open_obligations()[0].text}）" for e in dorm[:5])
            )
        suggested = [e.name for e in st.entities if e.card_suggested and not e.has_card]
        if suggested:
            parts.append("【建议升卡实体】" + "、".join(suggested[:8]))
        threads = st.open_threads()
        if threads:
            parts.append(
                "【进行中叙事线】\n"
                + "\n".join(
                    f"- {t.name}（绑定{t.bound_entity or '无'}，已推进{len(t.milestones)}节，urgency={t.urgency}）"
                    for t in threads[:8]
                )
            )
    except Exception as e:  # noqa: BLE001
        degrade("batch_replan.summary.roster", "实体名册读取失败，摘要缺该段", e)

    if not parts:
        return "（暂无可用的进展摘要）"
    return "\n\n".join(parts) + _append_metrics(project_dir)


def _append_metrics(project_dir: Path) -> str:
    """度量类摘要段（引入率 + 上批作战笔记）；单源失败返回空。"""
    from agent.core.infra.degrade import degrade

    tail: list[str] = []
    try:
        from agent.core.story.intro_rate import intro_rate_text

        t = intro_rate_text(project_dir)
        if t:
            tail.append(t)
    except Exception as e:  # noqa: BLE001
        degrade("batch_replan.summary.intro_rate", "引入率度量失败，摘要缺该段", e)
    try:
        from agent.core.quality.batch_reflection import load_latest_reflection_text

        t = load_latest_reflection_text(project_dir)
        if t:
            tail.append(t)
    except Exception as e:  # noqa: BLE001
        degrade("batch_replan.summary.reflection", "作战笔记读取失败，摘要缺该段", e)
    return "\n\n".join(tail)


def maybe_replan(
    project_dir: str | Path,
    console: Console | None = None,
    *,
    enabled: bool = True,
    decide: Any = None,
) -> bool:
    """批前复规划入口：续写批次（有 plan.json 且已写 >0 章）时触发一次。

    Returns:
        是否实际执行了复规划。失败 degrade 显性留痕后返回 False（不阻断写作）。
    """
    console = console or Console()
    if not enabled:
        return False
    project_dir = Path(project_dir)
    plan_file = project_dir / ".state" / "plan.json"
    current = count_chapters(project_dir)
    if not plan_file.exists() or current <= 0:
        return False  # 首批（无计划/零进度）不触发，首轮规划照旧

    try:
        from agent.agents.planner import PlannerAgent
        from agent.core.story.plan_managers import audit_plan, save_audit_report

        planner = PlannerAgent(project_dir, console=console)
        summary = build_batch_summary(project_dir)
        plan = planner.replan_batch(current, summary, decide=decide)

        # ---- 四管理者确定性审计（§7）：规划不被信任，BLOCK 打回重排 1 次 ----
        report = audit_plan(project_dir, plan.episode_tree, current)
        if not report.passed:
            feedback = "上一版规划未通过管理者审计，必须修复以下问题后重新给出全部弧线：\n" + "\n".join(
                f"- [{f.level}/{f.manager}] {f.message}" for f in report.findings
            )
            plan = planner.replan_batch(current, summary + "\n\n【管理者审计反馈】\n" + feedback, decide=decide)
            report = audit_plan(project_dir, plan.episode_tree, current)
            report.retried = True
        save_audit_report(project_dir, report)

        if report.passed:
            console.print(
                f"[cyan]Planner 批间复规划完成（第 {current} 章后剩余弧线已重排，"
                f"管理者审计{'通过' if not report.warns else f'通过，WARN {len(report.warns)} 条留痕'}，"
                f"下一批裁决落盘 .state/batch_directive.json）[/cyan]"
            )
        else:
            # 打回重排后仍 BLOCK：保留计划但显性上报（人工可查 plan_audit.json）
            console.print(f"[red]✗ 批间复规划审计仍未通过（BLOCK {len(report.blocks)} 条），"
                          f"计划已保留但需人工复核 .state/plan_audit.json[/red]")
        return True
    except Exception as e:  # noqa: BLE001 - 显性降级：规划者缺席时状态机兜底继续写
        degrade("autowrite.batch_replan", "批间复规划失败，本批沿用既有计划继续写", e)
        console.print(f"[yellow]⚠ 批间复规划失败（{e}），本批沿用既有计划继续[/yellow]")
        return False


__all__ = ["build_batch_summary", "count_chapters", "maybe_replan"]
