"""G10 AI 味默认治理测试（T4 验收，纯离线零 LLM）

覆盖（对齐设计 §5.3 / 拍板 5）：
- 默认（无参数）autowrite → gate_mode="block"（默认治理翻转）；
- --ai-gate-mode advisory → 显式放宽（标红不拒落盘）；
- --no-ai-gate → 不注入 guardrails；
- 非法值回退 "block"（保持默认治理）；
- guardrails.gate(mode=BLOCK) 命中 AI 味 → passed=False（拒落盘机制既有，仅默认档位翻转）。

零 LLM：复用 test_g6_cli 的 _CapturingPipeline 模式捕获构造参数。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from agent.cli.commands.autowrite import autowrite
from agent.workflows.agentic_pipeline import AgenticPipelineWorkflow, PipelineResult


def _capture_pipeline_run(monkeypatch, captured: dict[str, Any], result) -> None:
    class _CapturingPipeline(AgenticPipelineWorkflow):
        def __init__(self, **kwargs):
            captured.update(kwargs)
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
        no_mainline_gate=True,
        no_ending_gate=True,
        no_progress=True,
        no_stream=True,
        no_human_summary=True,
        no_cost=True,
    )
    kwargs.update(overrides)
    autowrite(**kwargs)


# ============================================================
# 1. 默认 → gate_mode="block"（G10 默认治理翻转，拍板 5）
# ============================================================
def test_g10_default_gate_mode_is_block(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, Any] = {}
    _capture_pipeline_run(monkeypatch, captured, PipelineResult(planned=True))
    _call_autowrite(project_dir=str(tmp_path))
    assert captured["gate_mode"] == "block", "默认应 block（AI 味命中拒落盘）"
    assert captured["guardrails"] is not None, "默认应注入 build_guardrails()"


# ============================================================
# 2. --ai-gate-mode advisory → 显式放宽
# ============================================================
def test_g10_advisory_explicit_relax(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, Any] = {}
    _capture_pipeline_run(monkeypatch, captured, PipelineResult(planned=True))
    _call_autowrite(project_dir=str(tmp_path), ai_gate_mode="advisory")
    assert captured["gate_mode"] == "advisory"


# ============================================================
# 3. --no-ai-gate → 不注入 guardrails（行为与 G4 一致）
# ============================================================
def test_g10_no_ai_gate_no_guardrails(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, Any] = {}
    _capture_pipeline_run(monkeypatch, captured, PipelineResult(planned=True))
    _call_autowrite(project_dir=str(tmp_path), no_ai_gate=True)
    assert captured["guardrails"] is None


# ============================================================
# 4. 非法值回退 "block"（保持默认治理）
# ============================================================
def test_g10_invalid_mode_falls_back_block(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, Any] = {}
    _capture_pipeline_run(monkeypatch, captured, PipelineResult(planned=True))
    _call_autowrite(project_dir=str(tmp_path), ai_gate_mode="bogus")
    assert captured["gate_mode"] == "block", "非法值应回退默认治理 block"


# ============================================================
# 5. guardrails.gate(mode=BLOCK) 命中 AI 味 → passed=False（拒落盘机制）
# ============================================================
def test_g10_guardrails_gate_block_rejects_ai_flavor(tmp_path: Path) -> None:
    from agent.core.quality.guardrails GateMode, build_guardrails

    gr = build_guardrails()
    # 构造含高置信 AI 腔组合式短语的文本（词表命中），含合规章节标题避免 title_placeholder 误伤
    text = "# 第1章 · 风云初起\n\n他嘴角勾起一抹弧度，不禁微微一笑，仿佛一切都尽在掌握。"
    res = gr.gate(text, mode=GateMode.BLOCK)
    assert res.passed is False, "block 模式命中 AI 味应拒落盘（passed=False）"
    assert any(
        r.get("rule_id") == "ai_flavor" or r.get("rule") == "ai_flavor"
        for r in res.violations
    ), f"violations 应含 ai_flavor 规则：{res.violations}"

    # advisory 模式：命中但仅标红（warn），不构成拒绝
    res_adv = gr.gate(text, mode=GateMode.ADVISORY)
    assert res_adv.passed is True, "advisory 命中仅标红（warn 不拒绝）"
