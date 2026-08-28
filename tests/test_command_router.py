"""命令路由器单元测试"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.core.engine.command_router import CommandRouter
from agent.core.engine.state_machine import State, StateMachine


@pytest.fixture
def router(tmp_path: Path) -> CommandRouter:
    sm = StateMachine(project_dir=tmp_path)
    return CommandRouter(state_machine=sm)


def test_parse_command(router: CommandRouter) -> None:
    result = router.parse("/write")
    assert result == ("/write", {})


def test_parse_command_with_args(router: CommandRouter) -> None:
    result = router.parse("/rollback ch020")
    assert result is not None
    assert result[0] == "/rollback"
    assert result[1]["raw"] == "ch020"


def test_parse_dialog_returns_none(router: CommandRouter) -> None:
    """自由对话返回 None"""
    assert router.parse("hello, 我想写个修仙小说") is None


def test_route_command_allowed(router: CommandRouter) -> None:
    """允许的命令应路由到 handler"""
    called = []
    router.register("/help", lambda **kw: called.append(True))
    result = router.route("/help")
    assert result.type == "command"
    assert result.handler is not None


def test_route_command_rejected_by_state(router: CommandRouter) -> None:
    """/write 在 INIT 状态应被门禁拒绝"""
    router.sm.state = State.INIT
    result = router.route("/write")
    assert result.type == "rejected"
    assert "不可用" in (result.reason or "")


def test_route_command_not_registered(router: CommandRouter) -> None:
    """已注册但无 handler 的命令"""
    router.sm.state = State.WRITING
    result = router.route("/write")
    assert result.type == "rejected"
    assert "未注册" in (result.reason or "")


def test_route_dialog(router: CommandRouter) -> None:
    result = router.route("hello world")
    assert result.type == "dialog"
