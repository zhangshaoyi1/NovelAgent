"""P1 回退预算 + 滚动体检「就地修复」回归测试（2026-09-12）。

背景：五灵破归档连续 31 次回退、同一章节窗口反复翻车，没有任何一条路径上报人工。
两个具体缺陷：

1. 滚动体检走 ``evaluate()``——**只回退、不重写**，修复被推给外层「再起一批盲写
   同样的 5 章」；
2. ``max_rollback_attempts`` 只活在单次 ``evaluate_with_repair`` 循环里，每批新建
   Evaluator 即归零，回退次数**不跨批**。

本测试把「跨批计数 → 连续超限 → escalated 上报人工」与「检查点必须走修复闭环」
钉成红线。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.core.quality.rollback_budget import RollbackBudget
from agent.workflows.pipeline.agentic_pipeline_agents import _PipelineAgentsMixin

# ---------------------------------------------------------------- 预算单元


def test_budget_counts_and_trips(tmp_path: Path) -> None:
    b = RollbackBudget.load(tmp_path, limit=3)
    assert b.consecutive == 0
    # 目标章**递增**：隔离 same_target 独立硬闸（见 test_budget_trips_on_same_target_streak），
    # 本用例单独验证「consecutive > limit」这一条款。
    for i, expected in enumerate((1, 2, 3), start=1):
        assert b.bump(target_chapter=170 + i, reason="硬指标不达标") == expected
    assert not b.tripped(), "等于上限不算熔断（> limit 才熔断）"
    assert b.bump(target_chapter=180, reason="硬指标不达标") == 4
    assert b.tripped(), "超过上限必须熔断"


def test_budget_trips_on_same_target_streak(tmp_path: Path) -> None:
    """独立硬闸（2026-09-15）：同一章节窗口连续回退 2 次即熔断，**不依赖 consecutive**。

    实测灵荒薪传 4 批 21 次开章全部锁死在 23–27 窗口、净增 0 章 ——
    同窗口重复翻车是死循环的直接特征，不该被"次数还没到上限"掩盖。
    """
    from agent.core.quality.rollback_budget import SAME_TARGET_LIMIT

    assert SAME_TARGET_LIMIT == 2
    b = RollbackBudget.load(tmp_path, limit=9)  # 上限远未到
    b.bump(target_chapter=23, reason="x")
    assert not b.tripped(), "首次回退不熔断"
    b.bump(target_chapter=23, reason="x")
    assert b.tripped(), "同一窗口连续 2 次回退必须熔断（独立硬闸）"
    assert b.consecutive == 2 and b.limit == 9, "与 consecutive 条款无关"
    assert "同一章节窗口" in b.trip_reason()


def test_budget_load_returns_shared_instance(tmp_path: Path) -> None:
    """进程内共享实例：落盘失败时计数仍单调推进（旧实现下一秒被旧值覆盖）。"""
    a = RollbackBudget.load(tmp_path, limit=3)
    a.bump(target_chapter=23, reason="x")
    b = RollbackBudget.load(tmp_path, limit=3)
    assert b is a, "load() 必须返回进程内共享实例"
    assert b.consecutive == 1
    # 显式 refresh 仍以磁盘为准（跨进程复核口径）
    assert RollbackBudget.load(tmp_path, limit=3, refresh=True).consecutive == 1


def test_budget_save_falls_back_when_replace_denied(tmp_path: Path, monkeypatch) -> None:
    """落盘加固：os.replace 被占用/拒绝时，重试 + 非原子兜底仍要写出（账实相符）。"""
    from agent.core.quality import rollback_budget as rb

    def _boom(*a, **k):
        raise PermissionError("[WinError 5] 拒绝访问")

    monkeypatch.setattr(rb.os, "replace", _boom)
    b = RollbackBudget.load(tmp_path, limit=3)
    b.bump(target_chapter=23, reason="x")
    assert b.path.exists(), "非原子兜底必须写出预算文件"
    assert RollbackBudget.load(tmp_path, limit=3, refresh=True).consecutive == 1


def test_budget_reset_after_pass(tmp_path: Path) -> None:
    b = RollbackBudget.load(tmp_path, limit=3)
    b.bump(target_chapter=180, reason="x")
    b.bump(target_chapter=180, reason="x")
    b.reset()
    assert b.consecutive == 0
    assert b.total == 2, "累计次数保留，供复盘"
    assert not b.tripped()


def test_budget_tracks_same_target_churn(tmp_path: Path) -> None:
    """同一章节窗口反复回退（死循环特征）要能被识别。"""
    b = RollbackBudget.load(tmp_path, limit=5)
    b.bump(target_chapter=181, reason="x")
    b.bump(target_chapter=181, reason="x")
    b.bump(target_chapter=181, reason="x")
    assert b.same_target_streak == 3
    assert "同一章节窗口" in b.reason_text()
    b.bump(target_chapter=175, reason="x")
    assert b.same_target_streak == 1, "换窗口后重新计数"


def test_budget_persists_across_instances(tmp_path: Path) -> None:
    """跨批生效的关键：计数落盘，新实例（新进程）读得到。"""
    RollbackBudget.load(tmp_path, limit=3).bump(target_chapter=180, reason="批末体检")
    again = RollbackBudget.load(tmp_path, limit=3, refresh=True)  # refresh = 模拟新进程读盘
    assert again.consecutive == 1
    assert again.last_target == 180
    assert again.path.exists()


def test_budget_broken_file_degrades(tmp_path: Path) -> None:
    p = tmp_path / ".state" / "rollback_budget.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{不是合法 json", encoding="utf-8")
    b = RollbackBudget.load(tmp_path, limit=3)
    assert b.consecutive == 0 and not b.tripped(), "损坏文件按未计数处理，不抛异常"


# ---------------------------------------------------------------- 检查点接线


class _Dim:
    def __init__(self, label: str, value: float, passed: bool, required: bool = True) -> None:
        self.label, self.value, self.passed, self.required = label, value, passed, required


class _Plan:
    def __init__(self, target_chapter: int) -> None:
        self.target_chapter = target_chapter


class _Report:
    def __init__(self, gate: str, escalated: bool = False, target: int = 180,
                 rolled_back: bool = True) -> None:
        self.dimensions = [_Dim("连贯性", 35.0, gate == "pass")]
        self.score = 67.83
        self._gate = gate
        self.escalated = escalated
        self.escalated_reason = "评测器已放弃自动处置" if escalated else ""
        self.repair = _Plan(target)
        # P1 修正（2026-09-12）：只有真实回退才 bump；默认 True 对应假评测器
        # 在 evaluate_with_repair 里确实触发了定向重写的场景。
        self.rolled_back = rolled_back

    def gate_decision(self) -> str:
        return self._gate


class _Writer:
    def __init__(self, sink: list[Any]) -> None:
        self._sink = sink

    def run(self, rewrite_hint: str = "", chapter_num: int | None = None) -> None:
        self._sink.append(rewrite_hint)


class _Evaluator:
    def __init__(self, report: _Report, sink: list[Any]) -> None:
        self._report = report
        self._sink = sink
        self.last_failed_report = None

    def evaluate_with_repair(self, rewriter: Any) -> _Report:
        self._sink.append("evaluate_with_repair")
        rewriter([self._report.repair.target_chapter])  # 模拟评测器内部触发定向重写
        return self._report


class _FakePipeline(_PipelineAgentsMixin):
    def __init__(self, project_dir: Path, report: _Report, limit: int = 3,
                 vary_target: bool = False) -> None:
        from rich.console import Console

        self.project_dir = project_dir
        self.console = Console()
        self.max_rollback_attempts = limit
        self._rolling_escalation_reason = ""
        # 门禁可观测性计数（2026-09-12）：检查点失明/复位回调依赖
        self._gate_blind_streak = 0
        self._consecutive_flagged = 0
        self._gate_escalation_reason = ""
        self.calls: list[Any] = []
        self.failures: list[tuple[str, str, str]] = []
        self._report = report
        self._vary_target = vary_target
        self._eval_calls = 0

    def _note_gate_blind(self, where: str, err: Exception) -> None:  # noqa: D102
        self._gate_blind_streak += 1

    def _note_gate_ok(self) -> None:  # noqa: D102
        self._gate_blind_streak = 0

    def _emit_progress(self, *a: Any, **k: Any) -> None:  # noqa: D102
        pass

    def _emit_event(self, *a: Any, **k: Any) -> None:  # noqa: D102
        pass

    def _emit_failure(self, kind: str, msg: str, severity: str = "warn") -> None:
        self.failures.append((kind, msg, severity))

    def _ensure_evaluator(self) -> Any:
        self._eval_calls += 1
        report = self._report
        if self._vary_target:
            # 每次体检换一个回退目标：隔离 same_target 独立硬闸，
            # 让「consecutive > limit」条款可被单独验证。
            report = _Report(
                self._report._gate, self._report.escalated,
                target=170 + self._eval_calls, rolled_back=self._report.rolled_back,
            )
        return _Evaluator(report, self.calls)

    def _ensure_writer(self) -> Any:
        return _Writer(self.calls)


def test_checkpoint_uses_repair_loop_not_plain_evaluate(tmp_path: Path) -> None:
    """检查点必须走 evaluate_with_repair（就地定向重写），不得只回退不重写。"""
    pipe = _FakePipeline(tmp_path, _Report("pass"))
    assert pipe._rolling_eval_checkpoint() is True
    assert "evaluate_with_repair" in pipe.calls, "检查点仍在用只回退不重写的 evaluate()"
    assert any(isinstance(c, str) and c != "evaluate_with_repair" for c in pipe.calls), (
        "重写回调未被调用 —— 修复闭环没接上"
    )


def test_checkpoint_pass_resets_budget(tmp_path: Path) -> None:
    RollbackBudget.load(tmp_path, limit=3).bump(target_chapter=180, reason="上一次")
    pipe = _FakePipeline(tmp_path, _Report("pass"))
    assert pipe._rolling_eval_checkpoint() is True
    assert RollbackBudget.load(tmp_path, limit=3).consecutive == 0


def test_checkpoint_block_breaks_batch_and_counts(tmp_path: Path) -> None:
    pipe = _FakePipeline(tmp_path, _Report("block"), limit=3)
    assert pipe._rolling_eval_checkpoint() is False, "不达标应中断本批"
    assert RollbackBudget.load(tmp_path, limit=3).consecutive == 1


def test_checkpoint_trips_and_escalates_after_limit(tmp_path: Path) -> None:
    """连续回退超上限 → 必须 escalated 上报人工，不再无限重试。

    目标章刻意**每次变化**（``vary_target``），以隔离同窗口独立硬闸，
    单独验证「consecutive > limit」这一条款。
    """
    pipe = _FakePipeline(tmp_path, _Report("block"), limit=3, vary_target=True)
    for i in range(4):
        assert pipe._rolling_eval_checkpoint() is False, f"第 {i + 1} 次应停批"
    assert pipe._rolling_escalation_reason, "熔断后必须记录上报告知人工的原因"
    assert any(sev == "block" for _k, _m, sev in pipe.failures), "熔断应以 block 级上报"
    assert "超过上限 3" in pipe._rolling_escalation_reason


def test_checkpoint_trips_on_same_window_within_limit(tmp_path: Path) -> None:
    """独立硬闸（2026-09-15）：同一窗口连续 2 次回退即停批，即使次数远未到上限。"""
    pipe = _FakePipeline(tmp_path, _Report("block", target=23), limit=9)
    assert pipe._rolling_eval_checkpoint() is False
    assert not pipe._rolling_escalation_reason, "首次回退还不该熔断"
    assert pipe._rolling_eval_checkpoint() is False
    assert "同一章节窗口" in pipe._rolling_escalation_reason, (
        "同窗口第 2 次回退必须触发独立硬闸（不依赖 consecutive>limit）"
    )


# ---------------------------------------------------------------- 独立账本对账


def _write_rollback_ledger(tmp_path: Path, *, target: int, snapshots: int) -> None:
    """造出「回退动作自己写下」的独立账本：归档快照目录 + state.progress。"""
    import json as _json

    arch = tmp_path / "chapters" / "_archived"
    arch.mkdir(parents=True, exist_ok=True)
    existing = sorted(p.name for p in arch.glob("rollback_to_*"))
    for i in range(len(existing), snapshots):
        (arch / f"rollback_to_{target}_2026091517{i:02d}00").mkdir(parents=True, exist_ok=True)
    st = tmp_path / ".state"
    st.mkdir(parents=True, exist_ok=True)
    (st / "state.json").write_text(
        _json.dumps({"state": "WRITING",
                     "progress": {"last_rollback_target": target}}),
        encoding="utf-8",
    )


def test_checkpoint_counts_via_independent_ledger_when_report_lies(tmp_path: Path) -> None:
    """R2 正面用例：报告 ``rolled_back=False``，但独立账本前进了 → 仍记账 + 显性上报。

    实测灵荒薪传 09-15 发生 5 次真实回退（``state.progress.last_rollback_at``
    = 17:10:04），而 ``rollback_budget.json`` 一次都没更新 ⇒ 熔断护栏从未生效。
    """
    pipe = _FakePipeline(tmp_path, _Report("block", rolled_back=False), limit=3)
    _write_rollback_ledger(tmp_path, target=23, snapshots=1)
    pipe._rolling_eval_checkpoint()
    assert RollbackBudget.load(tmp_path, limit=3, refresh=True).consecutive == 0, (
        "水位未初始化时只对齐基线（**仅此一次**，对应装机前的历史快照）"
    )

    _write_rollback_ledger(tmp_path, target=23, snapshots=2)  # 又回退了一次
    pipe._rolling_eval_checkpoint()
    assert RollbackBudget.load(tmp_path, limit=3, refresh=True).consecutive == 1, (
        "账本前进但报告未标记时，必须按独立账本补记"
    )
    assert any("记账不一致" in m for _k, m, _s in pipe.failures), (
        "两账本不一致必须显性上报（不得静默）"
    )


def test_new_instance_first_reconciliation_uses_ledger(tmp_path: Path) -> None:
    """★ 红线（登记单 ``20260916_独立账本首轮对齐豁免使熔断失效`` §六.3）：
    **新实例的首轮对账也必须使用账本差值**（水位跨运行持久化）。

    旧实现把水位留在 pipeline **实例属性**（``_rollback_ledger_seq``），而 pipeline
    每次 autowrite 运行新建 ⇒ 每次运行的首轮回退都落进「首次对账」豁免分支 ⇒
    独立账本 ``seq`` 从未被使用 ⇒ 判据退回 ``report.rolled_back`` 单点源。

    实测后果：灵荒薪传当日 **5 次回退全部指向第 27 章**、间隔约 30 分钟
    （= 5 个独立运行，每次至多 1 次回退 ⇒ 豁免恒命中），而账本停在
    ``consecutive=3 / same_target_streak=1`` ⇒ ``tripped()`` 恒 False ⇒
    每 30 分钟销毁 5 章且净增 0 章，无人干预。
    """
    from agent.core.quality import rollback_budget as rb

    # 运行 1（装机）：水位未初始化 ⇒ 仅此一次对齐基线
    p1 = _FakePipeline(tmp_path, _Report("block", rolled_back=False), limit=3)
    _write_rollback_ledger(tmp_path, target=27, snapshots=1)
    p1._rolling_eval_checkpoint()
    assert RollbackBudget.load(tmp_path, limit=3, refresh=True).last_counted_seq == 1, (
        "对账水位必须落盘（不得只留在实例内存）"
    )

    # 运行 2：**全新实例 + 全新进程**（清进程缓存模拟），报告仍说谎
    rb._CACHE.clear()
    p2 = _FakePipeline(tmp_path, _Report("block", rolled_back=False), limit=3)
    _write_rollback_ledger(tmp_path, target=27, snapshots=2)
    p2._rolling_eval_checkpoint()
    assert RollbackBudget.load(tmp_path, limit=3, refresh=True).consecutive == 1, (
        "新实例首轮对账不得忽略独立账本——否则每次运行至多 1 次回退 ⇒ "
        "豁免恒命中 ⇒ 熔断永不成立"
    )


def test_same_window_trips_across_runs(tmp_path: Path) -> None:
    """★ 行为级验收（登记单 §六.2）：**跨运行**的同窗口连续 2 次回退必须熔断。

    实测场景：5 次回退全指向第 27 章，却因 ``same_target_streak=1`` 从未熔断。
    本用例把它钉死：两个独立运行、同一目标章、上限远未到 ⇒ 第 2 次必须停批上报。
    """
    from agent.core.quality import rollback_budget as rb

    for i, snapshots in enumerate((1, 2), start=1):
        rb._CACHE.clear()  # 每个运行都是新进程
        pipe = _FakePipeline(tmp_path, _Report("block", target=27), limit=9)
        _write_rollback_ledger(tmp_path, target=27, snapshots=snapshots)
        stopped = pipe._rolling_eval_checkpoint() is False
        assert stopped, f"第 {i} 次回退应停批"
        if i == 2:
            assert pipe._rolling_escalation_reason, (
                "跨运行同窗口第 2 次回退必须触发熔断上报人工"
            )
            assert "同一章节窗口" in pipe._rolling_escalation_reason


def test_bump_unknown_target_keeps_streak(tmp_path: Path) -> None:
    """目标章取不到时**不得归零**同窗口计数（否则抹掉最强死循环信号）。

    实测：5 次回退全指向第 27 章而 ``same_target_streak`` 恒为 1 —— 至少有 2 次
    bump 时 target 取到 0，把 streak 归零了。
    """
    b = RollbackBudget.load(tmp_path, limit=9)
    b.bump(target_chapter=27, reason="x")
    b.bump(target_chapter=27, reason="x")
    assert b.same_target_streak == 2 and b.tripped()
    b.bump(target_chapter=0, reason="目标章取不到")
    assert b.same_target_streak == 2, "target 未知不构成「换了窗口」的证据"
    assert b.last_target == 27, "last_target 不得被 0 覆盖"


def test_bump_swap_window_resets_streak(tmp_path: Path) -> None:
    """换到**另一个已知**窗口才重新计数（原有语义不变）。"""
    b = RollbackBudget.load(tmp_path, limit=9)
    b.bump(target_chapter=27, reason="x")
    b.bump(target_chapter=27, reason="x")
    b.bump(target_chapter=31, reason="x")
    assert b.same_target_streak == 1 and b.last_target == 31


def test_persist_failure_is_reported_as_event(tmp_path: Path, monkeypatch) -> None:
    """★ 护栏落盘失败必须**可取证**（failure 事件），不能只留一行 logging。

    daemon stdout 为 0 字节 ⇒ 事后无法区分"没回退"与"记不上账"
    （登记单 ``20260916_独立账本首轮对齐豁免使熔断失效`` §二.R3 / §六.4）。
    """
    from agent.core.quality import rollback_budget as rb

    seen: list[str] = []
    b = RollbackBudget.load(tmp_path, limit=3)
    b.on_persist_failure = seen.append

    def _boom(*_a: Any, **_k: Any) -> None:
        raise PermissionError("[WinError 5] 拒绝访问")

    monkeypatch.setattr(rb.os, "replace", _boom)   # 原子替换失败
    monkeypatch.setattr(rb.Path, "write_text", _boom)  # 非原子兜底也失败

    assert b.save() is False
    assert seen, "落盘彻底失败必须回调上报（护栏失效可取证）"
    assert "落盘失败" in seen[0]


def test_checkpoint_no_count_when_no_rollback_anywhere(tmp_path: Path) -> None:
    """反向用例：两账本都没回退 → 不记账（防把 escalation 误当回退）。"""
    pipe = _FakePipeline(
        tmp_path, _Report("block", escalated=True, rolled_back=False), limit=9
    )
    pipe._rolling_eval_checkpoint()
    assert RollbackBudget.load(tmp_path, limit=9, refresh=True).consecutive == 0


def test_checkpoint_propagates_evaluator_escalation(tmp_path: Path) -> None:
    """评测器自身已放弃（escalated，未回退）→ 检查点不得当作「再试一次」，直接上报人工。"""
    pipe = _FakePipeline(tmp_path, _Report("block", escalated=True, rolled_back=False), limit=9)
    assert pipe._rolling_eval_checkpoint() is False
    assert pipe._rolling_escalation_reason
    assert "评测器已放弃" in pipe._rolling_escalation_reason
    # 未真回退不得计数（与 agentic_pipeline_agents 的新判据一致）
    assert RollbackBudget.load(tmp_path, limit=9).consecutive == 0


def test_checkpoint_warn_soft_dimension_continues(tmp_path: Path) -> None:
    """软维度告警不得中断写作（HA-Eval L4 语义，防回归）。"""
    pipe = _FakePipeline(tmp_path, _Report("warn"), limit=3)
    assert pipe._rolling_eval_checkpoint() is True
