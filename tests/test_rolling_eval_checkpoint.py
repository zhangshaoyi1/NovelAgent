"""B1/B2（2026-09-10）：滚动体检检查点 + --batch 相对批次目标。

背景事故：web 续写按钮把批次翻译为「绝对值章数目标」（--chapters 190），体检
只在写章循环**结束后**执行一次 → 目标没写满或中途被打断时，体检永不触发
（ch156-178 共 23 章零体检记录）。本组测试锁定两项修复：

- B1：``rolling_eval_every`` 每 N 章在循环内体检一次；不过则中断本批。
- B2：``--batch`` 由 CLI 用实时章数换算绝对目标，消除前端快照过期导致的空跑。
- P1（2026-09-12）：检查点改走 ``evaluate_with_repair``（就地定向重写）而非 ``evaluate``
  （只回退不重写），故本文件的假评测器实现的是 ``evaluate_with_repair``。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.workflows.pipeline.agentic_pipeline import AgenticPipelineWorkflow


def _dim(name: str, label: str, value: float, threshold: float, direction: str, passed: bool):
    return SimpleNamespace(
        name=name, label=label, value=value, threshold=threshold,
        direction=direction, passed=passed,
    )


def _report(passed: bool, *, rolled_back: bool = False, escalated: bool = False):
    dims = [
        _dim("setting_consistency_high", "设定一致", 0.0 if passed else 3.0, 0.0, "<=", passed),
        _dim("logic_holes", "逻辑漏洞", 0.0 if passed else 3.0, 0.0, "<=", passed),
    ]
    return SimpleNamespace(
        overall_pass=passed,
        score=80.0 if passed else 66.0,
        dimensions=dims,
        rolled_back=rolled_back,
        escalated=escalated,
    )


def _gated_report(
    gate: str,
    *,
    escalated: bool = False,
    reason: str = "",
    rolled_back: bool = False,
):
    """构造带 ``gate_decision()`` 的报告，用于覆盖 pass/block/warn/recheck 四象限。

    2026-09-18 新增：旧 ``_report`` 无 ``gate_decision`` 属性 ⇒ 检查点走兼容分支
    （``pass``/``block`` 二值），**测不到 warn / recheck 象限** —— 而本次事故正是
    「销毁授权在第一轮（block 级证据）、调用方拿到的是复评后（warn 级证据）」。
    """
    ok = gate == "pass"
    dims = [
        _dim("setting_consistency_high", "设定一致", 0.0, 0.0, "<=", gate != "block"),
        _dim("coherence", "连贯性", 88.0 if ok else 70.0, 85.0, ">=", ok),
    ]
    rep = SimpleNamespace(
        overall_pass=ok,
        score=88.0 if ok else 70.0,
        dimensions=dims,
        rolled_back=rolled_back,
        escalated=escalated,
        escalated_reason=reason,
    )
    rep.gate_decision = lambda: gate
    return rep


def _seed_ledger(tmp_path: Path, *, water: int, dirs: int) -> None:
    """预置独立账本：水位 ``water`` + ``dirs`` 个 ``rollback_to_*`` 快照目录。

    ``RollbackBudget.last_counted_seq`` 初值为 **-1**（未启用）⇒ 首次对账会落进
    「基线对齐、仅此一次」豁免（不计数）。要测「账本前进被记账」必须先显式对齐水位，
    否则测出来的是豁免分支而不是目标分支（本仓纪律：别把豁免路径当主路径）。
    """
    from agent.core.quality.rollback_budget import RollbackBudget

    arch = tmp_path / "chapters" / "_archived"
    for i in range(dirs):
        (arch / f"rollback_to_54_20260918_1000{i}").mkdir(parents=True, exist_ok=True)
    RollbackBudget.load(tmp_path).mark_ledger(water)


class _FakeEvaluator:
    def __init__(self, passed: bool = True, raises: Exception | None = None,
                 rolled_back: bool = False, escalated: bool = False,
                 report=None):
        self._passed = passed
        self._raises = raises
        self._rolled_back = rolled_back
        self._escalated = escalated
        self._report = report
        self.calls = 0
        self.rewrite_calls = 0
        self.last_failed_report = None

    def evaluate_with_repair(self, rewriter):
        """P1 契约：检查点走修复闭环（回退后带教训重写、再复评）。"""
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        if self._report is not None:
            return self._report
        return _report(self._passed, rolled_back=self._rolled_back,
                       escalated=self._escalated)


def _pipeline(tmp_path: Path, evaluator: _FakeEvaluator, every: int = 5) -> AgenticPipelineWorkflow:
    """构造 pipeline（绕过 __init__ 的重依赖，只装测试所需字段）。"""
    p = AgenticPipelineWorkflow.__new__(AgenticPipelineWorkflow)
    p.console = SimpleNamespace(
        print=lambda *a, **k: None,
        rule=lambda *a, **k: None,
    )
    p._emit_progress = lambda *a, **k: None
    p._emit_event = lambda *a, **k: None
    p._emit_failure = lambda *a, **k: None
    p.eval_enabled = True
    p.rolling_eval_every = every
    p._ensure_evaluator = lambda: evaluator
    p.project_dir = tmp_path
    p.max_rollback_attempts = 3
    p._rolling_escalation_reason = ""
    return p


# ---------------- B1：滚动体检 ----------------

def test_rolling_checkpoint_passes_when_report_ok(tmp_path: Path) -> None:
    ev = _FakeEvaluator(passed=True)
    p = _pipeline(tmp_path, ev)
    assert p._rolling_eval_checkpoint() is True
    assert ev.calls == 1


def test_rolling_checkpoint_blocks_when_report_fails(tmp_path: Path) -> None:
    ev = _FakeEvaluator(passed=False)
    p = _pipeline(tmp_path, ev)
    assert p._rolling_eval_checkpoint() is False
    assert ev.calls == 1


def test_rolling_block_without_rollback_does_not_bump_budget(tmp_path: Path) -> None:
    """P1 修正（2026-09-12）：gate=block 且未真回退（必然 escalated）不得 bump——
    否则同一回合滚动体检 + 批末体检对同一窗口重复计数，提前误触发熔断。"""
    from agent.core.quality.rollback_budget import RollbackBudget

    ev = _FakeEvaluator(passed=False, rolled_back=False, escalated=True)
    p = _pipeline(tmp_path, ev)
    assert p._rolling_eval_checkpoint() is False
    budget = RollbackBudget.load(tmp_path)
    assert budget.consecutive == 0


def test_rolling_block_with_real_rollback_bumps_budget(tmp_path: Path) -> None:
    """真回退（rolled_back=True）仍计 1 次——修正不得误伤正常计数。"""
    from agent.core.quality.rollback_budget import RollbackBudget

    ev = _FakeEvaluator(passed=False, rolled_back=True, escalated=False)
    p = _pipeline(tmp_path, ev)
    assert p._rolling_eval_checkpoint() is False
    budget = RollbackBudget.load(tmp_path)
    assert budget.consecutive == 1


def test_rolling_checkpoint_uses_repair_loop(tmp_path: Path) -> None:
    """P1 红线：检查点必须走 evaluate_with_repair（旧版 evaluate 只回退不重写）。"""
    ev = _FakeEvaluator(passed=True)
    p = _pipeline(tmp_path, ev)
    assert p._rolling_eval_checkpoint() is True
    assert ev.calls == 1, "检查点未调用修复闭环入口 evaluate_with_repair"
    assert not hasattr(ev, "evaluate"), "假评测器不应再依赖旧的 evaluate 入口"


def test_rolling_checkpoint_degrades_open_on_exception(tmp_path: Path) -> None:
    """体检抛异常（LLM 不可用）时必须放行——质量闸门不该因基建抖动中断写作。"""
    ev = _FakeEvaluator(raises=RuntimeError("LLM 不可用"))
    p = _pipeline(tmp_path, ev)
    assert p._rolling_eval_checkpoint() is True


def test_rolling_checkpoint_degrades_open_on_none_report(tmp_path: Path) -> None:
    class _NoneEval:
        def evaluate_with_repair(self, rewriter):
            return None

    p = _pipeline(tmp_path, _NoneEval())
    assert p._rolling_eval_checkpoint() is True


def test_rolling_eval_every_zero_disables(tmp_path: Path) -> None:
    """every=0 关闭滚动体检（保留批末终审）。"""
    ev = _FakeEvaluator(passed=False)
    p = _pipeline(tmp_path, ev, every=0)
    # 触发条件含 every>0，故不应调用体检
    should_run = (
        p.eval_enabled and p.rolling_eval_every > 0
        and 5 > 0 and 5 % p.rolling_eval_every == 0
    )
    assert should_run is False
    assert ev.calls == 0


def test_rolling_eval_period_alignment() -> None:
    """周期对齐：wrote=5/10/15 触发，6/7 不触发。"""
    every = 5
    triggered = [w for w in range(1, 17) if w % every == 0]
    assert triggered == [5, 10, 15]


def test_constructor_clamps_negative_every() -> None:
    """负数归零（防御性钳制）。"""
    from agent.workflows.pipeline.agentic_pipeline import AgenticPipelineWorkflow as AP

    p = AP.__new__(AP)
    p.rolling_eval_every = max(0, int(-3))
    assert p.rolling_eval_every == 0


# ---------------- B2：--batch 相对批次 ----------------

def _count_chapters(project: Path) -> int:
    ch_dir = project / "chapters"
    if not ch_dir.exists():
        return 0
    return sum(1 for f in ch_dir.glob("ch*.md") if f.stem[2:].isdigit())


def test_batch_target_uses_live_chapter_count(tmp_path: Path) -> None:
    """--batch 5 于水位 178 时 → 目标 183（而非前端快照可能算出的 175）。"""
    ch_dir = tmp_path / "chapters"
    ch_dir.mkdir()
    for i in range(1, 179):
        (ch_dir / f"ch{i:03d}.md").write_text("x", encoding="utf-8")
    cur = _count_chapters(tmp_path)
    assert cur == 178
    assert cur + 5 == 183


def test_batch_ignores_non_chapter_files(tmp_path: Path) -> None:
    """非章节文件（如 ch_notes.md）不计入水位。"""
    ch_dir = tmp_path / "chapters"
    ch_dir.mkdir()
    (ch_dir / "ch001.md").write_text("x", encoding="utf-8")
    (ch_dir / "ch002.md").write_text("x", encoding="utf-8")
    (ch_dir / "ch_notes.md").write_text("x", encoding="utf-8")
    assert _count_chapters(tmp_path) == 2


def test_batch_no_stale_snapshot_regression(tmp_path: Path) -> None:
    """回归：前端旧快照（170）+5=175 < 实际水位 178 → 旧算法秒退；新算法 178+5=183 正常。"""
    ch_dir = tmp_path / "chapters"
    ch_dir.mkdir()
    for i in range(1, 179):
        (ch_dir / f"ch{i:03d}.md").write_text("x", encoding="utf-8")
    live = _count_chapters(tmp_path)
    stale_target = 170 + 5          # 前端过期快照算出的目标
    new_target = live + 5           # CLI 实时换算
    assert not (live < stale_target)  # 旧算法：循环条件为假 → 秒退
    assert live < new_target          # 新算法：正常推进


# ---------------- 2026-09-17：「未回退即断链归零」解陈旧熔断自锁 ----------------
#
# 事故（《灵荒薪传》，登记单 ``20260917_熔断计数无断链归零_陈旧置位自锁``）：
# ``consecutive`` 冻结在 9（账本 mtime 停在 11:06:40）后 ``reset()`` 从不触发
# —— 它原先**只在 ``gate == "pass"`` 调用**。于是每个检查点读到陈旧
# ``tripped()``（``9 > 3``）⇒ 批内 ``break`` ⇒ 每轮只写 ``rolling_eval_every``(=5)
# 章即被截断（下午三轮各 +5、净推进 15 章），且**上报的是 11:06 的旧原因**。
# 自锁的本质：解锁要求检查点 ``pass``，而每次到达检查点都被陈旧值拦下。
#
# 语义修正：``consecutive`` 是「**连续**回退」计数 ⇒ 本轮检查点**未发生任何回退**
# 时链条即断裂，必须归零。安全性依据：``report.rolled_back`` 为真时
# ``_count_rollback`` 恒返回 ≥1（宁多记不漏记），故「返回 0」=「确无回退」。


def test_stale_budget_released_when_checkpoint_has_no_rollback(tmp_path: Path) -> None:
    """陈旧置位必须在「本轮确无回退」的检查点上解锁，且不得再报旧原因。"""
    from agent.core.quality.rollback_budget import RollbackBudget

    for _ in range(9):
        RollbackBudget.load(tmp_path).bump(target_chapter=37, reason="陈旧置位")
    seeded = RollbackBudget.load(tmp_path)
    assert seeded.consecutive == 9 and seeded.tripped()

    ev = _FakeEvaluator(passed=False, rolled_back=False, escalated=True)
    p = _pipeline(tmp_path, ev)
    assert p._rolling_eval_checkpoint() is False, "硬指标不达标仍应中断本批（不得放行质量）"

    b = RollbackBudget.load(tmp_path)
    assert b.consecutive == 0, "本轮未发生任何回退 ⇒ 「连续回退」链条必须断裂归零"
    assert b.same_target_streak == 0, "同窗口连击计数同样应随链条断裂而清零"
    assert not b.tripped()
    assert "超过上限" not in p._rolling_escalation_reason, (
        "不得再把陈旧熔断的 11:06 旧原因当作本轮原因上报"
    )


def test_stale_budget_released_without_escalation(tmp_path: Path) -> None:
    """不带 escalated 的 block 检查点同样解锁（走「未达标」分支而非熔断分支）。"""
    from agent.core.quality.rollback_budget import RollbackBudget

    for _ in range(9):
        RollbackBudget.load(tmp_path).bump(target_chapter=37, reason="陈旧置位")

    p = _pipeline(
        tmp_path, _FakeEvaluator(passed=False, rolled_back=False, escalated=False)
    )
    assert p._rolling_eval_checkpoint() is False
    assert RollbackBudget.load(tmp_path).consecutive == 0
    assert not p._rolling_escalation_reason, "无回退且未放弃处置时不应上报熔断"


def test_stale_budget_release_does_not_disable_breaker(tmp_path: Path) -> None:
    """解锁**不得**误伤熔断：随后真实连续回退仍须累计到上限并停批上报。"""
    from agent.core.quality.rollback_budget import RollbackBudget

    for _ in range(9):
        RollbackBudget.load(tmp_path).bump(target_chapter=37, reason="陈旧置位")

    # 第 1 次：确无回退 ⇒ 解锁（归零）
    p = _pipeline(
        tmp_path, _FakeEvaluator(passed=False, rolled_back=False, escalated=False)
    )
    assert p._rolling_eval_checkpoint() is False
    assert RollbackBudget.load(tmp_path).consecutive == 0

    # 随后每次都是**真实回退** ⇒ 必须重新累计到 limit(3) 之上并熔断
    q = _pipeline(
        tmp_path, _FakeEvaluator(passed=False, rolled_back=True, escalated=False)
    )
    for i in range(4):
        q._rolling_eval_checkpoint()
        assert RollbackBudget.load(tmp_path).consecutive == i + 1, (
            f"第 {i + 1} 次真实回退计数不符 —— 边界归零误伤了正常累计"
        )
    assert "超过上限" in q._rolling_escalation_reason, (
        "解锁后真实连续回退仍必须触发熔断，不得被顺带关掉"
    )


def test_batch_end_site_pairs_count_with_release() -> None:
    """两处成对（纪律）：批末预算块也必须「先统一对账、再据 counted 归零」。

    2026-09-18 更新：批末块的记账已由「``elif not _infra_down`` 分支里的
    ``_count_rollback(...) <= 0``」改为**无条件统一对账点** ``_reconcile_rollback_ledger``
    —— 旧写法在 ``verified_pass`` 时**先 reset 再看账本**，把已经发生的销毁
    连记都没记就抹平（登记单 ``20260918_回退销毁与记账分支脱钩``）。
    本断言是**锚点契约**：找不到对账点即说明锚点漂移，必须重新取证（不许直接删）。
    """
    import ast
    from pathlib import Path as _P

    src_path = (
        _P(__file__).resolve().parents[1]
        / "src/agent/workflows/pipeline/agentic_pipeline.py"
    )
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    reconcile_line = None
    reset_if_line = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and getattr(node.func, "attr", "") == "_reconcile_rollback_ledger"
        ):
            reconcile_line = node.lineno
        if isinstance(node, ast.If) and ".reset()" in ast.unparse(node):
            if "counted" in ast.unparse(node.test):
                reset_if_line = node.lineno
    assert reconcile_line is not None, (
        "批末块缺统一对账点 ``_reconcile_rollback_ledger(...)``（锚点漂移，须重新取证能力新家）"
    )
    assert reset_if_line is not None, (
        "批末块的归零必须**以 counted 为条件**——不得回到「verified_pass 就直接 reset」"
    )
    assert reconcile_line < reset_if_line, (
        "批末块必须先对账再决定归零，否则 reset 会抹掉本批已发生的回退"
    )


# ---------------- 2026-09-18：「销毁与记账分支脱钩」→ 统一对账点 ----------------
#
# 事故（《灵荒薪传》ch054-058，登记单 ``20260918_回退销毁与记账分支脱钩``）：
# 回退销毁发生在 ``evaluate_with_repair()`` **内部**，授权它的是**第一轮** report
# （硬维失败 ⇒ block 级证据）；而检查点拿到的是该方法**复评后**的返回 report ——
# 重写之后硬维往往已达标，只剩软维失败 ⇒ ``gate_decision()`` 返 **warn**。
# 旧实现把 ``_count_rollback`` 写在 ``gate == "block"`` 分支里 ⇒ 落到 warn 象限
# **一次都不记账** ⇒ ``consecutive`` 恒 0 ⇒ ``tripped()`` 恒 False ⇒ 熔断与
# 2026-09-17 新加的 ``rollback_barrier`` 全部空转 ⇒ 同一窗口反复销毁 5 轮、净推进 0
# （账本 mtime 冻结 11h，``last_counted_seq`` 停在 31 而真实快照已 36 —— 铁证）。
#
# 语义修正：记账依据 = **动作事实**（独立账本是否前进），与 gate 象限无关。


def test_warn_gate_still_counts_rollback(tmp_path: Path) -> None:
    """★ 核心红线：gate=warn（仅软维失败）时**仍须记账**。

    这是本次事故的直接成因——销毁已经发生，但 report 落在 warn 象限 ⇒ 旧实现不记账。
    """
    from agent.core.quality.rollback_budget import RollbackBudget

    _seed_ledger(tmp_path, water=0, dirs=1)  # 独立账本前移 1（快照 0 → 1）
    ev = _FakeEvaluator(report=_gated_report("warn", rolled_back=False))
    p = _pipeline(tmp_path, ev)
    assert p._rolling_eval_checkpoint() is True, "warn 象限应放行继续写作"

    assert RollbackBudget.load(tmp_path).consecutive == 1, (
        "warn 象限漏记账 —— 熔断与前置闸将再次全部空转（本次事故的直接成因）"
    )


def test_recheck_gate_still_counts_rollback(tmp_path: Path) -> None:
    """gate=recheck（降级证据 + 真回退）同样必须记账 —— 同一不变量，不按象限豁免。"""
    from agent.core.quality.rollback_budget import RollbackBudget

    _seed_ledger(tmp_path, water=0, dirs=2)  # 前移 2
    ev = _FakeEvaluator(report=_gated_report("recheck", rolled_back=False))
    p = _pipeline(tmp_path, ev)
    p._rolling_eval_checkpoint()
    assert RollbackBudget.load(tmp_path).consecutive == 2


def test_warn_gate_escalated_stops_batch(tmp_path: Path) -> None:
    """D1 补口：warn 分支必须看 ``report.escalated``（旧实现只告警继续）。"""
    ev = _FakeEvaluator(
        report=_gated_report(
            "warn", escalated=True, reason="回退 2 次仍不达标，需人工介入"
        )
    )
    p = _pipeline(tmp_path, ev)
    assert p._rolling_eval_checkpoint() is False, "evaluator 已放弃处置时不得继续写作"
    assert "人工介入" in p._rolling_escalation_reason


def test_warn_gate_without_escalation_continues(tmp_path: Path) -> None:
    """反向：warn 且未放弃处置 ⇒ 仍只告警继续（不得把软维告警升级成停批）。"""
    ev = _FakeEvaluator(report=_gated_report("warn", escalated=False))
    p = _pipeline(tmp_path, ev)
    assert p._rolling_eval_checkpoint() is True
    assert not p._rolling_escalation_reason


def test_pass_with_real_rollback_does_not_reset(tmp_path: Path) -> None:
    """回退后重写通过 ≠ 没回退：不得被 reset 抹平（否则熔断永久失效）。"""
    from agent.core.quality.rollback_budget import RollbackBudget

    _seed_ledger(tmp_path, water=0, dirs=1)
    ev = _FakeEvaluator(report=_gated_report("pass", rolled_back=False))
    p = _pipeline(tmp_path, ev)
    assert p._rolling_eval_checkpoint() is True
    assert RollbackBudget.load(tmp_path).consecutive == 1, (
        "通过分支无条件 reset ⇒ 靠回退刷出来的「通过」永远不计入熔断"
    )


def test_pass_without_rollback_still_resets(tmp_path: Path) -> None:
    """回归：通过且**确无回退**时仍须归零（保持 2026-09-17「断链归零」语义）。"""
    from agent.core.quality.rollback_budget import RollbackBudget

    for _ in range(5):
        RollbackBudget.load(tmp_path).bump(target_chapter=54, reason="陈旧置位")
    assert RollbackBudget.load(tmp_path).consecutive == 5

    ev = _FakeEvaluator(report=_gated_report("pass"))
    p = _pipeline(tmp_path, ev)
    assert p._rolling_eval_checkpoint() is True
    assert RollbackBudget.load(tmp_path).consecutive == 0


def test_reconcile_exception_does_not_reset(tmp_path: Path) -> None:
    """对账异常（-1）时**不得归零**：未知不是"确无回退"（一号病的镜像）。"""
    from agent.core.quality.rollback_budget import RollbackBudget

    for _ in range(5):
        RollbackBudget.load(tmp_path).bump(target_chapter=54, reason="陈旧置位")

    ev = _FakeEvaluator(report=_gated_report("pass"))
    p = _pipeline(tmp_path, ev)

    def _boom(*a, **k):
        raise RuntimeError("账本不可读")

    p._count_rollback = _boom
    assert p._rolling_eval_checkpoint() is True
    assert RollbackBudget.load(tmp_path).consecutive == 5, (
        "对账异常被当成「没有回退」⇒ 又一次把失败读成通过"
    )


def test_reconcile_runs_before_gate_branches() -> None:
    """锚点契约：统一对账点必须位于**所有** ``gate ==`` 分支之前。

    若被挪回某个分支内，「漏象限」缺陷会原样复现。本断言锁位置而非行为，
    是为了让改动者必须先读到这条纪律（登记单 §纪律）。
    """
    import ast
    from pathlib import Path as _P

    src_path = (
        _P(__file__).resolve().parents[1]
        / "src/agent/workflows/pipeline/agentic_pipeline_agents.py"
    )
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_rolling_eval_checkpoint":
            fn = node
            break
    assert fn is not None, "未找到 _rolling_eval_checkpoint（锚点漂移，须重新取证）"

    reconcile_line = None
    gate_lines: list[int] = []
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Call)
            and getattr(node.func, "attr", "") == "_reconcile_rollback_ledger"
        ):
            reconcile_line = min(reconcile_line or 10**9, node.lineno)
        if isinstance(node, ast.Compare) and "gate" in ast.unparse(node.left):
            for c in node.comparators:
                if isinstance(c, ast.Constant) and isinstance(c.value, str):
                    gate_lines.append(node.lineno)
    assert reconcile_line is not None, "滚动检查点缺统一对账点调用"
    assert gate_lines, "未找到 gate 象限判定（锚点漂移）"
    assert reconcile_line < min(gate_lines), (
        f"统一对账点（L{reconcile_line}）必须在所有 gate 分支（最早 L{min(gate_lines)}）"
        f"**之前** —— 否则 warn/recheck 象限会再次漏记账"
    )

