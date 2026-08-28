"""G11 写作方法模板测试（T6 验收，纯离线零 LLM）。

覆盖（对齐 G11/设计.md §3 / §9 T3）：
- load_method_text 内置模板选择写入 method.md；
- 读已有 project/method.md；缺失/空 → ("", "")；
- PlannerAgent.run user_msg 注入【写作方法模板】段（decide stub 捕获 messages）；
- m3_outline._llm_generate_outline user_prompt 注入（FakeLLM 捕获）；
- method_enabled=False 不注入。
"""

from __future__ import annotations

from pathlib import Path

from agent.agents.planner import PlannerAgent
from agent.core.story.method_style import load_method_text
from agent.prompts import G11_METHOD_INSTRUCTION_TEMPLATE


def _has_method_segment(text: str) -> bool:
    return "# 写作方法模板" in text and "不要生硬套用" in text


# ---------------------------------------------------------------- load_method_text
def test_load_method_text_builtin_writes_method_file(tmp_path: Path) -> None:
    text, name = load_method_text(tmp_path, enabled=True, method="three_act")
    assert name == "three_act"
    assert "三幕" in text or "第一幕" in text
    # 已写入 project/method.md（用户可再编辑）
    method_file = tmp_path / "method.md"
    assert method_file.exists()
    assert "三幕" in method_file.read_text(encoding="utf-8")


def test_load_method_text_project_file(tmp_path: Path) -> None:
    (tmp_path / "method.md").write_text("# 自定义模板\n\n按自己的节奏写。", encoding="utf-8")
    text, name = load_method_text(tmp_path, enabled=True)
    assert "按自己的节奏写" in text
    assert name == "自定义模板"


def test_load_method_text_missing(tmp_path: Path) -> None:
    text, name = load_method_text(tmp_path, enabled=True)
    assert text == "" and name == ""


def test_load_method_text_disabled(tmp_path: Path) -> None:
    (tmp_path / "method.md").write_text("不应注入", encoding="utf-8")
    text, name = load_method_text(tmp_path, enabled=False)
    assert text == "" and name == ""


def test_load_method_text_unknown_builtin(tmp_path: Path) -> None:
    text, name = load_method_text(tmp_path, enabled=True, method="no_such_template")
    assert text == "" and name == ""


# ---------------------------------------------------------------- PlannerAgent.run
class _DecideCapture:
    """捕获 messages 并返回合法 MasterPlan（避免 G4 分级重试）。"""

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.calls = 0

    def __call__(self, messages):
        from tests._g3_fakes import _make_plan

        self.messages = messages
        self.calls += 1
        plan = _make_plan()
        if hasattr(plan, "model_dump"):
            data = plan.model_dump()
        else:
            data = plan.dict()
        # 补齐 episode_tree（G4 显式关键字段检查要求非空 list）
        if not data.get("episode_tree"):
            data["episode_tree"] = [
                {
                    "id": "A1", "name": "开局", "chapter_start": 1,
                    "chapter_end": 12, "goal": "主线", "subline_id": "S01",
                }
            ]
        return data


def test_planner_injects_method(tmp_path: Path) -> None:
    (tmp_path / "method.md").write_text(
        "# 英雄之旅\n\n按英雄之旅组织全书。", encoding="utf-8"
    )
    cap = _DecideCapture()
    pl = PlannerAgent(tmp_path, decide=cap)
    pl.run("写一本修仙小说")
    assert cap.calls == 1
    user = cap.messages[1]["content"]
    assert _has_method_segment(user)
    assert "英雄之旅" in user


def test_planner_no_method(tmp_path: Path) -> None:
    cap = _DecideCapture()
    pl = PlannerAgent(tmp_path, decide=cap)
    pl.run("写一本修仙小说")
    assert not _has_method_segment(cap.messages[1]["content"])


def test_planner_method_disabled(tmp_path: Path) -> None:
    (tmp_path / "method.md").write_text("# 模板\n\n内容", encoding="utf-8")
    cap = _DecideCapture()
    pl = PlannerAgent(tmp_path, decide=cap, method_enabled=False)
    pl.run("写一本修仙小说")
    assert not _has_method_segment(cap.messages[1]["content"])


# ---------------------------------------------------------------- m3_outline
class _M3LLM:
    """捕获 user_prompt 并返回合法 JSON。"""

    def __init__(self) -> None:
        self.user_prompt: str = ""
        self.calls = 0

    def chat_creative(self, messages, *args, **kwargs):
        self.user_prompt = messages[1]["content"]
        self.calls += 1
        return type("R", (), {"text": '{"synopsis": "简介", "sublines": []}'})()


def _arch_data() -> dict:
    return {
        "title": "测试之书",
        "architecture": {
            "protagonist_triple": {"who": "主角", "want": "变强", "obstacle": "宿敌"},
            "main_plot": {
                "beginning": "开局", "development": "发展", "twist": "反转", "resolution": "结局",
            },
            "story_core": "核心", "sublines_preview": "支线", "conflict_nodes": "冲突",
            "theme": "主题", "ending": "结局", "emotional_tone": "燃",
            "synopsis": "简介",
        },
    }


def test_m3_injects_method(tmp_path: Path) -> None:
    from agent.workflows.m3_outline import M3OutlineWorkflow

    (tmp_path / "method.md").write_text("# 起承转合\n\n按起承转合组织。", encoding="utf-8")
    llm = _M3LLM()
    wf = M3OutlineWorkflow(tmp_path, llm_client=llm)
    wf._llm_generate_outline(
        {"scope": "long", "story_core": "核心", "title": "t", "banned": ""}, _arch_data()
    )
    assert _has_method_segment(llm.user_prompt)
    assert "起承转合" in llm.user_prompt


def test_m3_no_method(tmp_path: Path) -> None:
    from agent.workflows.m3_outline import M3OutlineWorkflow

    llm = _M3LLM()
    wf = M3OutlineWorkflow(tmp_path, llm_client=llm)
    wf._llm_generate_outline(
        {"scope": "long", "story_core": "核心", "title": "t", "banned": ""}, _arch_data()
    )
    assert not _has_method_segment(llm.user_prompt)


def test_m3_method_disabled(tmp_path: Path) -> None:
    from agent.workflows.m3_outline import M3OutlineWorkflow

    (tmp_path / "method.md").write_text("# 模板\n\n内容", encoding="utf-8")
    llm = _M3LLM()
    wf = M3OutlineWorkflow(tmp_path, llm_client=llm, method_enabled=False)
    wf._llm_generate_outline(
        {"scope": "long", "story_core": "核心", "title": "t", "banned": ""}, _arch_data()
    )
    assert not _has_method_segment(llm.user_prompt)
