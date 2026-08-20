"""G9 失败自助恢复测试（T7 验收，纯离线）

覆盖（对齐设计 §6.2 / §9 T7）：
- 注入写章失败 → failure 事件含 step/reason/severity/next_steps，进 result.failures；
- build_run_summary 聚合正确（事件数/失败数/各失败/总耗时/结局）；
- next_steps_for 指向真实命令（doctor/reset-state/draft-status/write/status）；
- _shared.enforce_gate 门禁拒绝信封只增 next_steps（既有 code/message 不变）；
- --json 信封新增 4 字段不影响既有字段解析（只增不删）。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import typer

from agent.cli._shared import enforce_gate
from agent.cli.commands.autowrite import autowrite
from agent.core.events import build_run_summary, next_steps_for
from agent.core.state_machine import State
from agent.workflows.agentic_pipeline import AgenticPipelineWorkflow, PipelineResult
from tests._g3_fakes import _StubEditor, _StubMemory, _StubPlanner, _make_plan
from tests.conftest import _build_minimal_project


class _RaisingWriter:
    """写章桩：run() 直接抛异常（模拟写章失败）。"""

    def __init__(self, project_dir: Path, message: str = "LLM 调用失败：connection timeout") -> None:
        self.project_dir = Path(project_dir)
        self.message = message
        self.calls = 0

    def run(self, *args, **kwargs):
        self.calls += 1
        raise RuntimeError(self.message)


def _make_project(tmp_path: Path) -> Path:
    d = _build_minimal_project(tmp_path, state=State.WRITING)
    (d / "discussion.md").write_text("# 讨论纪要\n\n已收敛。", encoding="utf-8")
    return d


def _make_pipeline(d: Path, writer, *, target: int = 5, on_event=None, progress_file=None):
    return AgenticPipelineWorkflow(
        project_dir=d,
        llm_client=getattr(writer, "llm", None),
        brief="",
        target_chapters=target,
        eval_enabled=False,
        console=__import__("rich.console", fromlist=["Console"]).Console(quiet=True),
        planner=_StubPlanner(_make_plan()),
        writer_workflow=writer,
        editor=_StubEditor(),
        memory=_StubMemory(),
        mainline_gate=False,
        ending_gate=False,
        on_event=on_event,
        progress_file=progress_file,
    )


# ============================================================
# 1. 写章失败 → failure 事件含 step/reason/severity/next_steps
# ============================================================
def test_write_failure_event_and_result_failures(tmp_path: Path) -> None:
    d = _make_project(tmp_path)
    writer = _RaisingWriter(d, message="LLM 调用失败：connection timeout")
    events: list[dict] = []
    p = _make_pipeline(d, writer, target=5, on_event=events.append,
                       progress_file=d / ".state" / "progress.json")
    result = p.run()

    # failure 事件已发射
    fails = [e for e in events if e["type"] == "failure"]
    assert fails, "写章失败应发 failure 事件"
    f = fails[0]
    assert f["step"] == "write_chapter"
    assert f["reason"] == "LLM 调用失败：connection timeout"
    assert f["severity"] == "error"
    assert f["next_steps"] == next_steps_for("write_chapter", str(d))

    # result.failures 已填充（_finalize_g9 收集）
    assert result.failures
    assert result.failures[0]["step"] == "write_chapter"

    # done 事件仍发射（收尾）
    assert events[-1]["type"] == "done"
    assert result.summary is not None
    assert result.summary["failures"] == 1
    assert result.summary["failed_steps"] == ["write_chapter"]
    assert "运行摘要：共" in result.summary["text"]
    assert "失败[write_chapter]" in result.summary["text"]
    assert "下一步：novel-agent doctor -d" in result.summary["text"]


# ============================================================
# 2. build_run_summary 聚合正确（纯确定性）
# ============================================================
def test_build_run_summary_aggregation() -> None:
    events = [
        {"type": "planning", "elapsed_s": 0},
        {"type": "failure", "step": "write_chapter", "reason": "r1", "severity": "error",
         "next_steps": ["novel-agent doctor -d p", "novel-agent write -d p"]},
        {"type": "failure", "step": "eval", "reason": "r2", "severity": "warn",
         "next_steps": ["novel-agent doctor -d p", "novel-agent autowrite -d p"]},
        {"type": "done", "total_elapsed_s": 300,
         "blocked": True, "tripped": False, "escalated": False},
    ]
    s = build_run_summary(events, SimpleNamespace(chapters_written=1))
    assert s["events"] == 4
    assert s["failures"] == 2
    assert s["failed_steps"] == ["write_chapter", "eval"]
    assert s["total_elapsed_s"] == 300
    assert s["chapters_written"] == 1
    assert "运行摘要：共 4 事件 · 2 失败" in s["text"]
    assert "失败[write_chapter]（error）：r1" in s["text"]
    assert "下一步：novel-agent doctor -d p" in s["text"]
    assert "结局：blocked" in s["text"]
    # 无失败时输出「0 失败」
    ok = build_run_summary([{"type": "done", "total_elapsed_s": 60}])
    assert ok["failures"] == 0
    assert "0 失败" in ok["text"]
    assert "结局：正常完成" in ok["text"]


# ============================================================
# 3. next_steps 指向真实命令
# ============================================================
def test_next_steps_for_real_commands(tmp_path: Path) -> None:
    d = _build_minimal_project(tmp_path)
    # write_chapter → doctor + write 重试
    cmds = next_steps_for("write_chapter", str(d))
    assert cmds[0].startswith("novel-agent doctor -d")
    assert any("novel-agent write -d" in c for c in cmds)
    assert all(str(d) in c for c in cmds)
    # state_parse → reset-state
    assert next_steps_for("state_parse", "p") == ["novel-agent reset-state -d p"]
    # draft_residue → draft-status + write
    dcmds = next_steps_for("draft_residue", "p")
    assert any("draft-status" in c for c in dcmds)
    assert any("novel-agent write -d" in c for c in dcmds)
    # gate → status + write
    gcmds = next_steps_for("gate", "p")
    assert any("novel-agent status -d" in c for c in gcmds)
    # adjust 高冲突 → doctor + status
    acmds = next_steps_for("adjust", "p")
    assert any("doctor" in c for c in acmds) and any("status" in c for c in acmds)
    # 未知 step → 回退 doctor + status
    fcmds = next_steps_for("unknown_step", "p")
    assert any("doctor" in c for c in fcmds) and any("status" in c for c in fcmds)


# ============================================================
# 4. _shared 门禁拒绝信封只增 next_steps
# ============================================================
def test_enforce_gate_envelope_adds_next_steps(tmp_path: Path, capsys) -> None:
    d = _build_minimal_project(tmp_path, state=State.INIT)  # /write 在 INIT 不可用
    with pytest.raises(typer.Exit) as exc:
        enforce_gate(str(d), "write", json_mode=True)
    assert exc.value.exit_code == 2, "门禁拒绝退出码应为 2"
    captured = capsys.readouterr()
    lines = [l for l in captured.out.strip().split("\n") if l.strip()]
    env = json.loads(lines[-1])
    assert env["success"] is False
    assert env["error"]["code"] == "gate_rejected", "既有 code 不变"
    assert "message" in env["error"], "既有 message 保留"
    assert env["error"]["next_steps"] == next_steps_for("gate", str(d)), "只增 next_steps"


# ============================================================
# 5. --json 信封新增 4 字段不影响既有字段解析（只增不删）
# ============================================================
def _mock_pipeline_run(result: PipelineResult):
    def _fake_run(self):
        return result

    return patch.object(AgenticPipelineWorkflow, "run", _fake_run)


def test_json_envelope_new_fields_only(tmp_path: Path, capsys) -> None:
    result_obj = PipelineResult(planned=True, chapters_written=2, final_chapter=2)
    base = {k: v for k, v in result_obj.to_dict().items()
            if k not in ("progress_file", "failures", "stream", "summary")}

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
            golden_three_gate=True,
            no_golden_three_gate=False,
            golden_three_threshold=60,
            golden_three_floor=40,
            ai_gate=True,
            no_ai_gate=False,
            ai_gate_mode="advisory",
            ai_flavor_words=None,
            padding_gate=True,
            no_padding_gate=False,
            padding_threshold=0.30,
            no_human_summary=False,
            no_cost=True,
            mainline_window=5,
            ending_ratio=0.25,
            no_mainline_gate=False,
            no_ending_gate=False,
            no_progress=False,
            no_stream=False,
        )

    captured = capsys.readouterr()
    lines = [l for l in captured.out.strip().split("\n") if l.strip()]
    env = json.loads(lines[-1])
    # 既有字段字节级不变（值一致、键不删）
    for k, v in base.items():
        assert env[k] == v, f"既有字段 {k} 应保持不变"
    # 新增 4 字段
    assert "progress_file" in env and env["progress_file"] is not None
    assert Path(env["progress_file"]).is_absolute()
    assert str(Path(env["progress_file"])) == str((tmp_path / ".state" / "progress.json").resolve())
    assert env["failures"] == []
    assert env["summary"] is None
    assert env["stream"] is None


def test_json_no_progress_no_stream_set_null(tmp_path: Path, capsys) -> None:
    result_obj = PipelineResult(planned=True, chapters_written=1, final_chapter=1)
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
            golden_three_gate=True,
            no_golden_three_gate=False,
            golden_three_threshold=60,
            golden_three_floor=40,
            ai_gate=True,
            no_ai_gate=False,
            ai_gate_mode="advisory",
            ai_flavor_words=None,
            padding_gate=True,
            no_padding_gate=False,
            padding_threshold=0.30,
            no_human_summary=False,
            no_cost=True,
            mainline_window=5,
            ending_ratio=0.25,
            no_mainline_gate=False,
            no_ending_gate=False,
            no_progress=True,
            no_stream=True,
        )
    captured = capsys.readouterr()
    lines = [l for l in captured.out.strip().split("\n") if l.strip()]
    env = json.loads(lines[-1])
    assert env["progress_file"] is None, "--no-progress → progress_file 置 null"
    assert env["stream"] is None, "--no-stream → stream 置 null"
    assert env["success"] is True
    assert env["chapters_written"] == 1
