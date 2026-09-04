"""M2 脉络讨论工作流单元测试

mock LLM，验证多轮对话、讨论纪要生成、状态转换。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent.client import LLMResponse
from agent.core.story.setting_manager import SettingManager
from agent.core.engine.state_machine import State, StateMachine
from agent.workflows.planning.m2_discuss import (
    M2DiscussWorkflow,
    M2Input,
)


@pytest.fixture
def mock_llm() -> MagicMock:
    """mock LLM，按调用次数返回不同响应"""
    from itertools import cycle
    from types import SimpleNamespace

    llm = MagicMock()
    responses = cycle([
        SimpleNamespace(text="这是一个关键问题：主角的动机是什么？"),
        SimpleNamespace(text="- 主角动机：复仇\n- 金手指：太虚镜\n- 主线：逆天修仙"),
    ])
    llm.chat.side_effect = responses
    return llm


@pytest.fixture
def project_with_world(tmp_path: Path) -> Path:
    """创建一个已含 world.md 的项目"""
    sm = SettingManager(tmp_path)
    sm.save_world(
        metadata={
            "title": "太虚镜",
            "scope": "long",
            "genre": "xiuxian",
            "style": {"tone": "热血"},
        },
        content=(
            "# 总设定集 · 太虚镜\n\n"
            "## 故事简介\n\n"
            "废柴少年偶得神秘传承太虚镜，踏上逆天修仙路\n\n"
            "## 世界观\n\n世界观内容"
        ),
    )
    # 设置状态为 DISCUSSING
    sm_state = StateMachine(tmp_path)
    sm_state.state = State.DISCUSSING
    sm_state.save()
    return tmp_path


@pytest.fixture
def workflow(
    project_with_world: Path, mock_llm: MagicMock
) -> M2DiscussWorkflow:
    return M2DiscussWorkflow(
        project_dir=project_with_world,
        llm_client=mock_llm,
        setting_manager=SettingManager(project_with_world),
        state_machine=StateMachine(project_with_world),
    )


def test_m2_generates_discussion_md(
    workflow: M2DiscussWorkflow, mock_llm: MagicMock
) -> None:
    """M2 应生成 discussion.md"""
    # 非交互模式：预设 2 个回答
    user_input = M2Input(
        max_rounds=5,
        preset_answers=["主角想为师父报仇", "/next"],
    )
    result = workflow.run(user_input=user_input)

    assert result.discussion_file.exists()
    content = result.discussion_file.read_text(encoding="utf-8")
    assert "太虚镜" in content
    assert "讨论过程" in content
    assert "讨论总结" in content


def test_m2_state_transitions_to_architecting(
    workflow: M2DiscussWorkflow
) -> None:
    """M2 完成后状态应转为 ARCHITECTING"""
    user_input = M2Input(preset_answers=["回答", "/next"])
    workflow.run(user_input=user_input)
    assert workflow.state_machine.state == State.ARCHITECTING


def test_m2_exits_on_next_command(workflow: M2DiscussWorkflow) -> None:
    """/next 命令应结束讨论"""
    user_input = M2Input(max_rounds=10, preset_answers=["/next"])
    result = workflow.run(user_input=user_input)

    # 只 1 轮（LLM 提问 + 用户 /next）
    assert result.rounds == 1


def test_m2_exits_on_done_command(workflow: M2DiscussWorkflow) -> None:
    """/done 命令也应结束讨论"""
    user_input = M2Input(max_rounds=10, preset_answers=["/done"])
    result = workflow.run(user_input=user_input)
    assert result.rounds == 1


def test_m2_max_rounds_limit(workflow: M2DiscussWorkflow, mock_llm: MagicMock) -> None:
    """达到 max_rounds 应停止"""
    user_input = M2Input(
        max_rounds=2,
        preset_answers=["回答1", "回答2", "回答3"],  # 第 3 个不会被用到
    )
    result = workflow.run(user_input=user_input)
    assert result.rounds == 2


def test_m2_includes_history_in_discussion(
    workflow: M2DiscussWorkflow
) -> None:
    """discussion.md 应包含对话历史"""
    user_input = M2Input(
        preset_answers=["主角动机是复仇", "/next"],
    )
    result = workflow.run(user_input=user_input)

    content = result.discussion_file.read_text(encoding="utf-8")
    assert "主角动机是复仇" in content
    assert "关键问题" in content  # LLM 的提问


def test_m2_summary_included(workflow: M2DiscussWorkflow) -> None:
    """discussion.md 应包含讨论总结"""
    user_input = M2Input(preset_answers=["回答", "/next"])
    result = workflow.run(user_input=user_input)

    content = result.discussion_file.read_text(encoding="utf-8")
    assert "讨论总结" in content
    assert "主角动机" in content  # mock 总结内容


def test_m2_rejects_wrong_state(tmp_path: Path, mock_llm: MagicMock) -> None:
    """非 DISCUSSING 状态应拒绝"""
    sm = SettingManager(tmp_path)
    sm.save_world({"title": "x"}, "# 总设定集")
    state = StateMachine(tmp_path)
    state.state = State.INIT  # 错误状态
    state.save()

    workflow = M2DiscussWorkflow(
        project_dir=tmp_path,
        llm_client=mock_llm,
        setting_manager=SettingManager(tmp_path),
        state_machine=StateMachine(tmp_path),
    )
    with pytest.raises(RuntimeError, match="不允许讨论"):
        workflow.run(user_input=M2Input(preset_answers=["/next"]))


def test_m2_requires_world_md(tmp_path: Path, mock_llm: MagicMock) -> None:
    """无 world.md 应报错"""
    state = StateMachine(tmp_path)
    state.state = State.DISCUSSING
    state.save()

    workflow = M2DiscussWorkflow(
        project_dir=tmp_path,
        llm_client=mock_llm,
        setting_manager=SettingManager(tmp_path),
        state_machine=StateMachine(tmp_path),
    )
    with pytest.raises(RuntimeError, match="world.md 不存在"):
        workflow.run(user_input=M2Input(preset_answers=["/next"]))


def test_m2_llm_called_with_world_info(
    workflow: M2DiscussWorkflow, mock_llm: MagicMock
) -> None:
    """LLM 应收到 world.md 的信息"""
    user_input = M2Input(preset_answers=["/next"])
    workflow.run(user_input=user_input)

    mock_llm.chat.assert_called()
    # 第一个 chat 调用是 chat_creative（讨论问题）
    chat_request = mock_llm.chat.call_args_list[0][0][0]
    messages = chat_request.messages
    # system prompt 应包含修仙小说创作顾问
    assert "修仙小说创作顾问" in messages[0]["content"]
    # user prompt 应包含标题
    assert "太虚镜" in messages[1]["content"]


def test_m2_state_persisted(workflow: M2DiscussWorkflow) -> None:
    """状态应持久化"""
    user_input = M2Input(preset_answers=["/next"])
    workflow.run(user_input=user_input)

    sm2 = StateMachine(workflow.project_dir)
    sm2.load()
    assert sm2.state == State.ARCHITECTING


def test_m2_discussion_md_has_frontmatter(
    workflow: M2DiscussWorkflow
) -> None:
    """discussion.md 应有 front matter"""
    user_input = M2Input(preset_answers=["回答", "/next"])
    workflow.run(user_input=user_input)

    content = result_file = workflow.discussion_file.read_text(encoding="utf-8")
    assert content.startswith("---")
    assert "title:" in content
    assert "rounds:" in content
