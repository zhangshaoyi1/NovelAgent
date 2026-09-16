"""闸门信号可达性红线（登记单 ``20260916_闸门信号可达性普查``）。

守卫三类缺陷：
- **A1/A2（置位即短路）**：滚动体检熔断 / 门禁失明熔断与 token 预算熔断一样，
  必须在批末评测段之前就把控制流短路，而不是等到 run() 收尾才折入结果对象。
- **B1（护栏状态跨运行存活）**：``_gate_blind_streak`` / ``_consecutive_flagged``
  必须落盘 —— 旧实现存在 pipeline 实例属性上，而 pipeline 每次运行新建，
  于是"连续 3 次即熔断"退化为"单次运行内连续 3 次"，跨运行永不累计。
- **C1（写时质检失明入账）**：``agentic_write`` 写的 ``gate_skipped`` 标记必须
  被批内扫描计入失明熔断，否则最频繁的门禁环节失明无人知。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from agent.core.infra.degrade import degrade  # noqa: F401 - 保证命名空间可导入
from agent.workflows.pipeline.agentic_pipeline_events import (
    GATE_BLIND_FILE,
    GATE_ESCALATION_LIMIT,
    _PipelineEventsMixin,
)

_PIPELINE_SRC = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "agent"
    / "workflows"
    / "pipeline"
    / "agentic_pipeline.py"
)


class _Console:
    def print(self, *args, **kwargs):  # noqa: D102 - 静音
        pass


class _Harness(_PipelineEventsMixin):
    """最小 mixin 宿主：模拟"一次运行"的 pipeline 实例。"""

    def __init__(self, project_dir):
        self.project_dir = Path(project_dir)
        self.console = _Console()
        self._failures: list[tuple[str, str, str]] = []
        self._gate_escalation_reason = ""
        self._gate_blind_streak = 0
        self._gate_write_gate_streak = 0
        self._consecutive_flagged = 0
        self._quality_flags: list[dict] = []

    def _emit_failure(self, step: str, reason: str, severity: str = "error") -> None:
        self._failures.append((step, reason, severity))


def _state(project_dir) -> dict:
    path = Path(project_dir) / GATE_BLIND_FILE
    assert path.exists(), "护栏状态文件必须落盘"
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- A1 / A2：置位即短路
def _run_source() -> str:
    text = _PIPELINE_SRC.read_text(encoding="utf-8")
    tree = ast.parse(text)
    run_fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "run"
    )
    return ast.get_source_segment(text, run_fn) or ""


def test_batch_eval_short_circuits_on_every_trip_signal() -> None:
    """批末 ``evaluate_with_repair`` 必须位于三合一熔断守卫的 return 之后。"""
    src = _run_source()
    assert "if _trip_reason:" in src, "批末评测段缺少统一的熔断守卫（_trip_reason）"
    guard = src.index("if _trip_reason:")
    for signal in (
        "result.tripped",
        "self._rolling_escalation_reason",
        "self._gate_escalation_reason",
    ):
        assert signal in src[:guard], f"{signal} 未参与熔断守卫判定（漏检 ⇒ 白跑批末评测）"
    eval_at = src.index("evaluate_with_repair", guard)
    return_at = src.index("return result", guard)
    assert return_at < eval_at, (
        "熔断置位后必须先 return 再考虑批末评测；"
        "否则等于「判定被记录、但没有改变控制流」"
    )


def test_trip_guard_marks_escalated_for_non_budget_trips() -> None:
    """非预算类熔断（体检/门禁）在短路出口必须以 escalated 收尾，否则外层会再起一批。"""
    src = _run_source()
    guard = src.index("if _trip_reason:")
    block = src[guard : src.index("evaluate_with_repair", guard)]
    assert "result.escalated = True" in block
    assert "result.escalated_reason" in block


# ---------------------------------------------------------------- B1：跨运行存活
def test_gate_blind_streak_persists_across_runs(tmp_path) -> None:
    """两次独立运行各失明 2 次 ⇒ 第二次运行内即应熔断（修复前永不累计）。"""
    first = _Harness(tmp_path)
    for _ in range(GATE_ESCALATION_LIMIT - 1):
        first._note_gate_blind("editor_review", RuntimeError("boom"))
    assert first._gate_blind_streak == GATE_ESCALATION_LIMIT - 1
    assert first._gate_escalation_reason == "", "未达阈值不应熔断"
    assert _state(tmp_path)["blind_streak"] == GATE_ESCALATION_LIMIT - 1

    second = _Harness(tmp_path)  # 模拟"下一次 autowrite 运行"（新实例）
    second._note_gate_blind("editor_review", RuntimeError("boom"))
    assert second._gate_blind_streak == GATE_ESCALATION_LIMIT
    assert second._gate_escalation_reason, "跨运行累计后必须熔断"
    assert _state(tmp_path)["blind_streak"] == GATE_ESCALATION_LIMIT


def test_sub_threshold_blind_is_visible(tmp_path) -> None:
    """未达阈值也必须留痕（旧实现只有 logging，daemon 下不可取证）。"""
    h = _Harness(tmp_path)
    h._note_gate_blind("guardrails_gate", RuntimeError("x"))
    assert [f[0] for f in h._failures] == ["gate_blind"]
    assert h._failures[0][2] == "warn"


def test_gate_ok_resets_persisted_streak(tmp_path) -> None:
    """门禁正常工作 ⇒ 连击归零**并落盘**（否则跨运行累计只增不减）。"""
    h1 = _Harness(tmp_path)
    h1._note_gate_blind("editor_review", RuntimeError("boom"))
    h1._note_gate_ok()
    assert _state(tmp_path)["blind_streak"] == 0

    h2 = _Harness(tmp_path)
    h2._note_gate_blind("editor_review", RuntimeError("boom"))
    assert h2._gate_blind_streak == 1


def test_flagged_streak_persists_across_runs(tmp_path) -> None:
    """「连续告警留章」计数同样跨运行累计。"""
    h1 = _Harness(tmp_path)
    for ch in (1, 2):
        h1._flag_chapter_quality(ch, [{"message": "低质"}], "正文")
    assert h1._consecutive_flagged == 2
    assert h1._gate_escalation_reason == ""
    assert _state(tmp_path)["flagged_streak"] == 2

    h2 = _Harness(tmp_path)
    h2._flag_chapter_quality(3, [{"message": "低质"}], "正文")
    assert h2._consecutive_flagged == GATE_ESCALATION_LIMIT
    assert h2._gate_escalation_reason


# ---------------------------------------------------------------- C1：写时质检失明入账
def _write_flags(project_dir, chapter: int, violations: list[str]) -> None:
    path = Path(project_dir) / ".state" / "chapter_quality_flags.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8")).get("flags", [])
    existing.append({"chapter": chapter, "violations": violations, "flagged_at": "t"})
    path.write_text(json.dumps({"flags": existing}, ensure_ascii=False), encoding="utf-8")


def test_gate_skipped_is_counted_into_write_gate_streak(tmp_path) -> None:
    """``gate_skipped`` 必须计入失明计数（修复前该路径完全不入账）。

    计入**独立来源桶** ``write_gate_streak`` —— 它不能被"其它门禁正常"的
    章末清零点抹掉，否则连击恒为 1、永不熔断（写进代码时被本测试当场抓出）。
    """
    h = _Harness(tmp_path)
    _write_flags(tmp_path, 7, ["gate_skipped: 九项质检解析失败"])
    assert h._scan_gate_skipped(7) is True
    assert h._gate_write_gate_streak == 1
    assert h._gate_blind_streak == 0
    assert [f[0] for f in h._failures] == ["gate_blind"]


def test_gate_skipped_ignores_other_chapters_and_flags(tmp_path) -> None:
    """只对本章的 ``gate_skipped`` 前缀计数，别章与其他告警不误计。"""
    h = _Harness(tmp_path)
    _write_flags(tmp_path, 7, ["gate_skipped: 解析失败"])
    _write_flags(tmp_path, 8, ["命中 AI 腔词句「语气平静」"])
    assert h._scan_gate_skipped(8) is False
    assert h._scan_gate_skipped(9) is False
    assert h._gate_write_gate_streak == 0
    assert h._gate_blind_streak == 0


def test_three_gate_skipped_chapters_trip_the_breaker(tmp_path) -> None:
    """连续 3 章写时质检失明 ⇒ 停批；**中间其它门禁正常也不许清零**。"""
    h = _Harness(tmp_path)
    for ch in range(1, GATE_ESCALATION_LIMIT + 1):
        h._note_gate_ok()  # 其它门禁正常（章末必然发生）——不得抹掉写时质检失明
        _write_flags(tmp_path, ch, [f"gate_skipped: 第{ch}章解析失败"])
        assert h._scan_gate_skipped(ch) is True
    assert h._gate_escalation_reason
    assert "质检基建持续故障" in h._gate_escalation_reason


def test_write_gate_streak_resets_when_write_gate_recovers(tmp_path) -> None:
    """写时质检恢复（本章无 gate_skipped）⇒ 该来源连击清零（恢复即清零）。"""
    h = _Harness(tmp_path)
    _write_flags(tmp_path, 1, ["gate_skipped: x"])
    h._scan_gate_skipped(1)
    assert h._gate_write_gate_streak == 1
    assert h._scan_gate_skipped(2) is False  # 第 2 章写时质检正常
    assert h._gate_write_gate_streak == 0
    assert _state(tmp_path)["write_gate_streak"] == 0


def test_corrupt_state_file_degrades_without_crash(tmp_path) -> None:
    """护栏状态损坏 ⇒ 显性降级并从 0 起算，不得中断写作。"""
    path = Path(tmp_path) / GATE_BLIND_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ 不是合法 json", encoding="utf-8")
    h = _Harness(tmp_path)
    h._note_gate_blind("editor_review", RuntimeError("boom"))
    assert h._gate_blind_streak == 1


@pytest.mark.parametrize("bad", ["", "abc", None, 3.7])
def test_as_int_is_defensive(bad) -> None:
    assert _PipelineEventsMixin._as_int(bad) in (0, 3)


# ---------------------------------------------------------------- D1：同一证据同一动作
from agent.workflows.pipeline.agentic_pipeline_agents import (  # noqa: E402
    _PipelineAgentsMixin,
)


class _FakeReport:
    """最小体检报告替身（只备 ``_rolling_eval_checkpoint`` 消费的字段）。"""

    def __init__(self, gate: str, escalated: bool = False, reason: str = "") -> None:
        self.dimensions: list = []
        self.score = 50.0
        self.eval_infra_unavailable = False
        self.escalated = escalated
        self.escalated_reason = reason
        self._gate = gate

    def gate_decision(self) -> str:
        return self._gate


class _FakeEvaluator:
    def __init__(self, report: _FakeReport) -> None:
        self._report = report

    def evaluate_with_repair(self, rewriter):  # noqa: ANN001, ANN201
        return self._report


class _FakeBudget:
    consecutive = 0
    limit = 3

    def tripped(self) -> bool:
        return False

    def reset(self) -> None:
        pass

    def reason_text(self) -> str:
        return ""


class _AgentHarness(_PipelineAgentsMixin):
    """最小混入宿主：驱动 ``_rolling_eval_checkpoint`` 的闸门分支。"""

    def __init__(self, project_dir, report: _FakeReport) -> None:
        self.project_dir = Path(project_dir)
        self.console = _Console()
        self._report = report
        self._failures: list[tuple[str, str, str]] = []
        self._rolling_escalation_reason = ""
        self._gate_blind_streak = 0
        self._gate_write_gate_streak = 0
        self._consecutive_flagged = 0
        self._gate_escalation_reason = ""
        self._quality_flags: list[dict] = []

    def _emit_progress(self, *a, **k) -> None:  # noqa: ANN002, ANN003
        pass

    def _emit_event(self, *a, **k) -> None:  # noqa: ANN002, ANN003
        pass

    def _emit_failure(self, step: str, reason: str, severity: str = "error") -> None:
        self._failures.append((step, reason, severity))

    def _note_gate_ok(self) -> None:
        pass

    def _note_gate_blind(self, *a, **k) -> None:  # noqa: ANN002, ANN003
        pass

    def _ensure_evaluator(self):  # noqa: ANN201
        return _FakeEvaluator(self._report)

    def _make_rewriter(self):  # noqa: ANN201
        return lambda chapters: None

    def _rollback_budget(self) -> _FakeBudget:
        return _FakeBudget()


def test_rolling_recheck_with_evaluator_giveup_stops_batch(tmp_path) -> None:
    """D1：证据不可信**且复评未恢复**（``report.escalated``）⇒ 与批末同动作：停批上报。

    修复前该分支只告警继续 —— 而批末拿到的是**同一份** ``evaluate_with_repair``
    报告，会因 ``result.escalated = report.escalated`` 停批 ⇒ 同一证据、两种动作强度
    （登记单 ``20260916_闸门信号可达性普查`` §四.D1）。
    """
    h = _AgentHarness(
        tmp_path, _FakeReport("recheck", escalated=True, reason="复评后仍未恢复可信")
    )
    assert h._rolling_eval_checkpoint() is False, "evaluator 已放弃处置时必须停批"
    assert h._rolling_escalation_reason, "停批必须置位 _rolling_escalation_reason"
    assert h._failures[-1][0] == "eval"
    assert h._failures[-1][2] == "block"


def test_rolling_recheck_without_giveup_continues(tmp_path) -> None:
    """仅降级维、复评即恢复（``escalated=False``）⇒ 告警继续（与批末一致）。"""
    h = _AgentHarness(tmp_path, _FakeReport("recheck", escalated=False))
    assert h._rolling_eval_checkpoint() is True
    assert h._rolling_escalation_reason == ""
    assert h._failures[-1][2] == "warn"
