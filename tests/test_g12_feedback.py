"""G12 读者数据回流测试（T7 验收，纯离线零 LLM）。

覆盖（对齐 G12/设计.md §4 / §9 T4）：
- add_feedback 写入 pacing_store（kind=reader_feedback）；
- list_feedback 查回；无效参数（全空/score 越界）→ ValueError；
- _load_context 分离 reader_signals（open_debts 既有行为不变）；
- _build_task 注入【读者反馈】段 + 弃书点标记；
- CLI reader_feedback --list / --json 信封（直接函数调用）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.core.story.pacing_store import PacingStore
from agent.cli.commands.reader_feedback import add_feedback, list_feedback


def _min_ctx(**overrides) -> dict:
    wi = {
        "title": "t", "tone": "热血", "pov": "第三人称", "rhythm": "快",
        "chapter_length": "2000", "info_density": "中", "banned_elements": "",
        "synopsis": "s", "realm_system": "r", "golden_finger_info": "g",
    }
    ctx = {
        "world_info": wi, "chapter_num": 13, "subline_id": "S01", "subline_name": "s1",
        "subline_goal": "g", "pressure_stage": "发展", "tension_level": "中",
        "route_node_id": "N1", "route_milestone": "M", "route_main_title": "T",
        "route_main_result": "R", "route_main_growth": "G", "characters_info": "C",
        "relations_info": "R", "foreshadow_task": "F", "prev_chapter_summary": "P",
        "payoff_task": "", "emotion_target": "", "reader_signals": [],
    }
    ctx.update(overrides)
    return ctx


# ---------------------------------------------------------------- add/list
def test_add_feedback_writes_kind(tmp_path: Path) -> None:
    r = add_feedback(tmp_path, score=7, abandon_at=12, comment="第12章节奏拖")
    assert r["debt_id"].startswith("fb-")
    debts = PacingStore(tmp_path).get_open_debts(n=100)
    fb = [d for d in debts if d.kind == "reader_feedback"]
    assert len(fb) == 1
    assert fb[0].planted_ch == 12
    assert "第12章节奏拖" in fb[0].desc


def test_list_feedback(tmp_path: Path) -> None:
    add_feedback(tmp_path, score=6, comment="中段疲软")
    items = list_feedback(tmp_path)
    assert len(items) == 1
    assert items[0]["desc"].startswith("读者评分 6/10")


def test_add_feedback_invalid(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="至少提供"):
        add_feedback(tmp_path)
    with pytest.raises(ValueError, match="0-10"):
        add_feedback(tmp_path, score=11)
    with pytest.raises(ValueError, match="正整数"):
        add_feedback(tmp_path, abandon_at=0)


# ---------------------------------------------------------------- 写章注入
def test_load_context_separates_reader_signals(tmp_path: Path) -> None:
    from tests.conftest import _build_minimal_project

    from agent.workflows.m5_write_chapter import M5WriteChapterWorkflow

    proj = _build_minimal_project(tmp_path)
    # 先写 12 章进度（state），再注入反馈（弃书点 12）
    sm = M5WriteChapterWorkflow(proj, llm_client=None, pre_validate=False).state_machine
    sm.load()
    sm.progress = {**(sm.progress or {}), "total_written": 12}
    sm.save()
    add_feedback(proj, abandon_at=12, comment="第12章节奏拖")

    wf = M5WriteChapterWorkflow(proj, llm_client=None, pre_validate=False)
    ctx = wf._load_context()
    assert ctx.get("reader_signals"), "应分离出 reader_feedback 信号"
    assert any("节奏拖" in s["desc"] for s in ctx["reader_signals"])
    # open_debts 既有行为不变（仍含全部债务）
    assert any(d.get("kind") == "reader_feedback" for d in ctx.get("open_debts", []))


def test_build_task_injects_feedback() -> None:
    from agent.workflows.agentic_write import AgenticWriteWorkflow

    wf = AgenticWriteWorkflow(Path("."), llm_client=None)
    task = wf._build_task(
        _min_ctx(reader_signals=[{"desc": "第12章弃读；节奏拖", "planted_ch": 12, "id": "fb-1"}])
    )
    assert "# 读者反馈" in task
    assert "第12章弃读" in task
    assert "位于本章之前" in task  # chapter_num=13 > planted_ch=12 → 强化标记


def test_build_task_no_feedback_byte_identical() -> None:
    from agent.workflows.agentic_write import AgenticWriteWorkflow

    wf = AgenticWriteWorkflow(Path("."), llm_client=None)
    task = wf._build_task(_min_ctx())
    assert "# 读者反馈" not in task


# ---------------------------------------------------------------- CLI 信封
def test_cli_json_envelope(tmp_path: Path) -> None:
    import json

    from agent.cli.commands import reader_feedback as rf

    class _Opt:
        def __init__(self, v):
            self.default = v

    # 直接函数调用（typer OptionInfo 归一化路径）
    rf.reader_feedback(
        project_dir=_Opt(str(tmp_path)),
        score=_Opt(8),
        abandon_at=_Opt(None),
        comment=_Opt("爽点不足"),
        list_only=_Opt(False),
        json_output=_Opt(True),
        env_file=_Opt(None),
    )
    items = list_feedback(tmp_path)
    assert len(items) == 1 and "爽点不足" in items[0]["desc"]
