"""G6 CLI 参数透传测试（T7，P1-1 验收，纯离线）

覆盖（对齐设计 §8 关键断言）：
- 三闸默认开：golden_three_gate=True / padding_gate=True / guardrails 注入 / gate_mode="block"（G10 默认治理翻转）。
- --no-* 关闭：--no-golden-three-gate / --no-padding-gate / --no-ai-gate（guardrails=None）。
- 阈值覆盖：--golden-three-threshold / --golden-three-floor / --padding-threshold 透传生效。
- --ai-gate-mode block → gate_mode="block"；--ai-flavor-words 追加词表。
- 退出码/JSON 契约不变（--json 信封含 guardrails 汇总键）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import typer
from typer.testing import CliRunner

from agent.cli.commands.autowrite import autowrite
from agent.core.quality.guardrails _DEFAULT_AI_FLAVOR_WORDS
from agent.workflows.agentic_pipeline import AgenticPipelineWorkflow, PipelineResult


def _capture_pipeline_run(monkeypatch, captured_kwargs: dict[str, Any], result: PipelineResult):
    """monkeypatch AgenticPipelineWorkflow 为捕获构造参数的桩。"""

    class _CapturingPipeline(AgenticPipelineWorkflow):
        def __init__(self, **kwargs):
            captured_kwargs.update(kwargs)
            super().__init__(**kwargs)

        def run(self):
            return result

    monkeypatch.setattr(
        "agent.workflows.agentic_pipeline.AgenticPipelineWorkflow",
        _CapturingPipeline,
    )


def _call_autowrite(**overrides) -> None:
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
    )
    kwargs.update(overrides)
    autowrite(**kwargs)


# ============================================================
# 1. 三闸默认开
# ============================================================
def test_g6_cli_defaults(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, Any] = {}
    _capture_pipeline_run(monkeypatch, captured, PipelineResult(planned=True))
    _call_autowrite(project_dir=str(tmp_path))
    assert captured["golden_three_gate"] is True
    assert captured["golden_three_threshold"] == 60
    assert captured["golden_three_floor"] == 40
    assert captured["padding_gate"] is True
    assert captured["padding_threshold"] == 0.30
    assert captured["gate_mode"] == "block"  # G10：默认治理 block（拍板 5）
    gr = captured["guardrails"]
    assert gr is not None, "默认应注入 build_guardrails()"
    assert gr.ai_flavor_words == list(_DEFAULT_AI_FLAVOR_WORDS)


# ============================================================
# 2. --no-* 关闭
# ============================================================
def test_g6_cli_no_golden_three_gate(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, Any] = {}
    _capture_pipeline_run(monkeypatch, captured, PipelineResult(planned=True))
    _call_autowrite(project_dir=str(tmp_path), no_golden_three_gate=True)
    assert captured["golden_three_gate"] is False


def test_g6_cli_no_padding_gate(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, Any] = {}
    _capture_pipeline_run(monkeypatch, captured, PipelineResult(planned=True))
    _call_autowrite(project_dir=str(tmp_path), no_padding_gate=True)
    assert captured["padding_gate"] is False


def test_g6_cli_no_ai_gate(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, Any] = {}
    _capture_pipeline_run(monkeypatch, captured, PipelineResult(planned=True))
    _call_autowrite(project_dir=str(tmp_path), no_ai_gate=True)
    assert captured["guardrails"] is None, "--no-ai-gate 不注入 guardrails（与 G4 一致）"


# ============================================================
# 3. 阈值覆盖
# ============================================================
def test_g6_cli_threshold_overrides(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, Any] = {}
    _capture_pipeline_run(monkeypatch, captured, PipelineResult(planned=True))
    _call_autowrite(
        project_dir=str(tmp_path),
        golden_three_threshold=75,
        golden_three_floor=45,
        padding_threshold=0.15,
    )
    assert captured["golden_three_threshold"] == 75
    assert captured["golden_three_floor"] == 45
    assert captured["padding_threshold"] == 0.15


# ============================================================
# 4. --ai-gate-mode / --ai-flavor-words
# ============================================================
def test_g6_cli_ai_gate_mode_block(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, Any] = {}
    _capture_pipeline_run(monkeypatch, captured, PipelineResult(planned=True))
    _call_autowrite(project_dir=str(tmp_path), ai_gate_mode="block")
    assert captured["gate_mode"] == "block"


def test_g6_cli_ai_flavor_words_extend(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, Any] = {}
    _capture_pipeline_run(monkeypatch, captured, PipelineResult(planned=True))
    _call_autowrite(project_dir=str(tmp_path), ai_flavor_words="定制词A,定制词B")
    gr = captured["guardrails"]
    assert gr is not None
    assert "定制词A" in gr.ai_flavor_words
    assert "定制词B" in gr.ai_flavor_words


def test_g6_cli_invalid_gate_mode_falls_back(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, Any] = {}
    _capture_pipeline_run(monkeypatch, captured, PipelineResult(planned=True))
    _call_autowrite(project_dir=str(tmp_path), ai_gate_mode="bogus")
    assert captured["gate_mode"] == "block"  # G10：默认治理 block（拍板 5）


# ============================================================
# 5. 退出码/JSON 契约：escalated → 2、blocked → 1、成功 → 0（G6 参数下不变）
# ============================================================
def test_g6_cli_exit_code_escalated(tmp_path: Path, monkeypatch) -> None:
    result = PipelineResult(planned=True, escalated=True, escalated_reason="黄金三章失败")
    with patch.object(AgenticPipelineWorkflow, "run", lambda self: result):
        with pytest.raises(typer.Exit) as exc_info:
            _call_autowrite(project_dir=str(tmp_path), json_output=False)
        assert exc_info.value.exit_code == 2, "escalated → 退出码 2"


def test_g6_cli_exit_code_blocked(tmp_path: Path, monkeypatch) -> None:
    result = PipelineResult(planned=True, blocked=True, block_reason="命中 AI 腔词句")
    with patch.object(AgenticPipelineWorkflow, "run", lambda self: result):
        with pytest.raises(typer.Exit) as exc_info:
            _call_autowrite(project_dir=str(tmp_path), json_output=False)
        assert exc_info.value.exit_code == 1, "blocked → 退出码 1"


def test_g6_cli_json_envelope_has_guardrails(tmp_path: Path, capsys, monkeypatch) -> None:
    result = PipelineResult(
        planned=True,
        guardrails={"mode": "advisory", "ai_flavor_hits": [], "ai_flavor_count": 0, "blocked": False},
    )
    with patch.object(AgenticPipelineWorkflow, "run", lambda self: result):
        _call_autowrite(project_dir=str(tmp_path), json_output=True)
    captured = capsys.readouterr()
    lines = [l for l in captured.out.strip().split("\n") if l.strip()]
    envelope = json.loads(lines[-1])
    assert envelope["success"] is True
    assert envelope["guardrails"]["mode"] == "advisory"
    assert envelope["guardrails"]["ai_flavor_count"] == 0
