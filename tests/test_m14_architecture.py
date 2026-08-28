"""M14 故事架构生成与确认门禁工作流单元测试

mock LLM，验证生成、迭代、确认、门禁、状态转换。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import frontmatter
import pytest

from agent.client import LLMClient, LLMResponse
from agent.core.story.setting_manager import SettingManager
from agent.core.engine.state_machine import State, StateMachine
from agent.workflows.m14_architecture import (
    M14ArchitectureWorkflow,
    M14ConfirmResult,
    M14GenerateResult,
)


# ------ 测试用 LLM 输出 ------

ARCHITECTURE_JSON = """{
  "story_core": "废柴少年凭借太虚镜推演功法，逆天改命推翻宗门垄断",
  "protagonist_triple": {
    "who": "林寻，灵根残缺的废柴少年",
    "want": "为师父报仇并推翻宗门垄断秩序",
    "obstacle": "宗门势力庞大，自身灵根残缺"
  },
  "main_plot": {
    "beginning": "林寻被逐出宗门，偶得太虚镜",
    "development": "推演功法暗中成长，结识逆命盟",
    "twist": "发现师父之死是宗门系统性恶行",
    "resolution": "推翻垄断秩序，重铸修仙界"
  },
  "sublines_preview": "- 太虚镜器灵觉醒\\n- 逆命盟内部派系博弈\\n- 天机阁追捕",
  "conflict_nodes": "- 镜灵与主角价值观冲突\\n- 逆命盟背叛\\n- 天机阁围剿",
  "theme": "知识共享对抗垄断掠夺",
  "ending": "主角证道，建立开放的新秩序",
  "emotional_tone": "热血中带有沉重底色",
  "synopsis": "末法时代，废柴少年林寻偶得太虚镜，踏上推翻宗门垄断的逆天之路。"
}"""

ARCHITECTURE_JSON_ITERATED = """{
  "story_core": "废柴少年凭借太虚镜推演功法，逆天改命推翻宗门垄断",
  "protagonist_triple": {
    "who": "林寻，灵根残缺的废柴少年",
    "want": "为师父报仇并推翻宗门垄断秩序",
    "obstacle": "宗门势力庞大，自身灵根残缺"
  },
  "main_plot": {
    "beginning": "林寻被逐出宗门，偶得太虚镜",
    "development": "推演功法暗中成长，结识逆命盟",
    "twist": "发现师父之死是宗门系统性恶行，且牵涉上古阴谋",
    "resolution": "推翻垄断秩序，重铸修仙界，镜灵牺牲"
  },
  "sublines_preview": "- 太虚镜器灵觉醒\\n- 逆命盟内部派系博弈\\n- 天机阁追捕",
  "conflict_nodes": "- 镜灵与主角价值观冲突\\n- 逆命盟背叛\\n- 天机阁围剿\\n- 上古阴谋浮出",
  "theme": "知识共享对抗垄断掠夺",
  "ending": "主角证道，镜灵牺牲换取新秩序诞生",
  "emotional_tone": "热血中带有沉重底色，结局悲壮",
  "synopsis": "末法时代，废柴少年林寻偶得太虚镜，踏上推翻宗门垄断的逆天之路。"
}"""


# ------ fixtures ------

@pytest.fixture
def mock_llm() -> MagicMock:
    """mock LLM"""
    llm = MagicMock(spec=LLMClient)
    llm.chat_creative.return_value = LLMResponse(
        text=ARCHITECTURE_JSON, usage={}, model="m"
    )
    return llm


@pytest.fixture
def mock_llm_iterate() -> MagicMock:
    """mock LLM，第二次调用返回迭代结果"""
    llm = MagicMock(spec=LLMClient)
    llm.chat_creative.side_effect = [
        LLMResponse(text=ARCHITECTURE_JSON, usage={}, model="m"),
        LLMResponse(text=ARCHITECTURE_JSON_ITERATED, usage={}, model="m"),
    ]
    return llm


@pytest.fixture
def project_with_world(tmp_path: Path) -> Path:
    """创建一个已含 world.md + discussion.md 的项目，状态为 ARCHITECTING"""
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
    (tmp_path / "discussion.md").write_text(
        "# 脉络讨论纪要\n\n## 讨论总结\n\n- 主角动机：复仇\n- 金手指：太虚镜\n",
        encoding="utf-8",
    )
    state = StateMachine(tmp_path)
    state.state = State.ARCHITECTING
    state.save()
    return tmp_path


@pytest.fixture
def workflow(
    project_with_world: Path, mock_llm: MagicMock
) -> M14ArchitectureWorkflow:
    return M14ArchitectureWorkflow(
        project_dir=project_with_world,
        llm_client=mock_llm,
        setting_manager=SettingManager(project_with_world),
        state_machine=StateMachine(project_with_world),
    ).with_confirm_yes(True)


@pytest.fixture
def workflow_iterate(
    project_with_world: Path, mock_llm_iterate: MagicMock
) -> M14ArchitectureWorkflow:
    return M14ArchitectureWorkflow(
        project_dir=project_with_world,
        llm_client=mock_llm_iterate,
        setting_manager=SettingManager(project_with_world),
        state_machine=StateMachine(project_with_world),
    ).with_confirm_yes(True)


# ============================================================
# generate()
# ============================================================

def test_m14_generate_creates_architecture_md(workflow: M14ArchitectureWorkflow) -> None:
    """generate 应创建 architecture.md"""
    result = workflow.generate()
    assert result.architecture_file.exists()
    content = result.architecture_file.read_text(encoding="utf-8")
    assert "太虚镜" in content
    assert "故事内核" in content
    assert "主角三要素" in content
    assert "主线脉络" in content


def test_m14_generate_returns_version_1(workflow: M14ArchitectureWorkflow) -> None:
    """初稿 version 应为 1"""
    result = workflow.generate()
    assert result.version == 1
    assert result.confirmed is False


def test_m14_generate_has_frontmatter(workflow: M14ArchitectureWorkflow) -> None:
    """architecture.md 应有 frontmatter 含 confirmed/version/title"""
    workflow.generate()
    post = frontmatter.load(workflow.architecture_file)
    assert post.metadata["title"] == "太虚镜"
    assert post.metadata["confirmed"] is False
    assert post.metadata["version"] == 1
    assert "created_at" in post.metadata
    assert "updated_at" in post.metadata


def test_m14_generate_includes_gate_notice(workflow: M14ArchitectureWorkflow) -> None:
    """architecture.md 应包含门禁说明"""
    workflow.generate()
    content = workflow.architecture_file.read_text(encoding="utf-8")
    assert "门禁" in content or "confirmed" in content


def test_m14_generate_llm_called_with_world_info(
    workflow: M14ArchitectureWorkflow, mock_llm: MagicMock
) -> None:
    """LLM 应被调用，且 prompt 含标题"""
    workflow.generate()
    mock_llm.chat_creative.assert_called_once()
    call_kwargs = mock_llm.chat_creative.call_args
    messages = call_kwargs.kwargs["messages"]
    user_msg = next(m["content"] for m in messages if m["role"] == "user")
    assert "太虚镜" in user_msg


def test_m14_generate_rejects_wrong_state(
    project_with_world: Path, mock_llm: MagicMock
) -> None:
    """非 ARCHITECTING 状态应拒绝"""
    sm = StateMachine(project_with_world)
    sm.state = State.DISCUSSING
    sm.save()
    wf = M14ArchitectureWorkflow(
        project_dir=project_with_world,
        llm_client=mock_llm,
        setting_manager=SettingManager(project_with_world),
        state_machine=StateMachine(project_with_world),
    )
    with pytest.raises(RuntimeError, match="不允许生成架构"):
        wf.generate()


def test_m14_generate_requires_world_md(tmp_path: Path, mock_llm: MagicMock) -> None:
    """world.md 不存在应报错"""
    state = StateMachine(tmp_path)
    state.state = State.ARCHITECTING
    state.save()
    wf = M14ArchitectureWorkflow(
        project_dir=tmp_path,
        llm_client=mock_llm,
        setting_manager=SettingManager(tmp_path),
        state_machine=StateMachine(tmp_path),
    )
    with pytest.raises(RuntimeError, match="world.md 不存在"):
        wf.generate()


# ============================================================
# iterate()
# ============================================================

def test_m14_iterate_increments_version(workflow_iterate: M14ArchitectureWorkflow) -> None:
    """迭代后 version 应 +1"""
    workflow_iterate.generate()
    result = workflow_iterate.iterate("把结局改成悲壮的，镜灵牺牲")
    assert result.version == 2


def test_m14_iterate_resets_confirmed(workflow_iterate: M14ArchitectureWorkflow) -> None:
    """迭代后 confirmed 应重置为 false"""
    workflow_iterate.generate()
    workflow_iterate.iterate("修改结局")
    post = frontmatter.load(workflow_iterate.architecture_file)
    assert post.metadata["confirmed"] is False


def test_m14_iterate_requires_existing_architecture(
    workflow: M14ArchitectureWorkflow,
) -> None:
    """architecture.md 不存在时迭代应报错"""
    with pytest.raises(RuntimeError, match="architecture.md 不存在"):
        workflow.iterate("some feedback")


def test_m14_iterate_uses_feedback_in_prompt(
    workflow_iterate: M14ArchitectureWorkflow, mock_llm_iterate: MagicMock
) -> None:
    """迭代 prompt 应包含用户反馈"""
    workflow_iterate.generate()
    workflow_iterate.iterate("让结局更悲壮")
    # 第二次 chat_creative 调用是迭代
    second_call = mock_llm_iterate.chat_creative.call_args_list[1]
    user_msg = next(
        m["content"] for m in second_call.kwargs["messages"] if m["role"] == "user"
    )
    assert "让结局更悲壮" in user_msg


# ============================================================
# confirm()
# ============================================================

def test_m14_confirm_writes_confirmed_true(workflow: M14ArchitectureWorkflow) -> None:
    """确认后 confirmed 应为 true"""
    workflow.generate()
    result = workflow.confirm()
    assert result.confirmed is True
    post = frontmatter.load(workflow.architecture_file)
    assert post.metadata["confirmed"] is True
    assert post.metadata["confirmed_at"] != ""


def test_m14_confirm_transitions_to_arch_confirmed(
    workflow: M14ArchitectureWorkflow,
) -> None:
    """确认后状态应转为 ARCH_CONFIRMED"""
    workflow.generate()
    workflow.confirm()
    workflow.state_machine.load()
    assert workflow.state_machine.state == State.ARCH_CONFIRMED


def test_m14_confirm_requires_existing_architecture(
    workflow: M14ArchitectureWorkflow,
) -> None:
    """architecture.md 不存在时确认应报错"""
    with pytest.raises(RuntimeError, match="architecture.md 不存在"):
        workflow.confirm()


def test_m14_confirm_rejects_already_confirmed(workflow: M14ArchitectureWorkflow) -> None:
    """已确认的架构（状态被手动改回 ARCHITECTING 时）不能重复确认"""
    workflow.generate()
    workflow.confirm()
    # 模拟用户在 ARCH_REVISION 状态下未重新迭代就尝试确认
    workflow.state_machine.state = State.ARCHITECTING
    workflow.state_machine.save()
    with pytest.raises(RuntimeError, match="已确认"):
        workflow.confirm()


def test_m14_confirm_returns_unlocked_stages(workflow: M14ArchitectureWorkflow) -> None:
    """确认结果应包含解锁的下游阶段列表"""
    workflow.generate()
    result = workflow.confirm()
    assert len(result.unlocked_stages) > 0
    assert any("大纲" in s or "角色" in s or "写作" in s for s in result.unlocked_stages)


def test_m14_confirm_rejects_wrong_state(
    project_with_world: Path, mock_llm: MagicMock
) -> None:
    """非 ARCHITECTING 状态确认应拒绝"""
    wf = M14ArchitectureWorkflow(
        project_dir=project_with_world,
        llm_client=mock_llm,
        setting_manager=SettingManager(project_with_world),
        state_machine=StateMachine(project_with_world),
    ).with_confirm_yes(True)
    # 手动写一个 architecture.md
    wf.architecture_file.write_text(
        "---\nconfirmed: false\nversion: 1\n---\n# 架构\n", encoding="utf-8"
    )
    # 改成错误状态
    sm = StateMachine(project_with_world)
    sm.state = State.DISCUSSING
    sm.save()
    with pytest.raises(RuntimeError, match="不允许确认架构"):
        wf.confirm()


# ============================================================
# check_confirmed() 门禁
# ============================================================

def test_check_confirmed_returns_false_when_no_file(tmp_path: Path) -> None:
    """architecture.md 不存在时门禁应返回 False"""
    assert M14ArchitectureWorkflow.check_confirmed(tmp_path) is False


def test_check_confirmed_returns_false_when_not_confirmed(
    workflow: M14ArchitectureWorkflow,
) -> None:
    """confirmed != true 时门禁应返回 False"""
    workflow.generate()
    assert M14ArchitectureWorkflow.check_confirmed(workflow.project_dir) is False


def test_check_confirmed_returns_true_after_confirm(
    workflow: M14ArchitectureWorkflow,
) -> None:
    """确认后门禁应返回 True"""
    workflow.generate()
    workflow.confirm()
    assert M14ArchitectureWorkflow.check_confirmed(workflow.project_dir) is True


def test_check_confirmed_accepts_string_path(workflow: M14ArchitectureWorkflow) -> None:
    """check_confirmed 应接受字符串路径"""
    workflow.generate()
    workflow.confirm()
    assert M14ArchitectureWorkflow.check_confirmed(str(workflow.project_dir)) is True


# ============================================================
# 全流程
# ============================================================

def test_m14_full_workflow_generate_iterate_confirm(
    workflow_iterate: M14ArchitectureWorkflow,
) -> None:
    """全流程：生成 → 迭代 → 确认"""
    # 1. 生成初稿
    r1 = workflow_iterate.generate()
    assert r1.version == 1
    assert r1.confirmed is False

    # 2. 迭代
    r2 = workflow_iterate.iterate("把结局改悲壮，镜灵牺牲")
    assert r2.version == 2
    assert r2.confirmed is False

    # 3. 确认
    r3 = workflow_iterate.confirm()
    assert r3.confirmed is True
    assert r3.version == 2

    # 门禁通过
    assert M14ArchitectureWorkflow.check_confirmed(workflow_iterate.project_dir) is True

    # 状态转换
    workflow_iterate.state_machine.load()
    assert workflow_iterate.state_machine.state == State.ARCH_CONFIRMED
