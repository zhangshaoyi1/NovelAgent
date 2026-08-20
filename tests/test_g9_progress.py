"""G9 进度事件流测试（T7 验收，纯离线零 LLM）

覆盖（对齐设计 §6.2 / §9 T7）：
- pipeline 事件序列：planning → chapter_start → chapter_done → … → done；
- elapsed_s 单调递增（距事件流起点，PRD 验收）；
- chapter_done 含 words/quality_passed/chapter_elapsed_s/eta_s，eta_s 与平均耗时一致；
- progress.json 结构 {"events","summary"} 与原子性（tmp 不存在/内容合法）；续接 seq；
- 事件发射异常（on_event 抛错 / 落盘写失败）仍能成书（不阻断主流程）。

零 LLM：writer 用 stub（复用 M5 上下文加载/落盘/进度更新），planner/editor/memory 全桩。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import frontmatter
import pytest
from rich.console import Console

from agent.core.events import ProgressEventBus, compute_eta_s
from agent.core.state_machine import State
from agent.workflows.agentic_pipeline import AgenticPipelineWorkflow
from agent.workflows.m5_write_chapter import M5WriteChapterWorkflow
from tests._g3_fakes import _StubEditor, _StubMemory, _StubPlanner, _make_plan
from tests.conftest import _build_minimal_project


class _NoLLM:
    """零 LLM 断言：任何调用都计数（G9 事件路径不应触发任何 LLM）。"""

    def __init__(self) -> None:
        self.calls = 0

    def chat_creative(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("零 LLM：不应调用 chat_creative")

    def chat_utility(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("零 LLM：不应调用 chat_utility")

    def chat(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("零 LLM：不应调用 chat")


class _StubWriter:
    """写章桩：复用 M5 上下文加载/落盘/进度更新（确定性、零 LLM）。"""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = Path(project_dir)
        self.calls = 0
        self.llm = _NoLLM()

    def run(self, *args, **kwargs) -> SimpleNamespace:
        self.calls += 1
        m5 = M5WriteChapterWorkflow(
            project_dir=self.project_dir,
            llm_client=self.llm,
            console=Console(quiet=True),
            conflict_arbiter=None,
            pre_validate=False,
        )
        m5.state_machine.load()
        ctx = m5._load_context()
        ch_dir = self.project_dir / "chapters"
        ch_dir.mkdir(parents=True, exist_ok=True)
        text = f"第 {ctx['chapter_num']} 章正文。\n\n林寻抬头望向远方，剧情推进。"
        post = frontmatter.Post(
            text,
            chapter=ctx["chapter_num"],
            subline=ctx["subline_id"],
            route_node="N01",
            pressure_stage=ctx["pressure_stage"],
            title=f"第{ctx['chapter_num']}章",
            word_count=len(text),
            quality_passed=True,
            revision_attempts=0,
            evidence_chain={"characters": [], "foreshadows": [], "settings": []},
        )
        (ch_dir / f"ch{ctx['chapter_num']:03d}.md").write_bytes(
            frontmatter.dumps(post).encode("utf-8")
        )
        m5._update_progress(ctx)
        return SimpleNamespace(
            chapter_num=ctx["chapter_num"],
            chapter_text=text,
            chapter_title="x",
            quality_passed=True,
        )


def _make_project(tmp_path: Path) -> Path:
    """G9 测试项目：跳过 M2 讨论（autoplan 幂等），保证全程零 LLM；
    进度带 current_subline（规划完成后的真实形态）。"""
    d = _build_minimal_project(tmp_path, state=State.WRITING)
    (d / "discussion.md").write_text("# 讨论纪要\n\n已收敛。", encoding="utf-8")
    from agent.core.state_machine import StateMachine

    sm = StateMachine(d)
    sm.load()
    sm.progress = {
        "current_subline": "S01_器灵人性觉醒",
        "current_chapter": 0,
        "total_written": 0,
    }
    sm.save()
    return d


def _make_pipeline(
    d: Path,
    writer: _StubWriter,
    *,
    target: int = 3,
    on_event=None,
    progress_file=None,
) -> AgenticPipelineWorkflow:
    return AgenticPipelineWorkflow(
        project_dir=d,
        llm_client=writer.llm,
        brief="",
        target_chapters=target,
        eval_enabled=False,
        console=Console(quiet=True),
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
# 1. 事件序列 + elapsed_s 单调递增 + ETA 一致
# ============================================================
def test_pipeline_event_sequence_and_monotonic(tmp_path: Path) -> None:
    d = _make_project(tmp_path)
    writer = _StubWriter(d)
    events: list[dict] = []
    p = _make_pipeline(d, writer, target=3, on_event=events.append,
                       progress_file=d / ".state" / "progress.json")
    result = p.run()

    # 事件序列：planning → chapter_start → chapter_done ×3 → done
    types = [e["type"] for e in events]
    assert types[0] == "planning", f"首个事件应为 planning，实际 {types}"
    assert types[-1] == "done", f"末个事件应为 done，实际 {types}"
    assert types.count("chapter_start") == 3
    assert types.count("chapter_done") == 3
    assert types.count("chapter_substage") == 0  # stub writer 不发子阶段事件
    # 顺序断言：每章 start 在 done 前
    for ch in (1, 2, 3):
        i_start = types.index("chapter_start")
        i_done = types.index("chapter_done")
        assert i_start < i_done
        types[i_start] = "used_start"
        types[i_done] = "used_done"

    # elapsed_s 单调递增（距事件流起点秒数）
    prev = -1
    for e in events:
        assert e["elapsed_s"] >= prev, f"elapsed_s 应单调递增：{e}"
        prev = e["elapsed_s"]

    # chapter_start 字段：chapter/total/subline/pressure_stage（best-effort）
    starts = [e for e in events if e["type"] == "chapter_start"]
    assert starts[0]["chapter"] == 1
    assert starts[0]["total"] == 3
    assert starts[0]["subline"] == "S01_器灵人性觉醒"
    assert starts[0]["pressure_stage"] == ""  # 首章无上一章 → ""
    assert starts[1]["pressure_stage"] == "铺垫"  # 读上一章 frontmatter

    # chapter_done 字段 + ETA 一致（平均 chapter_elapsed_s × 剩余；ETA 用此前已完成章）
    dones = [e for e in events if e["type"] == "chapter_done"]
    for i, e in enumerate(dones):
        assert e["chapter"] == i + 1
        assert "words" in e and "quality_passed" in e
        assert "chapter_elapsed_s" in e
        assert e["eta_s"] == compute_eta_s(events[: events.index(e)], 3, i + 1)

    # done 事件字段
    done = events[-1]
    assert done["chapters_written"] == 3
    assert done["blocked"] is False and done["tripped"] is False and done["escalated"] is False
    assert done["total_elapsed_s"] >= 0

    # result 收尾字段
    assert result.chapters_written == 3
    assert result.failures == []
    assert result.summary is not None
    assert result.summary["events"] == len(events)
    assert result.summary["chapters_written"] == 3
    assert result.progress_file is not None


# ============================================================
# 2. progress.json 结构与原子性
# ============================================================
def test_progress_json_structure_and_atomic(tmp_path: Path) -> None:
    d = _make_project(tmp_path)
    writer = _StubWriter(d)
    pf = d / ".state" / "progress.json"
    p = _make_pipeline(d, writer, target=2, progress_file=pf)
    p.run()

    assert pf.exists(), "progress.json 应已落盘"
    assert not pf.with_suffix(".json.tmp").exists(), "tmp 文件不应残留（原子替换）"
    data = json.loads(pf.read_text(encoding="utf-8"))
    assert set(data.keys()) == {"events", "summary"}
    assert isinstance(data["events"], list) and data["events"]
    assert data["events"][-1]["type"] == "done"
    assert data["summary"]["events"] == len(data["events"])
    assert data["summary"]["failures"] == 0

    # state.json 并存互不覆盖：progress 快照仍在
    sm_file = d / ".state" / "state.json"
    assert sm_file.exists(), "state.json 不应被 progress.json 覆盖"


# ============================================================
# 3. 续接 seq（跨运行可追溯）
# ============================================================
def test_bus_resumes_seq(tmp_path: Path) -> None:
    pf = tmp_path / ".state" / "progress.json"
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(json.dumps({
        "events": [
            {"seq": 1, "type": "planning", "elapsed_s": 0},
            {"seq": 2, "type": "chapter_start", "chapter": 1, "elapsed_s": 5},
            {"seq": 3, "type": "chapter_done", "chapter": 1, "elapsed_s": 9},
        ],
        "summary": {},
    }, ensure_ascii=False), encoding="utf-8")

    bus = ProgressEventBus(progress_file=pf)
    assert bus.seq == 3, "启动应续接既有最大 seq"
    assert len(bus.events) == 3
    bus.emit("chapter_start", chapter=2, total=10)
    assert bus.seq == 4
    assert bus.events[-1]["seq"] == 4

    # 读失败降级空列表不阻断
    bad = tmp_path / "bad"
    bad.mkdir(parents=True, exist_ok=True)
    (bad / "progress.json").write_text("{not json", encoding="utf-8")
    bus2 = ProgressEventBus(progress_file=bad / "progress.json")
    assert bus2.seq == 0 and bus2.events == []


# ============================================================
# 4. compute_eta_s 单元（口径 = 平均章耗时 × 剩余）
# ============================================================
def test_compute_eta_s_unit() -> None:
    events = [
        {"type": "chapter_done", "chapter": 1, "chapter_elapsed_s": 100},
        {"type": "chapter_done", "chapter": 2, "chapter_elapsed_s": 120},
    ]
    # 平均 110 × 剩余 8
    assert compute_eta_s(events, target=10, current=2) == round(110 * 8)
    # 无已完成章 → None
    assert compute_eta_s([], target=10, current=0) is None
    # 已写完（target <= current）→ 0（不足 1 章按 0）
    assert compute_eta_s(events, target=2, current=2) == 0
    assert compute_eta_s(events, target=1, current=3) == 0
    # 简化两参调用（B1 口径）：compute_eta_s(avg_chapter_elapsed, remaining)
    assert compute_eta_s(110, 8) == 880
    assert compute_eta_s(110, 0) == 0


# ============================================================
# 5. 事件发射异常不阻断成书
# ============================================================
def test_on_event_raise_does_not_block(tmp_path: Path) -> None:
    d = _make_project(tmp_path)
    writer = _StubWriter(d)

    def raising(_ev: dict) -> None:
        raise RuntimeError("on_event 回调异常")

    p = _make_pipeline(d, writer, target=2, on_event=raising,
                       progress_file=d / ".state" / "progress.json")
    result = p.run()  # 不应抛异常
    assert result.chapters_written == 2, "事件回调异常不应阻断成书"
    assert result.final_chapter == 2


def test_progress_write_failure_does_not_block(tmp_path: Path, monkeypatch) -> None:
    d = _make_project(tmp_path)
    writer = _StubWriter(d)

    def failing_write(*_a, **_k):
        raise OSError("磁盘写失败")

    monkeypatch.setattr("agent.core.events._atomic_write_progress", failing_write)
    p = _make_pipeline(d, writer, target=2, progress_file=d / ".state" / "progress.json")
    result = p.run()  # 不应抛异常
    assert result.chapters_written == 2, "落盘失败不应阻断成书"


# ============================================================
# 6. 零 LLM 断言（G9 事件路径纯确定性）
# ============================================================
def test_zero_llm(tmp_path: Path) -> None:
    d = _make_project(tmp_path)
    writer = _StubWriter(d)
    p = _make_pipeline(d, writer, target=2, progress_file=d / ".state" / "progress.json")
    p.run()
    assert writer.llm.calls == 0, f"G9 事件路径应零 LLM，实际 {writer.llm.calls} 次"
