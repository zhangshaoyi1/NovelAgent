"""M1 启动配置工作流单元测试

mock LLM，验证流程正确性，不真实调用 API。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent.client import LLMClient, LLMConfig, LLMResponse
from agent.core.story.setting_manager import SettingManager
from agent.core.engine.state_machine import State, StateMachine
from agent.workflows.m1_config import M1ConfigWorkflow, M1Input


@pytest.fixture
def mock_llm() -> MagicMock:
    """mock LLMClient，返回固定的世界观 JSON"""
    llm = MagicMock(spec=LLMClient)
    llm.chat_creative.return_value = LLMResponse(
        text=(
            '{"synopsis": "废柴少年得传承，逆天修仙",'
            '"worldview": "九天大陆，灵气复苏的时代",'
            '"power_system": "灵根+功法+法力体系",'
            '"factions": "- 玄天宗\\n- 魔道\\n- 散修联盟",'
            '"golden_finger": "太虚镜，可推演功法"}'
        ),
        usage={"total_tokens": 100},
        model="test-model",
    )
    return llm


@pytest.fixture
def workflow(tmp_path: Path, mock_llm: MagicMock) -> M1ConfigWorkflow:
    return M1ConfigWorkflow(
        project_dir=tmp_path,
        llm_client=mock_llm,
        setting_manager=SettingManager(tmp_path),
        state_machine=StateMachine(tmp_path),
    )


@pytest.fixture
def sample_input() -> M1Input:
    return M1Input(
        title="测试修仙录",
        scope="long",
        genre="xiuxian",
        style={
            "tone": "热血",
            "pov": "第三人称限制",
            "rhythm": "快",
            "chapter_length": 3000,
            "info_density": "中",
            "banned_elements": [],
        },
        story_core="废柴少年偶得神秘传承，踏上逆天修仙路",
    )


def test_m1_generates_world_md(workflow: M1ConfigWorkflow, sample_input: M1Input) -> None:
    """M1 应生成 world.md 文件"""
    result = workflow.run(user_input=sample_input)

    assert result.world_file.exists()
    content = result.world_file.read_text(encoding="utf-8")
    assert "测试修仙录" in content
    assert "废柴少年得传承" in content  # synopsis
    assert "九天大陆" in content  # worldview


def test_m1_metadata_has_frozen_fields(
    workflow: M1ConfigWorkflow, sample_input: M1Input
) -> None:
    """生成的 world.md 应包含 frozen_fields"""
    workflow.run(user_input=sample_input)

    data = workflow.sm.load_world()
    assert "frozen_fields" in data["metadata"]
    assert "realm_system" in data["metadata"]["frozen_fields"]
    assert "golden_finger_limits" in data["metadata"]["frozen_fields"]


def test_m1_includes_realm_system_template(
    workflow: M1ConfigWorkflow, sample_input: M1Input
) -> None:
    """world.md 应包含修仙题材包的境界体系模板"""
    result = workflow.run(user_input=sample_input)
    content = result.world_file.read_text(encoding="utf-8")

    # 修仙题材包模板包含这些境界
    assert "炼气" in content
    assert "筑基" in content
    assert "金丹" in content


def test_m1_includes_style_config(
    workflow: M1ConfigWorkflow, sample_input: M1Input
) -> None:
    """world.md 应包含风格配置"""
    result = workflow.run(user_input=sample_input)
    content = result.world_file.read_text(encoding="utf-8")

    assert "热血" in content
    assert "第三人称限制" in content
    assert "3000" in content  # chapter_length


def test_m1_state_transitions_to_discussing(
    workflow: M1ConfigWorkflow, sample_input: M1Input
) -> None:
    """M1 完成后状态机应处于 DISCUSSING"""
    workflow.run(user_input=sample_input)
    assert workflow.state_machine.state == State.DISCUSSING


def test_m1_state_persisted(workflow: M1ConfigWorkflow, sample_input: M1Input) -> None:
    """状态应持久化到 state.json"""
    workflow.run(user_input=sample_input)

    # 新建一个 StateMachine 加载，验证持久化
    sm2 = StateMachine(workflow.project_dir)
    sm2.load()
    assert sm2.state == State.DISCUSSING


def test_m1_fills_default_style(workflow: M1ConfigWorkflow) -> None:
    """未提供 style 时应填充默认值"""
    user_input = M1Input(title="x", scope="short", story_core="核心")
    workflow.run(user_input=user_input)

    data = workflow.sm.load_world()
    style = data["metadata"]["style"]
    assert style["tone"] == "热血"
    assert style["chapter_length"] == 3000


def test_m1_llm_called_with_correct_prompt(
    workflow: M1ConfigWorkflow, mock_llm: MagicMock, sample_input: M1Input
) -> None:
    """LLM 应被调用，且 system prompt 正确"""
    workflow.run(user_input=sample_input)

    mock_llm.chat_creative.assert_called_once()
    call_args = mock_llm.chat_creative.call_args
    messages = call_args.kwargs["messages"]
    assert messages[0]["role"] == "system"
    assert "修仙小说世界观设计师" in messages[0]["content"]
    assert messages[1]["role"] == "user"
    assert "测试修仙录" in messages[1]["content"]
    assert "废柴少年偶得神秘传承" in messages[1]["content"]


def test_m1_handles_llm_json_parse_failure(tmp_path: Path) -> None:
    """LLM 返回非 JSON 时重试一次，仍失败应明确抛错而非静默写残缺产物。"""
    bad_llm = MagicMock(spec=LLMClient)
    bad_llm.chat_creative.return_value = LLMResponse(
        text="这不是 JSON，只是普通文本输出",
        usage={},
        model="m",
    )
    workflow = M1ConfigWorkflow(
        project_dir=tmp_path,
        llm_client=bad_llm,
        setting_manager=SettingManager(tmp_path),
        state_machine=StateMachine(tmp_path),
    )
    user_input = M1Input(title="降级测试", scope="short", story_core="核心")
    with pytest.raises(RuntimeError, match="无法解析为 JSON"):
        workflow.run(user_input=user_input)

    # 不应静默写入残缺 world.md
    assert not (tmp_path / "world.md").exists()
    # 递增重试（充足预算 + 纯 JSON 强化）：共调用三次后仍失败才抛错
    assert bad_llm.chat_creative.call_count == 3


def test_m1_creates_project_dir_if_not_exists(
    tmp_path: Path, mock_llm: MagicMock, sample_input: M1Input
) -> None:
    """项目目录不存在时应自动创建"""
    project_dir = tmp_path / "nested" / "new_project"
    workflow = M1ConfigWorkflow(
        project_dir=project_dir,
        llm_client=mock_llm,
        setting_manager=SettingManager(project_dir),
        state_machine=StateMachine(project_dir),
    )
    workflow.run(user_input=sample_input)

    assert project_dir.exists()
    assert (project_dir / "world.md").exists()
