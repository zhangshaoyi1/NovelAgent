"""G8 结局模式测试（T3/T4/T7 验收，纯离线）

覆盖（对齐设计 §6.2 / §9 T3-T4）：
- stub 30 章 + 10 条伏笔 + 架构含 ending → 结局段触发（progress.ending_mode==true 且持久化）；
- 结局段每章注入回收任务（_load_foreshadow_task 输出断言：强制回收 + 禁新埋长线）；
- 末章 prompt 含架构 ending 关键词（fake writer 捕获 _build_task 输出）；
- 结局段不注入「埋设」任务（禁新埋长线，短线允许）；
- architecture.ending 为空/缺失 → 仍触发但注入「收尾」通用指令（降级不崩）；
- 回溯重写后 ending_mode 仍为 true（拍板 4：不退出）。
"""

from __future__ import annotations

import frontmatter
import pytest
from pathlib import Path
from rich.console import Console

from agent.core.engine.state_machine import State, StateMachine
from agent.workflows.m5_write_chapter import M5WriteChapterWorkflow
from tests._g3_fakes import _StubEditor, _StubMemory, _StubPlanner, _make_plan
from tests.conftest import _build_minimal_project
from tests.test_g8_mainline import _CountingLLM, _FakeWriter, _make_g8_project, _make_pipeline, _read_progress, S01

FORESHADOWS_TABLE = """# 伏笔登记表

| ID | 伏笔内容 | 埋设位置 | 预期回收点 | 状态 | 关联角色 |
|---|---|---|---|---|---|
| F-01 | 镜面乱码 | ch001 | ch025 | 已埋 | 林寻 |
| F-02 | 暗门功法 | ch002 | ch026 | 已埋 | 林寻 |
| F-03 | 太虚镜来历 | ch003 | ch027 | 未埋 | 太虚镜 |
| F-04 | 宗门密辛 | ch004 | ch028 | 已埋 | 林寻 |
| F-05 | 师父遗物 | ch005 | ch029 | 未埋 | 林寻 |
| F-06 | 短线钩子A | ch024 | ch025 | 已埋 | 林寻 |
| F-07 | 短线钩子B | ch025 | ch027 | 未埋 | 林寻 |
| F-08 | 短线钩子C | ch026 | ch028 | 已埋 | 林寻 |
| F-09 | 已废弃项 | ch002 | ch099 | 已废弃 | 林寻 |
| F-10 | 后续回收项 | ch006 | ch030 | 已回收 | 林寻 |
"""


def _make_ending_project(
    tmp_path: Path,
    *,
    target: int = 30,
    ending: str = "殉道",
    ending_ratio: float = 0.25,
    total_written: int = 0,
    foreshadows: str = FORESHADOWS_TABLE,
) -> Path:
    d = _make_g8_project(tmp_path, n_sublines=2, target=target)
    # 架构 ending：默认 ARCH_JSON.ending="殉道"；可覆盖为空
    if ending != "殉道":
        arch_path = d / "architecture.md"
        post = frontmatter.load(arch_path)
        arch = dict(post.metadata.get("architecture", {}) or {})
        arch["ending"] = ending
        post.metadata["architecture"] = arch
        arch_path.write_text(frontmatter.dumps(post), encoding="utf-8")
    if foreshadows is not None:
        (d / "foreshadows.md").write_text(foreshadows, encoding="utf-8")
    sm = StateMachine(d)
    sm.load()
    sm.progress = {
        "current_subline": S01,
        "current_chapter": total_written,
        "total_written": total_written,
        "last_written_at": "",
    }
    sm.save()
    return d


# ============================================================
# 1. 结局模式触发 + 持久化（拍板 2/4）
# ============================================================
def test_ending_mode_triggers_and_persists(tmp_path: Path) -> None:
    d = _make_ending_project(tmp_path, target=30, ending_ratio=0.25)
    writer = _FakeWriter(d)
    p = _make_pipeline(d, writer, ending_gate=True, ending_ratio=0.25, target=30)
    result = p.run()

    progress = _read_progress(d)
    assert progress["ending_mode"] is True, "第 23 章前应进入结局模式"
    assert progress["ending_mode_at"] == 23, f"ending_mode_at 应为 23，实际 {progress['ending_mode_at']}"
    assert result.ending is not None
    assert result.ending["ending_mode"] is True
    assert result.ending["ending_ratio"] == 0.25


# ============================================================
# 2. 结局段每章强制回收 + 禁新埋长线（拍板 5）
# ============================================================
def test_ending_section_foreshadow_task_injected(tmp_path: Path) -> None:
    d = _make_ending_project(tmp_path, target=30, ending_ratio=0.25)
    writer = _FakeWriter(d)
    p = _make_pipeline(d, writer, ending_gate=True, ending_ratio=0.25, target=30)
    p.run()

    # 第 23 章起进入结局段：foreshadow_task 含强制回收 + 禁新埋长线
    ch23_ctx = writer.ctx_list[22]
    task23 = ch23_ctx["foreshadow_task"]
    assert "强制回收 ≥1 条未回收伏笔" in task23
    assert "禁止新埋长线伏笔；短线（1-2 章内可自然回收）允许。" in task23
    assert "埋设" not in task23, "结局段不应注入「埋设」任务（禁新埋长线）"

    # 末章（第 30 章）同样处于结局段
    last_ctx = writer.ctx_list[-1]
    assert "强制回收 ≥1 条未回收伏笔" in last_ctx["foreshadow_task"]


def test_ending_section_lists_open_items(tmp_path: Path) -> None:
    d = _make_ending_project(tmp_path, target=30, ending_ratio=0.25, total_written=22)
    m5 = M5WriteChapterWorkflow(
        project_dir=d, llm_client=_CountingLLM(), console=Console(quiet=True),
        conflict_arbiter=None, pre_validate=False,
    )
    task = m5._load_foreshadow_task({"ending_mode": True, "total_written": 22})
    assert "F-01" in task, "结局段应列出未回收伏笔（含 ID）"
    assert "可回收" in task


# ============================================================
# 3. 末章 prompt 含架构 ending 关键词
# ============================================================
def test_last_chapter_prompt_contains_ending(tmp_path: Path) -> None:
    d = _make_ending_project(tmp_path, target=30, ending="殉道", ending_ratio=0.25)
    writer = _FakeWriter(d)
    p = _make_pipeline(d, writer, ending_gate=True, ending_ratio=0.25, target=30)
    p.run()

    last_task = writer.tasks[-1]
    assert "结局阶段指令" in last_task, "末章 prompt 应含结局阶段指令"
    assert "殉道" in last_task, "末章 prompt 应含架构 ending 关键词"
    assert "向架构结局" in last_task


# ============================================================
# 4. 非结局段逻辑零改动（每 10 章强制埋/回收保留）
# ============================================================
def test_non_ending_phase_logic_unchanged(tmp_path: Path) -> None:
    d = _make_ending_project(tmp_path, target=30, ending_ratio=0.25)
    m5 = M5WriteChapterWorkflow(
        project_dir=d, llm_client=_CountingLLM(), console=Console(quiet=True),
        conflict_arbiter=None, pre_validate=False,
    )
    # 第 10 章（非结局段）：既有「强制埋 ≥1 长线伏笔、回收 ≥1 旧伏笔」
    task10 = m5._load_foreshadow_task({"total_written": 9})
    assert "第 10 章" in task10 or "10 章" in task10 or "强制埋" in task10
    assert "强制回收 ≥1 条未回收伏笔" not in task10, "非结局段不应出现 G8 结局回收指令"


# ============================================================
# 5. ending 为空 → 降级「收尾」通用指令不崩
# ============================================================
def test_ending_empty_falls_back_to_wrapup(tmp_path: Path) -> None:
    d = _make_ending_project(tmp_path, target=30, ending="", ending_ratio=0.25)
    sm = StateMachine(d)
    sm.load()
    sm.progress = {
        "current_subline": S01,
        "total_written": 29,
        "ending_mode": True,
        "mainline_visited": [S01, "S02_支线2"],
    }
    sm.save()
    m5 = M5WriteChapterWorkflow(
        project_dir=d, llm_client=_CountingLLM(), console=Console(quiet=True),
        conflict_arbiter=None, pre_validate=False,
    )
    m5.state_machine.load()  # 与 M5.run 一致：先 load 再 _load_context
    assert m5._load_architecture_ending() == "", "ending 缺失应回退空串"

    ctx = m5._load_context()
    assert ctx["ending"] == ""
    assert ctx["ending_mode"] is True
    from agent.workflows.agentic_write import AgenticWriteWorkflow

    aw = AgenticWriteWorkflow(project_dir=d, llm_client=_CountingLLM(), console=Console(quiet=True))
    task = aw._build_task(ctx)
    assert "结局阶段指令（收尾）" in task
    assert "不留新开的故事线" in task
    assert "殉道" not in task, "ending 为空时不应注入具体结局文本"


def test_ending_empty_pipeline_no_crash(tmp_path: Path) -> None:
    d = _make_ending_project(tmp_path, target=8, ending="", ending_ratio=0.25)
    writer = _FakeWriter(d)
    p = _make_pipeline(d, writer, ending_gate=True, ending_ratio=0.25, target=8)
    result = p.run()
    assert result.final_chapter == 8, "ending 为空时结局模式应降级不阻断写章"
    assert writer.tasks[-1].count("结局阶段指令（收尾）") == 1


# ============================================================
# 6. 回溯重写后 ending_mode 仍为 true（拍板 4：不退出）
# ============================================================
def test_ending_mode_not_exited_after_rewrite(tmp_path: Path) -> None:
    d = _make_ending_project(tmp_path, target=30, ending_ratio=0.25)
    sm = StateMachine(d)
    sm.load()
    sm.progress = {
        "current_subline": S01,
        "total_written": 24,
        "ending_mode": True,
        "ending_mode_at": 23,
    }
    sm.save()

    p = _make_pipeline(d, _FakeWriter(d), ending_gate=True, ending_ratio=0.25, target=30)
    p._maybe_enter_ending_mode()  # 已进入 → 不重复触发、不清除
    sm.load()
    assert sm.progress["ending_mode"] is True
    assert sm.progress["ending_mode_at"] == 23

    # 写一章（回溯重写路径：writer.run 走 _update_progress 合并写入）
    writer = _FakeWriter(d)
    writer.run()
    sm.load()
    assert sm.progress["ending_mode"] is True, "合并写入后 ending_mode 必须保留（拍板 4）"
