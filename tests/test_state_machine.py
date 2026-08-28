"""状态机引擎单元测试"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.core.engine.state_machine import Event, State, StateMachine


@pytest.fixture
def sm(tmp_path: Path) -> StateMachine:
    """临时项目目录的状态机"""
    return StateMachine(project_dir=tmp_path)


def test_initial_state_is_init(sm: StateMachine) -> None:
    assert sm.state == State.INIT


def test_load_when_no_state_file(sm: StateMachine) -> None:
    """无 state.json 时默认 INIT"""
    sm.load()
    assert sm.state == State.INIT


def test_save_and_load_roundtrip(sm: StateMachine) -> None:
    """保存后加载应一致"""
    sm.state = State.WRITING
    sm.mode = "light"
    sm.progress = {"chapter_index": 5}
    sm.save()

    sm2 = StateMachine(project_dir=sm.project_dir)
    sm2.load()
    assert sm2.state == State.WRITING
    assert sm2.mode == "light"
    assert sm2.progress["chapter_index"] == 5


def test_global_commands_always_allowed(sm: StateMachine) -> None:
    """全局命令任意状态可用"""
    for state in State:
        sm.state = state
        assert sm.is_command_allowed("/help")
        assert sm.is_command_allowed("/mode")
        assert sm.is_command_allowed("/reset-state")


def test_command_allowed_in_correct_state(sm: StateMachine) -> None:
    """/write 在 WRITING 状态可用"""
    sm.state = State.WRITING
    assert sm.is_command_allowed("/write")


def test_command_rejected_in_wrong_state(sm: StateMachine) -> None:
    """/write 在 INIT 状态不可用"""
    sm.state = State.INIT
    assert not sm.is_command_allowed("/write")


def test_confirm_architecture_only_in_architecting(sm: StateMachine) -> None:
    """/confirm-architecture 仅在 ARCHITECTING 可用"""
    sm.state = State.INIT
    assert not sm.is_command_allowed("/confirm-architecture")
    sm.state = State.ARCHITECTING
    assert sm.is_command_allowed("/confirm-architecture")


def test_transition_init_to_configuring(sm: StateMachine) -> None:
    sm.transition(Event.START)
    assert sm.state == State.CONFIGURING


def test_transition_architecting_to_confirmed(sm: StateMachine) -> None:
    sm.state = State.ARCHITECTING
    sm.transition(Event.CONFIRM_ARCHITECTURE)
    assert sm.state == State.ARCH_CONFIRMED


def test_invalid_transition_raises(sm: StateMachine) -> None:
    """非法转换应抛 ValueError"""
    sm.state = State.INIT
    with pytest.raises(ValueError):
        sm.transition(Event.WRITE)


def test_allowed_commands_list(sm: StateMachine) -> None:
    """allowed_commands 应返回当前状态可用命令"""
    sm.state = State.WRITING
    cmds = sm.allowed_commands()
    assert "/write" in cmds
    assert "/help" in cmds
