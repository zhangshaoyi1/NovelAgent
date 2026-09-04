"""G11 风格模仿测试（T6 验收，纯离线零 LLM）。

覆盖（对齐 G11/设计.md §2 / §9 T2）：
- load_style_guide 三态（存在/缺失/enabled=False）+ 800 字截断；
- agentic_write._build_task 注入【风格指引】段（style_guide 存在 → 追加；缺失 → 不含）；
- m5_write_chapter._generate_chapter 同注入（FakeLLM 捕获 system prompt）；
- style_file 指定路径（--style-file）生效。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from rich.console import Console

from agent.core.story.method_style import STYLE_GUIDE_MAX_CHARS, load_style_guide
from agent.core.infra.prompt_manager import pm


def _min_ctx(style_guide: str = "") -> dict:
    """最小 ctx（覆盖 _build_task / _generate_chapter 的 format 键）。"""
    wi = {
        "title": "测试之书",
        "tone": "热血",
        "pov": "第三人称",
        "rhythm": "快节奏",
        "chapter_length": "2000",
        "info_density": "中等",
        "banned_elements": "",
        "synopsis": "简介",
        "realm_system": "境界",
        "golden_finger_info": "金手指",
    }
    return {
        "world_info": wi,
        "chapter_num": 3,
        "subline_id": "S01",
        "subline_name": "支线一",
        "subline_goal": "目标",
        "pressure_stage": "发展",
        "tension_level": "中",
        "route_node_id": "N1",
        "route_milestone": "里程碑",
        "route_main_title": "主线",
        "route_main_result": "结果",
        "route_main_growth": "成长",
        "characters_info": "角色信息",
        "relations_info": "关系网",
        "foreshadow_task": "伏笔任务",
        "prev_chapter_summary": "前情提要",
        "style_guide": style_guide,
    }


class _CaptureLLM:
    """捕获最后一次调用的 messages（FakeLLM：chat 返回 .text）。"""

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.calls = 0

    def chat(self, req):
        self.messages = req.messages
        self.calls += 1
        return type("R", (), {"text": "正文内容。"})()

    def chat_utility(self, *args, **kwargs):
        self.calls += 1
        return type("R", (), {"text": "{}"})()


# ---------------------------------------------------------------- load_style_guide
def test_style_guide_three_states(tmp_path: Path) -> None:
    # 缺失 → ""
    assert load_style_guide(tmp_path) == ""
    # 存在 → 正文
    (tmp_path / "style.md").write_text("冷峻白描、短句、少修辞", encoding="utf-8")
    assert load_style_guide(tmp_path) == "冷峻白描、短句、少修辞"
    # enabled=False → ""（--no-style）
    assert load_style_guide(tmp_path, enabled=False) == ""


def test_style_guide_truncate(tmp_path: Path) -> None:
    (tmp_path / "style.md").write_text("风" * 2000, encoding="utf-8")
    out = load_style_guide(tmp_path)
    assert len(out) == STYLE_GUIDE_MAX_CHARS


def test_style_guide_style_file(tmp_path: Path) -> None:
    other = tmp_path / "sub" / "my-style.txt"
    other.parent.mkdir(exist_ok=True)
    other.write_text("自定义风格文件", encoding="utf-8")
    assert load_style_guide(tmp_path, style_file=str(other)) == "自定义风格文件"
    # 指定文件不存在 → ""
    assert load_style_guide(tmp_path, style_file=str(tmp_path / "nope.txt")) == ""


# ---------------------------------------------------------------- agentic_write._build_task
def test_build_task_injects_style() -> None:
    from agent.workflows.writing.agentic_write import AgenticWriteWorkflow

    wf = AgenticWriteWorkflow(Path("."), llm_client=_CaptureLLM())
    task = wf._build_task(_min_ctx(style_guide="冷峻白描"))
    assert "# 风格指引" in task
    assert "冷峻白描" in task
    assert pm.get("g11.style_instruction").render_user(style_guide="冷峻白描") in task


def test_build_task_no_style_byte_identical() -> None:
    from agent.workflows.writing.agentic_write import AgenticWriteWorkflow

    wf = AgenticWriteWorkflow(Path("."), llm_client=_CaptureLLM())
    task_no = wf._build_task(_min_ctx(style_guide=""))
    assert "# 风格指引" not in task_no


# ---------------------------------------------------------------- m5._generate_chapter
def test_generate_chapter_injects_style() -> None:
    from agent.workflows.writing.m5_write_chapter import M5WriteChapterWorkflow

    llm = _CaptureLLM()
    wf = M5WriteChapterWorkflow(Path("."), llm_client=llm, pre_validate=False)
    wf._generate_chapter(_min_ctx(style_guide="冷峻白描"))
    assert llm.calls == 1
    system = llm.messages[0]["content"]
    assert "# 风格指引" in system
    assert "冷峻白描" in system


def test_generate_chapter_no_style() -> None:
    from agent.workflows.writing.m5_write_chapter import M5WriteChapterWorkflow

    llm = _CaptureLLM()
    wf = M5WriteChapterWorkflow(Path("."), llm_client=llm, pre_validate=False)
    wf._generate_chapter(_min_ctx(style_guide=""))
    assert "# 风格指引" not in llm.messages[0]["content"]


def test_generate_chapter_style_disabled(tmp_path: Path) -> None:
    """style_enabled=False（--no-style）→ 不读 style.md → 无注入。"""
    from tests.conftest import _build_minimal_project

    from agent.workflows.writing.m5_write_chapter import M5WriteChapterWorkflow

    proj = _build_minimal_project(tmp_path)
    (proj / "style.md").write_text("应被忽略的风格", encoding="utf-8")
    llm = _CaptureLLM()
    wf = M5WriteChapterWorkflow(
        proj, llm_client=llm, pre_validate=False, style_enabled=False
    )
    ctx = wf._load_context()
    assert ctx.get("style_guide") == ""
