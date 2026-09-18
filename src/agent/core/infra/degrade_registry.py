"""降级命名空间注册表（G2 契约化 · 件 A）

────────────────────────────────────────────────────────────────
为什么需要它
────────────────────────────────────────────────────────────────
``degrade(where, reason, ...)`` 的第一个参数 ``where`` 决定日志器名
（``agent.degrade.<where>``）与事件源。此前它是**自由字符串**——谁都能随手写
一个新名字，没人校验，于是：

- 命名空间无法枚举 → 无法回答「系统里到底有哪些降级点」；
- 同一位置换名即"新降级点" → 配额棘轮（``DEGRADE_EXEMPTION_BUDGET``）形同虚设；
- 改名/删除降级点无痕迹 → 无契约可审。

本模块把 ``where`` 从**自由字符串**升级为**契约面**：每个命名空间必须在此登记，
登记内容含**归属模块**（谁负责这个降级点）。红线
``tests/architecture/test_degrade_contract.py`` 用 AST 扫描全仓 ``degrade()``
调用，与注册表做**双向差集**：

    调用了但未登记 → FAIL（新增降级点必须登记）
    登记了但无调用 → FAIL（僵尸条目必须删除）

────────────────────────────────────────────────────────────────
命名空间规范
────────────────────────────────────────────────────────────────
- 形如 ``<域>.<子域>...``，小写字母/数字/下划线，点号分隔，至少一段；
- 建议首段 = 归属模块的短名（如 ``m5.context.rag`` 属于 ``workflows/writing/m5_context.py``）；
- **动态命名空间**（f-string）取其**静态前缀**登记于 ``DEGRADE_NAMESPACE_PREFIXES``，
  匹配规则：``ns == prefix`` 或 ``ns.startswith(prefix + ".")``。

⚠ 本表的初始化内容由 2026-09-15 全仓 AST 扫描生成（125 字面量 + 3 前缀），
是**存量快照**，不是设计目标。新增/修改降级点须同步维护本表 ——
维护它正是「降级点有契约」这件事本身。
"""

from __future__ import annotations

import re

# 命名空间语法：小写/数字/下划线，点号分隔的至少一段
NAMESPACE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*$")

# ── 字面量命名空间 → 归属模块（相对 src/agent）──────────────────────
DEGRADE_NAMESPACES: dict[str, str] = {
    "agentic_write.baseline_drift": "workflows/writing/agentic_write.py",
    "agentic_write.combined_quality_gate": "workflows/writing/agentic_write.py",
    "agentic_write.consistency_check": "workflows/writing/agentic_write.py",
    "agentic_write.craft_guide": "workflows/writing/agentic_write.py",
    "agentic_write.d_review": "workflows/writing/agentic_write.py",
    "agentic_write.debut_check": "workflows/writing/agentic_write.py",
    "agentic_write.deslop_recheck": "workflows/writing/agentic_write.py",
    "agentic_write.l1_block": "workflows/writing/agentic_write.py",
    "agentic_write.l1_trace": "workflows/writing/agentic_write.py",
    "agentic_write.dup_check": "workflows/writing/agentic_write.py",
    "agentic_write.ending_gap": "workflows/writing/agentic_write.py",
    "agentic_write.entity_check": "workflows/writing/agentic_write.py",
    "agentic_write.foreshadow_check": "workflows/writing/agentic_write.py",
    "agentic_write.gate_skipped_log": "workflows/writing/agentic_write.py",
    "agentic_write.golden_context": "workflows/writing/agentic_write.py",
    "agentic_write.golden_gate": "workflows/writing/agentic_write.py",
    "agentic_write.golden_gate_retry": "workflows/writing/agentic_write.py",
    "agentic_write.issue_debt": "workflows/writing/agentic_write.py",
    "agentic_write.mainline": "workflows/writing/agentic_write.py",
    "agentic_write.quality_gate": "workflows/writing/agentic_write.py",
    "agentic_write.text_hygiene": "workflows/writing/agentic_write.py",
    "agentic_write.theme_contract": "workflows/writing/agentic_write.py",
    "atomic.discard": "core/infra/atomic.py",
    "autowrite.batch_replan": "workflows/pipeline/batch_replan.py",
    "batch_reflection.input.debts": "core/quality/batch_reflection.py",
    "batch_reflection.input.flags": "core/quality/batch_reflection.py",
    "batch_reflection.input.l1": "core/quality/batch_reflection.py",
    "batch_reflection.input.lessons": "core/quality/batch_reflection.py",
    "batch_reflection.input.regress": "core/quality/batch_reflection.py",
    "batch_reflection.record": "core/quality/batch_reflection.py",
    "batch_replan.reverify": "workflows/pipeline/batch_replan.py",
    "batch_replan.summary.debts": "workflows/pipeline/batch_replan.py",
    "batch_replan.summary.intro_rate": "workflows/pipeline/batch_replan.py",
    "batch_replan.summary.lessons": "workflows/pipeline/batch_replan.py",
    "batch_replan.summary.reflection": "workflows/pipeline/batch_replan.py",
    "batch_replan.summary.roster": "workflows/pipeline/batch_replan.py",
    "budget_planner.curve_sync": "workflows/pipeline/budget_planner.py",
    "budget_planner.llm": "workflows/pipeline/budget_planner.py",
    "budget_planner.route_anchor": "workflows/pipeline/budget_planner.py",
    "budget_planner.subline_title": "workflows/pipeline/budget_planner.py",
    "change_gate.arbiter": "core/story/change_gate.py",
    "cli.appeal.load_ctx": "cli/commands/appeal.py",
    "closure_plan.debts": "core/story/closure_plan.py",
    "closure_plan.foreshadows.skip": "core/story/closure_plan.py",
    "closure_plan.load": "core/story/closure_plan.py",
    "closure_plan.roster": "core/story/closure_plan.py",
    "compose.closure_plan": "core/infra/compose_runner.py",
    "consistency.recheck_rule": "core/quality/consistency/checker.py",
    "design_brief.characters": "core/story/design_brief.py",
    "design_brief.chapters": "core/story/design_brief.py",
    "design_brief.plan": "core/story/design_brief.py",
    "design_brief.rubric": "core/story/design_brief.py",
    "design_brief.setting": "core/story/design_brief.py",
    "design_brief.subline": "core/story/design_brief.py",
    "entity_ledger.render": "core/story/entity_ledger.py",
    "entity_ledger.sync": "core/story/entity_ledger.py",
    "eval_lessons.save": "core/quality/eval_lessons.py",
    "evaluator_dims.mainline_stats.progress": "agents/evaluator_dims.py",
    "evaluator_dims.mainline_stats.subline": "agents/evaluator_dims.py",
    "evaluator_dims.mainline_stats.total": "agents/evaluator_dims.py",
    "evaluator.rollback_barrier": "agents/evaluator.py",
    "intro_rate.text": "core/story/intro_rate.py",
    "issue_debt.render": "core/story/issue_debt.py",
    "knowledge_ledger.render": "core/story/entity_ledger.py",
    "ledger_delta.apply": "workflows/writing/ledger_delta_producer.py",
    "ledger_delta.import": "workflows/writing/ledger_delta_producer.py",
    "ledger_delta.settle": "workflows/writing/ledger_delta_producer.py",
    "ledger_delta.unexpected": "workflows/writing/ledger_delta_producer.py",
    "m12.audit.rag": "workflows/evaluation/m12_audit.py",
    "m12.audit.recent_fallback": "workflows/evaluation/m12_audit.py",
    "m20.analyze.stage": "workflows/evaluation/m20_analyze.py",
    "m20.analyze.summary": "workflows/evaluation/m20_analyze.py",
    "m5.context.batch_directive": "workflows/writing/m5_context.py",
    "m5.context.closure": "workflows/writing/m5_context.py",
    "m5.context.continuity": "workflows/writing/m5_context.py",
    "m5.context.debts": "workflows/writing/m5_context.py",
    "m5.context.design_brief": "workflows/writing/m5_context.py",
    "m5.context.disposition": "workflows/writing/m5_context.py",
    "m5.context.learnings": "workflows/writing/m5_context.py",
    "m5.context.ledger": "workflows/writing/m5_context.py",
    "m5.context.payoff": "workflows/writing/m5_context.py",
    "m5.context.payoff_autogen": "workflows/writing/m5_context.py",
    "m5.context.rag": "workflows/writing/m5_context.py",
    "m5.context.relation": "workflows/writing/m5_context.py",
    "m5.context.resource": "workflows/writing/m5_context.py",
    "m5.record_book_ledger": "workflows/writing/m5_persist.py",
    "m5_context.setting_canon": "workflows/writing/m5_context.py",
    "m5_persist.archive_chapter": "workflows/writing/m5_persist.py",
    "m5_persist.ledger_delta": "workflows/writing/m5_persist.py",
    "m5_persist.setting_canon": "workflows/writing/m5_persist.py",
    "payoff.resolve.chapters": "core/story/payoff_script.py",
    "payoff.resolve.plan": "core/story/payoff_script.py",
    "payoff.resolve.state": "core/story/payoff_script.py",
    "pipeline.batch_reflection": "workflows/pipeline/agentic_pipeline.py",
    "pipeline.entity_sync": "workflows/pipeline/agentic_pipeline.py",
    "pipeline.gate_blind.editor_joint_recheck": "workflows/pipeline/agentic_pipeline.py",
    "pipeline.gate_blind.editor_review": "workflows/pipeline/agentic_pipeline.py",
    "pipeline.gate_blind.guardrails_gate": "workflows/pipeline/agentic_pipeline.py",
    "pipeline.gate_blind.guardrails_joint_recheck": "workflows/pipeline/agentic_pipeline.py",
    "pipeline.gate_blind.load": "workflows/pipeline/agentic_pipeline_events.py",
    "pipeline.gate_blind.save": "workflows/pipeline/agentic_pipeline_events.py",
    "pipeline.gate_skipped_scan": "workflows/pipeline/agentic_pipeline_events.py",
    "pipeline.last_rollback_target": "workflows/pipeline/agentic_pipeline_agents.py",
    "pipeline.resource_sync": "workflows/pipeline/agentic_pipeline.py",
    "pipeline.rollback_budget": "workflows/pipeline/agentic_pipeline.py",
    "pipeline.rollback_ledger": "workflows/pipeline/agentic_pipeline_agents.py",
    "pipeline.rollback_ledger_baseline": "workflows/pipeline/agentic_pipeline_agents.py",
    "pipeline.rollback_ledger_mismatch": "workflows/pipeline/agentic_pipeline_agents.py",
    "pipeline.rollback_unified_ledger": "workflows/pipeline/agentic_pipeline_agents.py",
    "pipeline.rolling_lessons": "workflows/pipeline/agentic_pipeline_agents.py",
    "plan_managers.dormant": "core/story/plan_managers.py",
    "plan_managers.threads": "core/story/plan_managers.py",
    "plan_managers.total": "core/story/plan_managers.py",
    "planner.replan.consolidate": "agents/planner.py",
    "power_scale.render": "core/story/entity_ledger.py",
    "process_manager.kill": "daemon/process_manager.py",
    "project_lock.acquire": "core/project_lock.py",
    "project_lock.release": "core/project_lock.py",
    "reader_appeal.canon_without_design": "core/quality/scoring/reader_appeal.py",
    "reader_appeal.count_unit_anomaly": "core/quality/scoring/reader_appeal.py",
    "reader_appeal.design_brief": "core/quality/scoring/reader_appeal.py",
    "reader_appeal.eval_design": "core/quality/scoring/reader_appeal.py",
    "reader_appeal.eval_intent": "core/quality/scoring/reader_appeal.py",
    "reader_appeal.eval_prev": "core/quality/scoring/reader_appeal.py",
    "reader_appeal.eval_setting_canon": "core/quality/scoring/reader_appeal.py",
    "reader_appeal.gate_chapter.context": "core/quality/scoring/reader_appeal.py",
    "reader_appeal.gate_first_chapters.context": "core/quality/scoring/reader_appeal.py",
    "reader_appeal.gate_first_chapters.recheck": "core/quality/scoring/reader_appeal.py",
    "reader_appeal.recheck_borderline": "core/quality/scoring/reader_appeal.py",
    "reader_appeal.score_chapter.retry": "core/quality/scoring/reader_appeal.py",
    "rewrite.pref.accumulate": "core/quality/rewrite/feedback_rewriter.py",
    "rollback_budget.as_int": "core/quality/rollback_budget.py",
    "rollback_budget.bump.save": "core/quality/rollback_budget.py",
    "rollback_budget.save.fallback": "core/quality/rollback_budget.py",
    "rollback_budget.save.mkdir": "core/quality/rollback_budget.py",
    "rollback_budget.save.replace": "core/quality/rollback_budget.py",
    "rollback_budget.load": "core/quality/rollback_budget.py",
    "rollback_budget.mark_ledger.save": "core/quality/rollback_budget.py",
    "rollback_budget.persist_failure_notify": "core/quality/rollback_budget.py",
    "rollback_budget.reset.save": "core/quality/rollback_budget.py",
    "setting_canon.load": "core/story/setting_canon.py",
    "setting_canon.load.entry": "core/story/setting_canon.py",
    "setting_canon.world_md.read": "core/story/setting_canon.py",
    "subline_curve.sync": "core/story/subline_curve.py",
    "task_queue.clear_stop_flag": "daemon/task_queue.py",
    "task_queue.finalize": "daemon/task_queue.py",
    "unlock.quarantine": "cli/commands/unlock.py",
}

# ── 前缀命名空间（f-string 动态段）→ 归属模块 ──────────────────────
# 用于 ``degrade(f"<prefix>.{var}", ...)`` 这类动态命名空间。
DEGRADE_NAMESPACE_PREFIXES: dict[str, str] = {
    "chapter_invalidation": "core/story/chapter_invalidation.py",
    "evaluator.score_fn": "agents/evaluator_metrics.py",
    "quality_checker.check_dimension": "core/quality/scoring/quality_checker.py",
}


def is_registered(namespace: str) -> bool:
    """命名空间是否已登记（精确或前缀匹配）。"""
    if namespace in DEGRADE_NAMESPACES:
        return True
    return any(
        namespace == p or namespace.startswith(p + ".") for p in DEGRADE_NAMESPACE_PREFIXES
    )


def owner_of(namespace: str) -> str | None:
    """返回命名空间的归属模块（相对 src/agent）；未登记返回 None。"""
    if namespace in DEGRADE_NAMESPACES:
        return DEGRADE_NAMESPACES[namespace]
    for p, owner in DEGRADE_NAMESPACE_PREFIXES.items():
        if namespace == p or namespace.startswith(p + "."):
            return owner
    return None


def is_valid_namespace(namespace: str) -> bool:
    """命名空间是否符合语法规范。"""
    return bool(NAMESPACE_PATTERN.match(namespace))
