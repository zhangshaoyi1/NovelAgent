"""G8 主线推进测试（T2/T7 验收，纯离线零 LLM）

覆盖（对齐设计 §6.2 / §9 T2）：
- stub 5 支线 + 30 章目标 + fake writer 推进 progress → 自动切换 ≥3 条支线；
- progress.current_subline / mainline_visited 正确（合并写入去重）；
- 切换时机符合压力曲线 / 规划区间上界（stub subline.md + plan.json；多源取 max）；
- 零 LLM（monkeypatch LLM 调用计数器为 0）；
- --no-mainline-gate 关闭后行为与现状一致（不决策不切换）；
- 进入结局模式后 decide_mainline_advance 返回 None（禁新线，拍板 5）。

纯离线：writer 用 fake（复用 M5 上下文加载/落盘/进度更新，不调 LLM）。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import frontmatter
import pytest
from rich.console import Console

from agent.client import LLMResponse
from agent.core.state_machine import State, StateMachine
from agent.workflows.agentic_pipeline import AgenticPipelineWorkflow
from agent.workflows.m5_write_chapter import M5WriteChapterWorkflow
from agent.workflows.mainline import decide_mainline_advance
from tests._g3_fakes import _StubEditor, _StubMemory, _StubPlanner, _make_plan
from tests.conftest import _build_minimal_project

S01 = "S01_器灵人性觉醒"


# ============================================================
# 桩与构造
# ============================================================
class _CountingLLM:
    """零 LLM 断言：任何调用都计数（G8 决策路径不应触发任何 LLM）。"""

    def __init__(self) -> None:
        self.calls = 0

    def _resp(self, text: str) -> LLMResponse:
        return LLMResponse(text=text, usage={"prompt_tokens": 0, "completion_tokens": 0}, raw={})

    def chat_creative(self, messages, **kwargs):
        self.calls += 1
        return self._resp("x")

    def chat_utility(self, messages, **kwargs):
        self.calls += 1
        return self._resp("{}")

    def chat(self, messages, **kwargs):
        self.calls += 1
        return self._resp("x")


def _subline_md(sid: str, curve_rows: list[tuple[str, str, str]] | None = None) -> str:
    body = (
        f"---\nsubline_id: \"{sid}\"\nsubline_name: \"{sid}\"\n"
        "status: \"planned\"\ncharacters: []\n---\n\n"
        "# 支线设定\n\n## 支线目标\n\n测试支线目标\n\n## 出场角色\n\n测试角色\n"
    )
    if curve_rows:
        rows = "\n".join(f"| {a} | {b} | {c} |" for a, b, c in curve_rows)
        body += f"\n## 剧集压力曲线\n\n| 阶段 | 章节 | 张力等级 |\n|---|---|---|\n{rows}\n"
    return body


def _make_g8_project(
    tmp_path: Path,
    n_sublines: int = 5,
    target: int = 30,
    plan_json: dict | None = None,
    curves: dict[str, list[tuple[str, str, str]]] | None = None,
    total_written: int = 0,
    current_subline: str = S01,
    subdir: str = "p",
) -> Path:
    """基于 _build_minimal_project 搭建含 n_sublines 条支线的 G8 测试项目。"""
    tmp_path = tmp_path / subdir
    d = _build_minimal_project(tmp_path, state=State.WRITING)
    # 跳过 M2 讨论（autoplan 幂等检查点），保证全程零 LLM
    (d / "discussion.md").write_text("# 讨论纪要\n\n已收敛。", encoding="utf-8")

    curves = curves or {}
    for i in range(1, n_sublines + 1):
        sid = S01 if i == 1 else f"S0{i}_支线{i}"
        sub_dir = d / "sublines" / sid
        sub_dir.mkdir(parents=True, exist_ok=True)
        (sub_dir / "subline.md").write_text(
            _subline_md(sid, curves.get(sid)), encoding="utf-8"
        )

    if plan_json is not None:
        plan_path = d / ".state" / "plan.json"
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(json.dumps(plan_json, ensure_ascii=False), encoding="utf-8")

    sm = StateMachine(d)
    sm.load()
    sm.progress = {
        "current_subline": current_subline,
        "current_chapter": total_written,
        "total_written": total_written,
        "last_written_at": "",
    }
    sm.save()
    return d


class _FakeWriter:
    """写章桩：复用 M5 上下文加载/落盘/进度更新（确定性、零 LLM），捕获每次任务。"""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = Path(project_dir)
        self.calls = 0
        self.tasks: list[str] = []
        self.ctx_list: list[dict] = []
        self.llm = _CountingLLM()

    def run(self, *args, **kwargs) -> SimpleNamespace:
        self.calls += 1
        m5 = M5WriteChapterWorkflow(
            project_dir=self.project_dir,
            llm_client=self.llm,
            console=Console(quiet=True),
            conflict_arbiter=None,
            pre_validate=False,
        )
        m5.state_machine.load()  # 与 M5.run（行 138）一致：先 load 再 _load_context
        ctx = m5._load_context()
        self.ctx_list.append(ctx)
        from agent.workflows.agentic_write import AgenticWriteWorkflow

        aw = AgenticWriteWorkflow(
            project_dir=self.project_dir, llm_client=self.llm, console=Console(quiet=True)
        )
        self.tasks.append(aw._build_task(ctx))
        # 落盘 chapters/ch<NNN>.md（frontmatter 含 subline 双保险字段）
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
            chapter_num=ctx["chapter_num"], chapter_text=text, chapter_title="x"
        )


def _make_pipeline(
    d: Path,
    writer: _FakeWriter,
    *,
    mainline_gate: bool = True,
    ending_gate: bool = True,
    ending_ratio: float = 0.25,
    mainline_window: int = 5,
    target: int = 30,
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
        mainline_window=mainline_window,
        ending_ratio=ending_ratio,
        mainline_gate=mainline_gate,
        ending_gate=ending_gate,
    )


def _read_progress(d: Path) -> dict:
    sm = StateMachine(d)
    sm.load()
    return dict(sm.progress or {})


# ============================================================
# 1. 自动切换 ≥3 条支线（无区间数据 → 每 window 章硬切）
# ============================================================
def test_mainline_auto_switches_at_least_3(tmp_path: Path) -> None:
    d = _make_g8_project(tmp_path, n_sublines=5, target=30)
    writer = _FakeWriter(d)
    p = _make_pipeline(d, writer, mainline_gate=True, ending_gate=False, target=30)
    result = p.run()

    progress = _read_progress(d)
    visited = progress.get("mainline_visited", [])
    assert result.final_chapter == 30
    assert len(visited) >= 3, f"应自动切换 ≥3 条支线，实际 {visited}"
    assert progress["current_subline"] == "S05_支线5", (
        f"最后一条支线应为 S05_支线5，实际 {progress['current_subline']}"
    )
    # 首条 S01 保留 + 每次切换追加新支线（去重）
    assert visited == [S01, "S02_支线2", "S03_支线3", "S04_支线4", "S05_支线5"]
    # result.mainline 摘要已填充
    assert result.mainline is not None
    assert result.mainline["mainline_visited"] == visited


# ============================================================
# 2. current_subline / mainline_visited 正确（含每 5 章切换时机）
# ============================================================
def test_mainline_switch_timing_by_window(tmp_path: Path) -> None:
    d = _make_g8_project(tmp_path, n_sublines=5, target=10)
    writer = _FakeWriter(d)
    p = _make_pipeline(d, writer, mainline_gate=True, ending_gate=False, target=10)
    p.run()
    progress = _read_progress(d)
    # 第 6 章前切 S01→S02；此后无决策点（11 未到）→ current=S02
    assert progress["current_subline"] == "S02_支线2"
    assert progress["mainline_visited"] == [S01, "S02_支线2"]


# ============================================================
# 3. 切换时机符合压力曲线区间上界
# ============================================================
def test_mainline_switch_follows_pressure_curve(tmp_path: Path) -> None:
    curves = {
        S01: [("铺垫", "1-5", "低")],
        "S02_支线2": [("铺垫", "1-10", "低")],
        "S03_支线3": [("铺垫", "1-15", "低")],
        "S04_支线4": [("铺垫", "1-20", "低")],
        "S05_支线5": [("铺垫", "1-100", "低")],
    }
    # 到第 10 章：ch6 越过 S01 上界 5 → 切 S02；ch11 未到（S02 上界 10 未越过）
    d = _make_g8_project(tmp_path, n_sublines=5, target=10, curves=curves)
    writer = _FakeWriter(d)
    p = _make_pipeline(d, writer, mainline_gate=True, ending_gate=False, target=10)
    p.run()
    progress = _read_progress(d)
    assert progress["current_subline"] == "S02_支线2"

    # 到第 21 章：ch6 切 S02、ch11 越 10 切 S03、ch16 越 15 切 S04、ch21 越 20 切 S05
    d2 = _make_g8_project(tmp_path, n_sublines=5, target=21, curves=curves, subdir="p2")
    writer2 = _FakeWriter(d2)
    p2 = _make_pipeline(d2, writer2, mainline_gate=True, ending_gate=False, target=21)
    p2.run()
    progress2 = _read_progress(d2)
    assert progress2["current_subline"] == "S05_支线5"
    assert progress2["mainline_visited"] == [S01, "S02_支线2", "S03_支线3", "S04_支线4", "S05_支线5"]


# ============================================================
# 4. 多源合一取 max（pressure 5 vs episode 8 → 第 11 章前才切）
# ============================================================
def test_mainline_upper_takes_max_of_pressure_and_episode(tmp_path: Path) -> None:
    curves = {S01: [("铺垫", "1-5", "低")]}
    plan_json = {
        "total_chapters": 30,
        "episode_tree": [
            {"id": "a1", "name": "弧1", "chapter_start": 1, "chapter_end": 8,
             "goal": "", "subline_id": S01},
        ],
    }
    # U = max(5, 8) = 8 → 第 6 章前（6<=8）不切；第 11 章前（11>8）才切
    d = _make_g8_project(tmp_path, n_sublines=2, target=10, plan_json=plan_json, curves=curves)
    writer = _FakeWriter(d)
    p = _make_pipeline(d, writer, mainline_gate=True, ending_gate=False, target=10)
    p.run()
    progress = _read_progress(d)
    assert progress["current_subline"] == S01, "U=8 时第 6 章前不应切换（max 规则防过早切）"

    d2 = _make_g8_project(tmp_path, n_sublines=2, target=11, plan_json=plan_json, curves=curves, subdir="p2")
    writer2 = _FakeWriter(d2)
    p2 = _make_pipeline(d2, writer2, mainline_gate=True, ending_gate=False, target=11)
    p2.run()
    progress2 = _read_progress(d2)
    assert progress2["current_subline"] == "S02_支线2", "第 11 章前已越过 U=8 应切换"


# ============================================================
# 5. 零 LLM（monkeypatch 计数为 0）
# ============================================================
def test_mainline_zero_llm(tmp_path: Path) -> None:
    d = _make_g8_project(tmp_path, n_sublines=5, target=30)
    writer = _FakeWriter(d)
    p = _make_pipeline(d, writer, mainline_gate=True, ending_gate=False, target=30)
    p.run()
    assert writer.llm.calls == 0, f"G8 主线推进应零 LLM，实际 {writer.llm.calls} 次"


# ============================================================
# 6. --no-mainline-gate → 不决策不切换（零回归）
# ============================================================
def test_mainline_gate_off_no_switch(tmp_path: Path) -> None:
    d = _make_g8_project(tmp_path, n_sublines=5, target=30)
    writer = _FakeWriter(d)
    p = _make_pipeline(d, writer, mainline_gate=False, ending_gate=False, target=30)
    result = p.run()
    progress = _read_progress(d)
    assert progress["current_subline"] == S01, "关闭 mainline gate 不应切换支线"
    assert progress["mainline_visited"] == [S01], (
        "关闭 mainline gate 只保留 _update_progress 双保险打底（当前支线）"
    )
    assert result.mainline is None, "关闭 mainline gate → result.mainline 为 None"


# ============================================================
# 7. decide_mainline_advance 单元：结局模式禁新线 / 最后一条 / 脏数据
# ============================================================
def test_decide_mainline_advance_ending_mode_returns_none(tmp_path: Path) -> None:
    d = _make_g8_project(tmp_path, n_sublines=5, target=30)
    sm = StateMachine(d)
    sm.load()
    sm.progress = {"current_subline": S01, "total_written": 25, "ending_mode": True}
    sm.save()
    sm.load()
    # 即便 chapter=26 已越过任意区间，结局模式也应禁新线（拍板 5）
    assert decide_mainline_advance(d, sm, 5) is None


def test_decide_mainline_advance_last_subline_returns_none(tmp_path: Path) -> None:
    d = _make_g8_project(tmp_path, n_sublines=5, target=30)
    sm = StateMachine(d)
    sm.load()
    sm.progress = {"current_subline": "S05_支线5", "total_written": 25}
    sm.save()
    sm.load()
    assert decide_mainline_advance(d, sm, 5) is None


def test_decide_mainline_advance_no_current_or_no_sublines(tmp_path: Path) -> None:
    d = _make_g8_project(tmp_path, n_sublines=5, target=30)
    sm = StateMachine(d)
    sm.load()
    sm.progress = {"total_written": 25}  # 无 current_subline
    sm.save()
    sm.load()
    assert decide_mainline_advance(d, sm, 5) is None

    empty = tmp_path / "empty"
    empty.mkdir(exist_ok=True)
    sm2 = StateMachine(empty)
    sm2.load()
    assert decide_mainline_advance(empty, sm2, 5) is None  # 无支线
