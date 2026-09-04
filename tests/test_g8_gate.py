"""G8 验收维度门禁测试（T5/T7 验收，纯离线）

覆盖（对齐设计 §6.2 / §9 T5）：
- stub 报告断言 mainline_progress / ending_convergence 存在、source="computed"、影响 overall_pass；
- mainline_progress：1 条支线恒达标（短书不误杀）；≥min(3,总数) 才达标；
- ending_convergence：无伏笔恒达标；ending_mode/末章任一缺失 → value=0 失败；
- mainline/ending 失败 → evaluate_with_repair 直通 escalated（spy trigger_rollback 未调用）；
- --no-mainline-gate / --no-ending-gate 不注入维度零回归；
- _update_progress 合并写入保留既有键（拍板 4 关键兼容点）。
- autowrite CLI 四参数透传 + --json 信封（T6 验收，仿 G6 CLI 测试模式）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from rich.console import Console

from agent.agents.evaluator import EvaluatorAgent
from agent.core.engine.state_machine import State, StateMachine
from agent.workflows.pipeline.agentic_pipeline import AgenticPipelineWorkflow, PipelineResult
from agent.workflows.writing.m5_write_chapter import M5WriteChapterWorkflow
from tests.conftest import _build_minimal_project
from tests.test_g8_mainline import _CountingLLM, _make_g8_project, S01


def _make_gate_project(
    tmp_path: Path,
    *,
    n_sublines: int = 5,
    visited: list[str] | None = None,
    ending_mode: bool = False,
    total_written: int = 0,
    target: int = 30,
    plan_json: dict | None = None,
    foreshadows: str | None = None,
    write_last_chapter: bool = False,
) -> Path:
    d = _make_g8_project(
        tmp_path, n_sublines=n_sublines, target=target,
        plan_json=plan_json, total_written=total_written,
    )
    sm = StateMachine(d)
    sm.load()
    sm.progress = {
        "current_subline": S01,
        "current_chapter": total_written,
        "total_written": total_written,
        "mainline_visited": list(visited or []),
        "ending_mode": ending_mode,
    }
    sm.save()
    if foreshadows is None:
        f = d / "foreshadows.md"
        if f.exists():
            f.unlink()  # 无伏笔 → 既有七维全过，G8 维度单独决定
    else:
        (d / "foreshadows.md").write_text(foreshadows, encoding="utf-8")
    if write_last_chapter:
        ch = d / "chapters"
        ch.mkdir(parents=True, exist_ok=True)
        (ch / f"ch{target:03d}.md").write_text(
            "---\ntitle: 末章\n---\n末章正文。", encoding="utf-8"
        )
    return d


def _make_evaluator(d: Path, *, mainline_gate: bool = True, ending_gate: bool = True) -> EvaluatorAgent:
    return EvaluatorAgent(
        d,
        mainline_gate=mainline_gate,
        ending_gate=ending_gate,
        mainline_window=5,
        ending_ratio=0.25,
    )


# ============================================================
# 1. 两维度存在 / source=computed / 影响 overall_pass
# ============================================================
def test_g8_dims_exist_computed_affect_overall_pass(tmp_path: Path) -> None:
    d = _make_gate_project(
        tmp_path, n_sublines=5, visited=[S01, "S02_支线2"], ending_mode=False,
    )
    ev = _make_evaluator(d)
    report = ev._evaluate_once()
    mp = report.dimension("mainline_progress")
    ec = report.dimension("ending_convergence")
    assert mp is not None, "应注入 mainline_progress"
    assert ec is not None, "应注入 ending_convergence"
    assert mp.source == "computed" and ec.source == "computed"
    assert mp.value == 2 and mp.threshold == 3, "已访问 2/5 < 3 → 失败"
    assert mp.passed is False
    assert ec.value == 0.0 and ec.passed is False, "ending_mode=false → value 归零失败"
    assert report.overall_pass is False, "G8 维度失败应使 overall_pass=False"


def test_mainline_three_visited_passes(tmp_path: Path) -> None:
    d = _make_gate_project(
        tmp_path, n_sublines=5,
        visited=[S01, "S02_支线2", "S03_支线3"],
        ending_mode=True, target=30,
        plan_json={"total_chapters": 30, "episode_tree": []},
        write_last_chapter=True,
    )
    ev = _make_evaluator(d)
    report = ev._evaluate_once()
    mp = report.dimension("mainline_progress")
    ec = report.dimension("ending_convergence")
    assert mp is not None and mp.passed is True, "已访问 3/5 ≥ 3 应达标"
    assert ec is not None and ec.passed is True, "ending_mode+末章存在+无伏笔 rate=1.0 应达标"
    assert report.overall_pass is True


# ============================================================
# 2. 短书不误杀：1 条支线恒达标
# ============================================================
def test_mainline_single_subline_always_passes(tmp_path: Path) -> None:
    d = _make_gate_project(tmp_path, n_sublines=1, visited=[S01], ending_mode=False)
    ev = _make_evaluator(d, ending_gate=False)
    report = ev._evaluate_once()
    mp = report.dimension("mainline_progress")
    assert mp is not None and mp.passed is True, "1 条支线 threshold=min(3,1)=1，访问 1 恒达标"
    assert report.overall_pass is True


# ============================================================
# 3. 无伏笔恒达标（不误杀）
# ============================================================
def test_ending_no_foreshadow_always_passes(tmp_path: Path) -> None:
    d = _make_gate_project(
        tmp_path, n_sublines=1, visited=[S01], ending_mode=True, target=30,
        plan_json={"total_chapters": 30, "episode_tree": []},
        write_last_chapter=True,
    )
    ev = _make_evaluator(d)
    report = ev._evaluate_once()
    ec = report.dimension("ending_convergence")
    assert ec is not None and ec.passed is True, "无伏笔 open_at_start=0 → rate=1.0 恒达标"
    assert ec.value == pytest.approx(1.0)


def test_ending_missing_last_chapter_fails(tmp_path: Path) -> None:
    d = _make_gate_project(
        tmp_path, n_sublines=1, visited=[S01], ending_mode=True, target=30,
        plan_json={"total_chapters": 30, "episode_tree": []},
        write_last_chapter=False,  # 末章缺失
    )
    ev = _make_evaluator(d, mainline_gate=False)
    report = ev._evaluate_once()
    ec = report.dimension("ending_convergence")
    assert ec is not None and ec.passed is False, "末章缺失 → ending_convergence 失败"


# ============================================================
# 4. 失败 → evaluate_with_repair 直通 escalated，禁止 trigger_rollback
# ============================================================
def test_g8_failure_escalates_without_rollback(tmp_path: Path) -> None:
    d = _make_gate_project(tmp_path, n_sublines=5, visited=[S01], ending_mode=False)
    ev = _make_evaluator(d)
    rollback_calls: list = []

    def spy(*args, **kwargs):
        rollback_calls.append(args)
        return None

    ev.trigger_rollback = spy  # type: ignore[method-assign]
    report = ev.evaluate_with_repair(lambda nums: None)
    assert report.escalated is True, "mainline/ending 失败应直通 escalated"
    assert rollback_calls == [], "mainline/ending 失败禁止触发 trigger_rollback（红线）"
    assert "全局结构门禁失败" in report.escalated_reason
    assert "主线推进" in report.escalated_reason, "明细应含主线推进统计"
    assert "结局收敛" in report.escalated_reason, "明细应含结局收敛统计"
    assert "已访问 1/5" in report.escalated_reason


# ============================================================
# 5. 开关关闭 → 不注入维度零回归
# ============================================================
def test_gates_off_no_dims_zero_regression(tmp_path: Path) -> None:
    d = _make_gate_project(tmp_path, n_sublines=5, visited=[S01], ending_mode=False)
    ev = _make_evaluator(d, mainline_gate=False, ending_gate=False)
    report = ev._evaluate_once()
    g8 = [x for x in report.dimensions if x.name.startswith(("mainline_", "ending_"))]
    assert g8 == [], "关闭门禁不应注入 mainline_*/ending_* 维度"
    assert report.overall_pass is True, "仅由既有七维（全过）决定，零回归"


# ============================================================
# 6. _update_progress 合并写入保留既有键（拍板 4 关键兼容点）
# ============================================================
def _make_m5(d: Path) -> M5WriteChapterWorkflow:
    return M5WriteChapterWorkflow(
        project_dir=d, llm_client=_CountingLLM(), console=Console(quiet=True),
        conflict_arbiter=None, pre_validate=False,
    )


def test_update_progress_merge_preserves_existing_keys(tmp_path: Path) -> None:
    d = _build_minimal_project(tmp_path, state=State.WRITING)
    m5 = _make_m5(d)
    sm = StateMachine(d)
    sm.load()
    sm.progress = {
        "current_subline": S01,
        "total_written": 5,
        "mainline_visited": [S01, "S02_支线2"],
        "ending_mode": True,
        "ending_mode_at": 23,
        "g8_custom_key": "keep",
    }
    sm.save()

    m5._update_progress({"subline_id": "S02_支线2", "chapter_num": 6})
    sm.load()
    p = sm.progress
    assert p["ending_mode"] is True, "合并写入必须保留 ending_mode"
    assert p["ending_mode_at"] == 23
    assert p["g8_custom_key"] == "keep", "任意既有键必须保留（禁止全新 dict 覆盖）"
    assert p["mainline_visited"] == [S01, "S02_支线2"], "去重：已存在不重复追加"
    assert p["total_written"] == 6
    assert p["current_subline"] == "S02_支线2"


def test_update_progress_initializes_mainline_visited(tmp_path: Path) -> None:
    d = _build_minimal_project(tmp_path, state=State.WRITING)
    m5 = _make_m5(d)
    sm = StateMachine(d)
    sm.load()
    sm.progress = {"current_subline": S01, "total_written": 0}
    sm.save()

    m5._update_progress({"subline_id": S01, "chapter_num": 1})
    sm.load()
    assert sm.progress["mainline_visited"] == [S01], "首次写章以当前支线打底初始化"


# ============================================================
# 7. autowrite CLI 四参数透传 + --json 信封（T6，仿 G6 CLI 测试模式）
# ============================================================
def _capture_pipeline_run(monkeypatch, captured: dict, result: PipelineResult) -> None:
    class _CapturingPipeline(AgenticPipelineWorkflow):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            super().__init__(**kwargs)

        def run(self):
            return result

    monkeypatch.setattr(
        "agent.workflows.pipeline.agentic_pipeline.AgenticPipelineWorkflow",
        _CapturingPipeline,
    )


def _call_autowrite(**overrides) -> None:
    from agent.cli.commands.autowrite import autowrite

    kwargs = dict(
        project_dir=str(Path("tmp")),
        json_output=True,
        env_file=None,
        brief="测试",
        chapters=0,
        mode="auto",
        no_eval=True,
        rollback_window=5,
        max_rollback=3,
        max_time=None,
        cost_tier="balanced",
        budget_margin=1.0,
        llm_timeout=None,
        appeal_gate=True,
        no_appeal_gate=False,
        appeal_threshold=60,
        appeal_window=1,
        no_human_summary=False,
        no_cost=False,
    )
    kwargs.update(overrides)
    autowrite(**kwargs)


def test_g8_cli_defaults(tmp_path: Path, monkeypatch) -> None:
    captured: dict = {}
    _capture_pipeline_run(monkeypatch, captured, PipelineResult(planned=True))
    _call_autowrite(project_dir=str(tmp_path))
    assert captured["mainline_window"] == 5
    assert captured["ending_ratio"] == 0.25
    assert captured["mainline_gate"] is True
    assert captured["ending_gate"] is True


def test_g8_cli_overrides_clamped(tmp_path: Path, monkeypatch) -> None:
    captured: dict = {}
    _capture_pipeline_run(monkeypatch, captured, PipelineResult(planned=True))
    _call_autowrite(
        project_dir=str(tmp_path), mainline_window=3, ending_ratio=0.4,
    )
    assert captured["mainline_window"] == 3
    assert captured["ending_ratio"] == 0.4
    # 钳制语义：window≥1，ratio∈[0,0.5]
    captured2: dict = {}
    _capture_pipeline_run(monkeypatch, captured2, PipelineResult(planned=True))
    _call_autowrite(project_dir=str(tmp_path), mainline_window=-2, ending_ratio=2.0)
    assert captured2["mainline_window"] == 1, "window 钳制 ≥1"
    assert captured2["ending_ratio"] == 0.5, "ratio 钳制 ≤0.5"


def test_g8_cli_no_gates(tmp_path: Path, monkeypatch) -> None:
    captured: dict = {}
    _capture_pipeline_run(monkeypatch, captured, PipelineResult(planned=True))
    _call_autowrite(project_dir=str(tmp_path), no_mainline_gate=True, no_ending_gate=True)
    assert captured["mainline_gate"] is False
    assert captured["ending_gate"] is False


def test_g8_cli_json_envelope(tmp_path: Path, monkeypatch, capsys) -> None:
    result = PipelineResult(
        planned=True,
        mainline={"current_subline": S01, "mainline_visited": [S01], "mainline_window": 5},
        ending={"ending_mode": True, "ending_mode_at": 23, "ending_ratio": 0.25},
    )
    _capture_pipeline_run(monkeypatch, {}, result)
    _call_autowrite(project_dir=str(tmp_path), json_output=True)
    lines = [l for l in capsys.readouterr().out.strip().split("\n") if l.strip()]
    envelope = json.loads(lines[-1])
    assert envelope["mainline"]["current_subline"] == S01
    assert envelope["ending"]["ending_mode"] is True
    # 既有字段零改动
    assert envelope["success"] is True
    assert "cost" in envelope


def test_g8_cli_json_no_gates_null(tmp_path: Path, monkeypatch, capsys) -> None:
    result = PipelineResult(
        planned=True,
        mainline={"current_subline": S01, "mainline_visited": [S01], "mainline_window": 5},
        ending={"ending_mode": True, "ending_mode_at": 23, "ending_ratio": 0.25},
    )
    _capture_pipeline_run(monkeypatch, {}, result)
    _call_autowrite(project_dir=str(tmp_path), json_output=True,
                    no_mainline_gate=True, no_ending_gate=True)
    lines = [l for l in capsys.readouterr().out.strip().split("\n") if l.strip()]
    envelope = json.loads(lines[-1])
    assert envelope["mainline"] is None, "--no-mainline-gate 后 mainline 置 null"
    assert envelope["ending"] is None, "--no-ending-gate 后 ending 置 null"
