"""Phase 1 离线测试（Agentic Loop + WriterAgent + AgenticWriteWorkflow）

不依赖网络 / 真实 LLM：通过注入 ``decide`` 与 ``quality_gate`` 实现完全离线验证。
覆盖：
- Agentic Loop：finish 交付 / 工具调用→观察→再 finish / 未知工具降级 / 迭代上限
- WriterAgent：tier=light 不修订、tier=auto 按门禁修订
- AgenticWriteWorkflow._build_task：纯函数，复用 M5 模板拼装任务（无文件/无 LLM）
- 动作协议 schema：AgentAction 的 required 含 action
"""

from __future__ import annotations

import pytest

from agent.core.agent_loop import AgentAction, AgentLoop, LoopResult
from agent.core.structured_output import pydantic_to_json_schema
from agent.core.tools.base import ToolResult
from agent.core.tools import registry
from agent.core.writer_agent import WriterAgent
from agent.workflows.agentic_write import AgenticWriteWorkflow


# ---------------------------------------------------------------------------
# 1. Agentic Loop
# ---------------------------------------------------------------------------
def _finish(draft: str) -> AgentAction:
    return AgentAction(think="done", action="finish", draft=draft)


def _call(tool: str, args: dict) -> AgentAction:
    return AgentAction(think="use tool", action="tool_call", tool=tool, args=args)


def test_loop_finish_immediately():
    loop = AgentLoop(tools=registry, decide=lambda m: _finish("章节正文内容"))
    res = loop.run("写第1章")
    assert isinstance(res, LoopResult)
    assert res.finished is True
    assert res.draft == "章节正文内容"
    assert len(res.steps) == 1
    assert res.steps[0].action == "finish"


def test_loop_tool_call_then_finish():
    calls = {"n": 0}

    def decide(messages):
        calls["n"] += 1
        if calls["n"] == 1:
            return _call("count_words", {"text": "你好世界"})
        return _finish("最终章节")

    loop = AgentLoop(tools=registry, decide=decide, max_iterations=8)
    res = loop.run("写第1章")
    assert res.finished is True
    assert res.draft == "最终章节"
    assert len(res.steps) == 2
    # 第一次工具调用的观察应是 count_words 的结构化结果
    step0 = res.steps[0]
    assert step0.action == "tool_call"
    assert step0.tool == "count_words"
    assert step0.observation["success"] is True
    assert step0.observation["data"]["cjk_chars"] == 4


def test_loop_unknown_tool_recovers():
    calls = {"n": 0}

    def decide(messages):
        calls["n"] += 1
        if calls["n"] == 1:
            return _call("no_such_tool", {"x": 1})
        return _finish("恢复后交付")

    loop = AgentLoop(tools=registry, decide=decide, max_iterations=8)
    res = loop.run("写第1章")
    assert res.finished is True
    assert res.draft == "恢复后交付"
    # 第一次未知工具应被降级为观察（不崩溃）
    assert res.steps[0].observation["success"] is False
    assert "未知工具" in res.steps[0].observation["error"]


def test_loop_max_iterations_reached():
    # decide 永远只调工具、不 finish → 应在上限处停止且 finished=False
    loop = AgentLoop(tools=registry, decide=lambda m: _call("count_words", {"text": "x"}), max_iterations=5)
    res = loop.run("写第1章")
    assert res.finished is False
    assert res.iterations == 5
    assert res.draft is None


def test_action_schema_required_action():
    schema = pydantic_to_json_schema(AgentAction)
    assert "action" in schema["required"]
    # think / tool / args / draft 都有默认值，不应是 required
    assert "think" not in schema["required"]
    assert "draft" not in schema["required"]


# ---------------------------------------------------------------------------
# 2. WriterAgent（注入 decide + quality_gate，离线）
# ---------------------------------------------------------------------------
def test_writer_light_no_revision():
    # light：即便门禁不通过也不修订
    gate_calls = {"n": 0}

    def gate(text, ctx):
        gate_calls["n"] += 1
        return False, {"issues": [{"rule_id": "open_hook", "severity": "error", "description": "缺钩子"}]}

    agent = WriterAgent(
        project_dir=".",
        tier="light",
        decide=lambda m: _finish("轻量章节"),
        quality_gate=gate,
    )
    text, rev, passed = agent.run("写第1章", ctx={})
    assert text == "轻量章节"
    assert rev == 0
    assert passed is False  # 透传门禁结果
    assert gate_calls["n"] == 1  # 仅首稿检查一次


def test_writer_auto_revises_until_pass():
    gate_state = {"calls": 0}

    def gate(text, ctx):
        gate_state["calls"] += 1
        if gate_state["calls"] == 1:
            return False, {"issues": [{"rule_id": "scene_ratio", "severity": "warn", "description": "场景不足"}]}
        return True, {"issues": []}

    agent = WriterAgent(
        project_dir=".",
        tier="auto",
        decide=lambda m: _finish(f"修订稿#{gate_state['calls']}"),
        quality_gate=gate,
    )
    text, rev, passed = agent.run("写第1章", ctx={})
    assert passed is True
    assert rev == 1  # 首稿未过 → 修订 1 次 → 通过
    assert gate_state["calls"] == 2


def test_writer_auto_caps_revisions():
    # 门禁始终不通过 → 修订次数受 tier 上限约束（auto 上限 3 起草 → 最多 2 次修订）
    def gate(text, ctx):
        return False, {"issues": [{"rule_id": "x", "severity": "error", "description": "始终不过"}]}

    agent = WriterAgent(
        project_dir=".",
        tier="auto",
        decide=lambda m: _finish("稿"),
        quality_gate=gate,
    )
    text, rev, passed = agent.run("写第1章", ctx={})
    assert passed is False
    assert rev == 2  # auto: 首稿 + 最多 2 次修订


# ---------------------------------------------------------------------------
# 3. AgenticWriteWorkflow._build_task（纯函数，无文件/无 LLM）
# ---------------------------------------------------------------------------
def _sample_ctx() -> dict:
    return {
        "world_info": {
            "title": "测试书",
            "tone": "冷峻",
            "pov": "第三人称限知",
            "rhythm": "紧凑",
            "chapter_length": 3000,
            "info_density": "中",
            "banned_elements": "无",
            "synopsis": "简介",
            "realm_system": "境界体系",
            "golden_finger_info": "金手指",
            "genre": "xianxia",
        },
        "chapter_num": 1,
        "subline_id": "s1",
        "subline_name": "支线一",
        "subline_goal": "目标",
        "pressure_stage": "铺垫",
        "tension_level": "低",
        "route_node_id": "N01",
        "route_milestone": "里程碑",
        "route_main_title": "主线",
        "route_main_result": "结果",
        "route_main_growth": "成长",
        "characters_info": "角色信息",
        "relations_info": "关系网",
        "foreshadow_task": "伏笔任务",
        "prev_chapter_summary": "前情",
        "rag_context": [],
        "open_debts": [],
        "characters_fingerprint": "指纹",
    }


def test_build_task_contains_key_fields():
    wf = AgenticWriteWorkflow(project_dir=".", tier="auto")
    task = wf._build_task(_sample_ctx())
    assert isinstance(task, str)
    assert "第 1 章" in task
    assert "测试书" in task
    assert "支线一" in task
    assert "N01" in task
    assert "伏笔任务" in task


def test_workflow_constructible():
    # 仅验证可构造（不调用 run，避免依赖真实项目/LLM）
    wf = AgenticWriteWorkflow(project_dir=".", tier="heavy")
    assert wf.tier == "heavy"
