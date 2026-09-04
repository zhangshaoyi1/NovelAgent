"""G4 CLI 改进测试（P1-1 验收）：验证 autowrite CLI 的诚实化行为。

纯离线：用 typer.testing.CliRunner + mock AgenticPipelineWorkflow.run()，
验证退出码、JSON success 诚实化、进度回调位置、超时/预算参数透传。
覆盖 PRD §7 验收③：非交互 CLI 入口可见且诚实。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import typer
from typer.testing import CliRunner

from agent.cli.commands.autowrite import autowrite
from agent.workflows.pipeline.agentic_pipeline import AgenticPipelineWorkflow, PipelineResult


# ============================================================
# 辅助：构造 PipelineResult
# ============================================================
def _make_result(
    *,
    blocked: bool = False,
    tripped: bool = False,
    escalated: bool = False,
    block_reason: str = "",
    escalated_reason: str = "",
    chapters_written: int = 0,
    final_chapter: int = 0,
) -> PipelineResult:
    return PipelineResult(
        planned=True,
        chapters_written=chapters_written,
        final_chapter=final_chapter,
        blocked=blocked,
        block_reason=block_reason,
        tripped=tripped,
        escalated=escalated,
        escalated_reason=escalated_reason,
    )


# ============================================================
# 辅助：mock AgenticPipelineWorkflow.run 返回指定 result
# ============================================================
def _mock_pipeline_run(result: PipelineResult):
    """返回一个 mock，使 AgenticPipelineWorkflow().run() 返回 result。"""
    def _fake_run(self):
        return result
    return patch.object(AgenticPipelineWorkflow, "run", _fake_run)


# ============================================================
# 1. result.blocked=True → 退出码非 0
# ============================================================
def test_cli_exit_code_blocked(tmp_path: Path) -> None:
    """result.blocked=True → autowrite 退出码非 0（直接调用命令函数，捕获 typer.Exit）。"""
    result_obj = _make_result(blocked=True, block_reason="测试阻塞原因")

    with _mock_pipeline_run(result_obj):
        with pytest.raises(typer.Exit) as exc_info:
            autowrite(
                project_dir=str(tmp_path),
                json_output=False,
                env_file=None,
                brief="测试",
                chapters=0,
                mode="auto",
                no_eval=False,
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
            )
        assert exc_info.value.exit_code != 0, "blocked 时退出码应非 0"
        assert exc_info.value.exit_code == 1, "blocked 时退出码应为 1"


def test_cli_exit_code_blocked_direct(tmp_path: Path) -> None:
    """直接调用 autowrite 函数：blocked=True → raise typer.Exit(code=1)。"""
    result_obj = _make_result(blocked=True, block_reason="测试阻塞")

    with _mock_pipeline_run(result_obj):
        with pytest.raises(typer.Exit) as exc_info:
            autowrite(
                project_dir=str(tmp_path),
                json_output=False,
                env_file=None,
                brief="测试",
                chapters=0,
                mode="auto",
                no_eval=False,
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
            )
        assert exc_info.value.exit_code != 0, "blocked 时退出码应非 0"
        assert exc_info.value.exit_code == 1, "blocked 时退出码应为 1"


def test_cli_exit_code_tripped(tmp_path: Path) -> None:
    """result.tripped=True → 退出码非 0。"""
    result_obj = _make_result(tripped=True, block_reason="墙钟超时熔断")

    with _mock_pipeline_run(result_obj):
        with pytest.raises(typer.Exit) as exc_info:
            autowrite(
                project_dir=str(tmp_path),
                json_output=False,
                env_file=None,
                brief="测试",
                chapters=0,
                mode="auto",
                no_eval=False,
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
            )
        assert exc_info.value.exit_code != 0, "tripped 时退出码应非 0"
        assert exc_info.value.exit_code == 1, "tripped 时退出码应为 1"


def test_cli_exit_code_escalated(tmp_path: Path) -> None:
    """result.escalated=True → 退出码非 0（code=2）。"""
    result_obj = _make_result(escalated=True, escalated_reason="回溯超限，上报人工")

    with _mock_pipeline_run(result_obj):
        with pytest.raises(typer.Exit) as exc_info:
            autowrite(
                project_dir=str(tmp_path),
                json_output=False,
                env_file=None,
                brief="测试",
                chapters=0,
                mode="auto",
                no_eval=False,
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
            )
        assert exc_info.value.exit_code == 2, "escalated 时退出码应为 2"


def test_cli_exit_code_success(tmp_path: Path) -> None:
    """成功路径 → 退出码 0（不 raise typer.Exit）。"""
    result_obj = _make_result(chapters_written=5, final_chapter=5)

    with _mock_pipeline_run(result_obj):
        # 成功时不应抛出 typer.Exit
        autowrite(
            project_dir=str(tmp_path),
            json_output=False,
            env_file=None,
            brief="测试",
            chapters=0,
            mode="auto",
            no_eval=False,
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
        )
    # 若未抛出 typer.Exit，则测试通过


# ============================================================
# 2. --json 模式 + result.blocked=True → success=False
# ============================================================
def test_cli_json_success_false_when_blocked(tmp_path: Path, capsys) -> None:
    """--json 模式 + blocked=True → stdout 输出 success=False。"""
    result_obj = _make_result(blocked=True, block_reason="测试阻塞")

    with _mock_pipeline_run(result_obj):
        autowrite(
            project_dir=str(tmp_path),
            json_output=True,
            env_file=None,
            brief="测试",
            chapters=0,
            mode="auto",
            no_eval=False,
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
        )

    captured = capsys.readouterr()
    # stdout 应为 JSON 信封
    lines = [l for l in captured.out.strip().split("\n") if l.strip()]
    assert len(lines) >= 1, "JSON 模式应输出至少一行到 stdout"
    envelope = json.loads(lines[-1])
    assert envelope["success"] is False, f"--json blocked 时 success 应为 False，实际：{envelope}"
    assert envelope["blocked"] is True
    assert envelope["block_reason"] == "测试阻塞"


def test_cli_json_success_false_when_tripped(tmp_path: Path, capsys) -> None:
    """--json 模式 + tripped=True → success=False。"""
    result_obj = _make_result(tripped=True, block_reason="墙钟超时熔断")

    with _mock_pipeline_run(result_obj):
        autowrite(
            project_dir=str(tmp_path),
            json_output=True,
            env_file=None,
            brief="测试",
            chapters=0,
            mode="auto",
            no_eval=False,
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
        )

    captured = capsys.readouterr()
    lines = [l for l in captured.out.strip().split("\n") if l.strip()]
    envelope = json.loads(lines[-1])
    assert envelope["success"] is False
    assert envelope["tripped"] is True


def test_cli_json_success_true_when_ok(tmp_path: Path, capsys) -> None:
    """--json 模式 + 成功 → success=True。"""
    result_obj = _make_result(chapters_written=3, final_chapter=3)

    with _mock_pipeline_run(result_obj):
        autowrite(
            project_dir=str(tmp_path),
            json_output=True,
            env_file=None,
            brief="测试",
            chapters=0,
            mode="auto",
            no_eval=False,
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
        )

    captured = capsys.readouterr()
    lines = [l for l in captured.out.strip().split("\n") if l.strip()]
    envelope = json.loads(lines[-1])
    assert envelope["success"] is True


# ============================================================
# 3. --json 模式 → 进度回调输出到 stderr（不污染 stdout）
# ============================================================
def test_cli_progress_callback_stderr(tmp_path: Path, capsys, monkeypatch) -> None:
    """--json 模式 → on_progress 回调输出到 stderr，stdout 仅含 JSON 信封。"""
    result_obj = _make_result(chapters_written=2, final_chapter=2)

    # 追踪 on_progress 回调是否被调用，以及输出位置
    progress_calls = []

    def _fake_run_with_progress(self):
        # 模拟 pipeline 内部调用 on_progress
        if self.on_progress:
            self.on_progress("planning", 0, 100)
            self.on_progress("writing", 1, 10)
            self.on_progress("evaluating", 0, 100)
        return result_obj

    monkeypatch.setattr(AgenticPipelineWorkflow, "run", _fake_run_with_progress)

    autowrite(
        project_dir=str(tmp_path),
        json_output=True,
        env_file=None,
        brief="测试",
        chapters=0,
        mode="auto",
        no_eval=False,
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
    )

    captured = capsys.readouterr()
    # stdout 应为纯 JSON（不含 [progress] 文本）
    stdout_lines = [l for l in captured.out.strip().split("\n") if l.strip()]
    for line in stdout_lines:
        # 每行都应是合法 JSON（信封）
        try:
            json.loads(line)
        except json.JSONDecodeError:
            pytest.fail(f"stdout 含非 JSON 行（被进度污染）：{line}")

    # stderr 应含进度输出（[progress] 前缀）
    assert "[progress]" in captured.err, (
        f"JSON 模式下进度应输出到 stderr，实际 stderr：{captured.err[:200]}"
    )
    assert "planning" in captured.err
    assert "writing" in captured.err


def test_cli_progress_callback_stdout_for_non_json(tmp_path: Path, capsys, monkeypatch) -> None:
    """非 JSON 模式 → on_progress 回调输出到 console（stdout）。"""
    result_obj = _make_result(chapters_written=1, final_chapter=1)

    def _fake_run_with_progress(self):
        if self.on_progress:
            self.on_progress("planning", 0, 100)
            self.on_progress("writing", 1, 10)
        return result_obj

    monkeypatch.setattr(AgenticPipelineWorkflow, "run", _fake_run_with_progress)

    autowrite(
        project_dir=str(tmp_path),
        json_output=False,
        env_file=None,
        brief="测试",
        chapters=0,
        mode="auto",
        no_eval=False,
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
    )

    captured = capsys.readouterr()
    # 非 JSON 模式：进度输出到 stdout（console）
    assert "规划中" in captured.out or "writing" in captured.out.lower() or "写章" in captured.out


# ============================================================
# 4. --max-time 参数透传到 AgenticPipelineWorkflow._max_time
# ============================================================
def test_cli_max_time_param(tmp_path: Path, monkeypatch) -> None:
    """--max-time 参数能透传到 AgenticPipelineWorkflow._max_time。"""
    captured_kwargs = {}

    class _CapturingPipeline(AgenticPipelineWorkflow):
        def __init__(self, **kwargs):
            captured_kwargs.update(kwargs)
            super().__init__(**kwargs)

        def run(self):
            return _make_result()

    monkeypatch.setattr(
        "agent.workflows.pipeline.agentic_pipeline.AgenticPipelineWorkflow",
        _CapturingPipeline,
    )

    autowrite(
        project_dir=str(tmp_path),
        json_output=True,
        env_file=None,
        brief="测试",
        chapters=0,
        mode="auto",
        no_eval=True,
        rollback_window=5,
        max_rollback=3,
        max_time=3600,
        cost_tier="balanced",
        budget_margin=1.0,
        llm_timeout=None,
        appeal_gate=True,
        no_appeal_gate=False,
        appeal_threshold=60,
        appeal_window=1,
    )

    assert captured_kwargs.get("max_time") == 3600, (
        f"--max-time 应透传为 3600，实际：{captured_kwargs.get('max_time')}"
    )


def test_cli_cost_tier_param(tmp_path: Path, monkeypatch) -> None:
    """--cost-tier 参数透传到 AgenticPipelineWorkflow._cost_tier。"""
    captured_kwargs = {}

    class _CapturingPipeline(AgenticPipelineWorkflow):
        def __init__(self, **kwargs):
            captured_kwargs.update(kwargs)
            super().__init__(**kwargs)

        def run(self):
            return _make_result()

    monkeypatch.setattr(
        "agent.workflows.pipeline.agentic_pipeline.AgenticPipelineWorkflow",
        _CapturingPipeline,
    )

    autowrite(
        project_dir=str(tmp_path),
        json_output=True,
        env_file=None,
        brief="测试",
        chapters=0,
        mode="auto",
        no_eval=True,
        rollback_window=5,
        max_rollback=3,
        max_time=None,
        cost_tier="quality",
        budget_margin=1.0,
        llm_timeout=None,
        appeal_gate=True,
        no_appeal_gate=False,
        appeal_threshold=60,
        appeal_window=1,
    )

    assert captured_kwargs.get("cost_tier") == "quality"


def test_cli_budget_margin_param(tmp_path: Path, monkeypatch) -> None:
    """--budget-margin 参数透传到 AgenticPipelineWorkflow._budget_margin。"""
    captured_kwargs = {}

    class _CapturingPipeline(AgenticPipelineWorkflow):
        def __init__(self, **kwargs):
            captured_kwargs.update(kwargs)
            super().__init__(**kwargs)

        def run(self):
            return _make_result()

    monkeypatch.setattr(
        "agent.workflows.pipeline.agentic_pipeline.AgenticPipelineWorkflow",
        _CapturingPipeline,
    )

    autowrite(
        project_dir=str(tmp_path),
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
        budget_margin=2.5,
        llm_timeout=None,
        appeal_gate=True,
        no_appeal_gate=False,
        appeal_threshold=60,
        appeal_window=1,
    )

    assert captured_kwargs.get("budget_margin") == 2.5


def test_cli_llm_timeout_param(tmp_path: Path, monkeypatch) -> None:
    """--llm-timeout 参数透传到 AgenticPipelineWorkflow._llm_timeout。"""
    captured_kwargs = {}

    class _CapturingPipeline(AgenticPipelineWorkflow):
        def __init__(self, **kwargs):
            captured_kwargs.update(kwargs)
            super().__init__(**kwargs)

        def run(self):
            return _make_result()

    monkeypatch.setattr(
        "agent.workflows.pipeline.agentic_pipeline.AgenticPipelineWorkflow",
        _CapturingPipeline,
    )

    autowrite(
        project_dir=str(tmp_path),
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
        llm_timeout=60,
        appeal_gate=True,
        no_appeal_gate=False,
        appeal_threshold=60,
        appeal_window=1,
    )

    assert captured_kwargs.get("llm_timeout") == 60


# ============================================================
# 5. 额外：--json 模式下异常也输出 success=False 信封
# ============================================================
def test_cli_json_error_envelope(tmp_path: Path, capsys, monkeypatch) -> None:
    """pipeline.run() 抛异常时 --json 模式输出 success=False 错误信封。"""
    def _boom(self):
        raise RuntimeError("pipeline 故意崩溃")

    monkeypatch.setattr(AgenticPipelineWorkflow, "run", _boom)

    with pytest.raises(typer.Exit) as exc_info:
        autowrite(
            project_dir=str(tmp_path),
            json_output=True,
            env_file=None,
            brief="测试",
            chapters=0,
            mode="auto",
            no_eval=False,
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
        )
    assert exc_info.value.exit_code == 1

    captured = capsys.readouterr()
    lines = [l for l in captured.out.strip().split("\n") if l.strip()]
    envelope = json.loads(lines[-1])
    assert envelope["success"] is False
    assert envelope["error"]["code"] == "autowrite_failed"
    assert "pipeline 故意崩溃" in envelope["error"]["message"]