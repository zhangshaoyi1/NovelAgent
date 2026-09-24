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

    # ---- 批级进展摘要（复规划与逐章契约补齐**共用**，只装一次）----
    # ★ 与下面的复规划解耦后仍要共用同一份摘要：两处若各装一次，不仅重复
    #   `reverify`/引入率等开销，还会让"补齐"看到与"复规划"不同的进展口径。
    try:
        summary = build_batch_summary(project_dir)
    except Exception as e:  # noqa: BLE001 - 摘要装配失败降级为占位，两条链各自继续
        degrade("batch_replan.summary", "批级摘要装配失败，复规划与逐章补齐均缺该段", e)
        summary = "（暂无可用的进展摘要）"

    # ---- 逐章契约供给补齐（供给端修复，2026-09-24）----
    # ★ 与复规划**并列**而非串联：复规划只产 arc 级规划，**从不写 subline.md 的
    #   逐章行**（唯一写入方是 M3 首轮），⇒ 21 章以后写手拿不到本章契约 ⇒ 按阶段
    #   模板自编 ⇒ 同质/注水 ⇒ 评委不合格 ⇒ 回退输入不变 ⇒ 整窗销毁-重写死循环。
    #   故复规划失败也必须补齐（二者是两条独立的批前增强链）。
    try:
        from agent.workflows.pipeline.subline_contract import ensure_window_contracts

        ensure_window_contracts(project_dir, summary=summary, console=console)
    except Exception as e:  # noqa: BLE001 - 增强项：失败显性留痕后继续写
        degrade(
            "autowire.subline_contract",
            "批前逐章契约补齐失败，本批沿用既有细纲（写手回退阶段级供给）",
            e,
        )

    try:
        from agent.agents.planner import PlannerAgent
        from agent.core.story.plan_managers import audit_plan, save_audit_report

        planner = PlannerAgent(project_dir, console=console)
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

        # ---- M5 规划评委（采样模式，**恒不阻断**）----
        # ★ 与上面的四管理者**职责不重叠**：四管理者判确定性结构（弧线衔接/
        #   重叠/空洞/越界），本层判**语义**（弧线是否合理推进母题）与
        #   **规划产出粒度**（段级+章级双级、档位覆盖）。
        # ★ 只记录不拦（§8.3）：其结果**绝不进入**下面的 BLOCK 判定。
        #   放在四管理者**之后**：即使语义评审炸了也不影响既有拦截链。
        try:
            from agent.core.story.plan_critic import review_plan

            review_plan(
                project_dir,
                arcs=plan.episode_tree,
                current_chapter=current,
                llm=planner.llm if hasattr(planner, "llm") else None,
                console=console,
            )
        except Exception as e:  # noqa: BLE001 - 观测面异常绝不阻断复规划
            degrade("autowire.plan_critic", "规划评委评审失败，本次采样缺失（不影响写作）", e)

        # ---- A5：规划变更 → 失效扇出（原实现只在「回滚」时触发）----
        # ★ 缺口（2026-09-20 立项）：`chapter_invalidation` 建成后**只在
        #   `m10_rollback` 被调用** ⇒ 规划改了、下游派生状态与已写章节不知情。
        # ★ 作用域**刻意窄**：只清算「计划派生」的 payoff_script + 对已写正文
        #   只报数（正文不因计划改变而失效，机械重写＝不可逆销毁）。
        # ★ 只对「有计划排期、且正文已存在」的章触发（否则是空转）。
        try:
            from agent.core.story.chapter_invalidation import (
                invalidate_for_plan_change,
                plan_chapter_digest,
            )

            ch_dir = project_dir / "chapters"
            affected = [
                c for c in sorted(plan_chapter_digest(project_dir))
                if (ch_dir / f"ch{c:03d}.md").exists()
            ]
            if affected:
                inv = invalidate_for_plan_change(project_dir, affected)
                if inv.stale:
                    console.print(
                        f"[yellow]⚠ 规划已变更：{inv.summary()}"
                        f"——已写章节仍在旧计划契约下产出，仅报数不自动重写[/yellow]"
                    )
        except Exception as e:  # noqa: BLE001 - 扇出异常绝不阻断复规划
            degrade(
                "batch_replan.plan_change_fanout",
                "规划变更后的失效扇出失败（本批继续，影响面待人工核查）",
                e,
            )

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
