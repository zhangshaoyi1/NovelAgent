"""质检失明分级红线（登记单 ``20260917_…`` 后续：20261001_信任链与叙事上限六项能力·子项 1）。

守卫四条验收标准：
- **V1.1 完全失明不静默通过**：十三项审稿主体一次未产出（``blind_level=full``）→
  章末扫描当章置熔断停批上报人工，不等连续 3 次连击。
- **V1.2 部分失明维持降级放行**：``blind_level=partial``（D 维/金三等附加维挂掉）
  → 只计连击，连续 3 次才熔断（单次不拦）。
- **V1.3 失明率一等指标**：批末统计 gate_skipped 章占比，落盘 + 审计快照 +
  超 20% 标记低置信交付（failure 事件）。
- **V1.4 维度契约**：``gate_blind_rate`` 必须在 dimension_registry 登记
  （RATIO_0_1 / LOWER_BETTER / COMPUTED），且 agentic_write 写入的 flag
  携带 ``blind_level`` 字段（分级信息不丢）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.workflows.pipeline.agentic_pipeline_events import (
    GATE_BLIND_FILE,
    _PipelineEventsMixin,
)


class _Console:
    def print(self, *args, **kwargs):  # noqa: D102 - 静音
        pass


class _Memory:
    def __init__(self) -> None:
        self.logs: list[tuple[str, str, dict]] = []

    def log(self, kind: str, title: str, data: dict) -> None:
        self.logs.append((kind, title, data))


class _Result:
    def __init__(self, chapters_written: int, final_chapter: int) -> None:
        self.chapters_written = chapters_written
        self.final_chapter = final_chapter


class _Harness(_PipelineEventsMixin):
    """最小 mixin 宿主（同 test_gate_signal_reachability 的夹具模式）。"""

    def __init__(self, project_dir):
        self.project_dir = Path(project_dir)
        self.console = _Console()
        self.memory = _Memory()
        self._failures: list[tuple[str, str, str]] = []
        self._gate_escalation_reason = ""
        self._gate_blind_streak = 0
        self._gate_write_gate_streak = 0
        self._consecutive_flagged = 0
        self._quality_flags: list[dict] = []

    def _emit_failure(self, step: str, reason: str, severity: str = "error") -> None:
        self._failures.append((step, reason, severity))


def _write_flags(project_dir: Path, flags: list[dict]) -> None:
    p = project_dir / ".state" / "chapter_quality_flags.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"flags": flags}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _flag(ch: int, level: str) -> dict:
    return {
        "chapter": ch,
        "violations": [f"gate_skipped: 测试失明（{level}）"],
        "blind_level": level,
    }


# ---------------------------------------------------------------- V1.1 完全失明立即熔断
def test_full_blind_escalates_immediately(tmp_path) -> None:
    h = _Harness(tmp_path)
    _write_flags(tmp_path, [_flag(7, "full")])
    assert h._scan_gate_skipped(7) is True
    assert h._gate_escalation_reason, "完全失明必须当章置熔断（连击=1 即停批）"
    assert "完全失明" in h._gate_escalation_reason


def test_full_blind_state_persisted(tmp_path) -> None:
    h = _Harness(tmp_path)
    _write_flags(tmp_path, [_flag(7, "full")])
    h._scan_gate_skipped(7)
    state = json.loads((tmp_path / GATE_BLIND_FILE).read_text(encoding="utf-8"))
    assert state["write_gate_streak"] == 1


# ---------------------------------------------------------------- V1.2 部分失明维持连击
def test_partial_blind_does_not_escalate_once(tmp_path) -> None:
    h = _Harness(tmp_path)
    _write_flags(tmp_path, [_flag(7, "partial")])
    assert h._scan_gate_skipped(7) is True
    assert not h._gate_escalation_reason, "部分失明单次不得熔断"


def test_partial_blind_escalates_after_limit(tmp_path) -> None:
    h = _Harness(tmp_path)
    for ch in (7, 8, 9):
        _write_flags(tmp_path, [_flag(ch, "partial")])
        h._scan_gate_skipped(ch)
    assert h._gate_escalation_reason, "部分失明连续 3 次必须熔断"


def test_legacy_flag_without_level_treated_as_partial(tmp_path) -> None:
    """旧 flag（无 blind_level 字段）按 partial 处理——向后兼容，不得误熔断。"""
    h = _Harness(tmp_path)
    _write_flags(tmp_path, [{
        "chapter": 7,
        "violations": ["gate_skipped: 旧格式"],
        "flagged_at": "2026-09-30T00:00:00",
    }])
    assert h._scan_gate_skipped(7) is True
    assert not h._gate_escalation_reason


# ---------------------------------------------------------------- V1.3 失明率一等指标
def test_blind_rate_marks_low_confidence(tmp_path) -> None:
    # 批次 ch1..ch5，其中 2 章失明（40% > 20%）→ 低置信交付
    _write_flags(tmp_path, [_flag(2, "partial"), _flag(4, "full")])
    h = _Harness(tmp_path)
    h._run_blind_rate_batch_end(_Result(chapters_written=5, final_chapter=5))
    state = json.loads((tmp_path / GATE_BLIND_FILE).read_text(encoding="utf-8"))
    assert state["low_confidence_delivery"] is True
    assert state["batch_blind_rate"] == pytest.approx(0.4)
    assert any(s == "low_confidence_delivery" for s, _, _ in h._failures)
    assert h.memory.logs, "低置信交付必须 memory 留痕"
    # L5 审计快照
    audit = (tmp_path / ".state" / "quality_audit.jsonl").read_text(encoding="utf-8")
    assert "gate_blind_rate" in audit


def test_blind_rate_ok_batch_not_marked(tmp_path) -> None:
    # 1/5 = 20%，不超阈值（严格大于）→ 不标记
    _write_flags(tmp_path, [_flag(3, "partial")])
    h = _Harness(tmp_path)
    h._run_blind_rate_batch_end(_Result(chapters_written=5, final_chapter=5))
    state = json.loads((tmp_path / GATE_BLIND_FILE).read_text(encoding="utf-8"))
    assert state["low_confidence_delivery"] is False
    assert not h._failures


def test_blind_rate_counts_only_batch_range(tmp_path) -> None:
    # 失明章在批次区间之外（前一批遗留）→ 不计入本批
    _write_flags(tmp_path, [_flag(90, "full")])
    h = _Harness(tmp_path)
    h._run_blind_rate_batch_end(_Result(chapters_written=5, final_chapter=5))
    state = json.loads((tmp_path / GATE_BLIND_FILE).read_text(encoding="utf-8"))
    assert state["batch_blind_rate"] == 0


# ---------------------------------------------------------------- V1.4 维度契约与分级不丢
def test_gate_blind_rate_dimension_registered() -> None:
    from agent.core.quality.dimension_registry import (
        Direction,
        SourceKind,
        Unit,
        spec_for,
    )

    spec = spec_for("gate_blind_rate")
    assert spec.unit is Unit.RATIO_0_1
    assert spec.direction is Direction.LOWER_BETTER
    assert spec.source is SourceKind.COMPUTED
    assert spec.default_threshold == pytest.approx(0.2)
    assert spec.required is False


def test_record_gate_skipped_persists_blind_level(tmp_path) -> None:
    from agent.workflows.writing.agentic_write import AgenticWriteWorkflow

    class _Host:
        project_dir = tmp_path

    host = _Host()
    AgenticWriteWorkflow._record_gate_skipped(
        host, {"chapter_num": 7}, "九项质检失败：模拟", blind_level="full"
    )
    flags = json.loads(
        (tmp_path / ".state" / "chapter_quality_flags.json").read_text(encoding="utf-8")
    )["flags"]
    assert flags[-1]["blind_level"] == "full"
    assert flags[-1]["violations"][0].startswith("gate_skipped")
