"""enforce_gate 与命令元数据驱动门禁的测试

覆盖（T-6：门禁改由 CommandMeta.allowed_states / is_global 派生，去除分散的状态-命令映射表）：
- 门禁完整性：COMMAND_REGISTRY 中每个命令都应声明 allowed_states 或 is_global
  （否则门禁无法派生）。
- enforce_gate 在正确阶段放行、在错误阶段以 exit_code=2 拦截。
- 未初始化的项目（无 state.json）放行。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import typer

from agent.core.command_router import COMMAND_REGISTRY
from agent.core.state_machine import State, StateMachine


def test_gate_map_covers_all_registered_commands() -> None:
    """COMMAND_REGISTRY 中每个命令都应有门禁派生字段（allowed_states 或 is_global）"""
    for cmd in COMMAND_REGISTRY:
        assert cmd.allowed_states is not None or cmd.is_global, (
            f"{cmd.name} 缺少 allowed_states 或 is_global（门禁无法派生）"
        )


def test_global_aux_commands_always_allowed() -> None:
    """辅助命令（status/snapshot/inject-genre）在任意状态下均可用（is_global）"""
    sm = StateMachine(Path("/tmp/__noop__"))
    for state in State:
        sm.state = state
        for cmd in ("/status", "/snapshot", "/inject-genre"):
            assert sm.is_command_allowed(cmd), f"{cmd} 应在 {state} 下可用（is_global）"


def test_enforce_gate_passes_when_uninitialized(tmp_path: Path) -> None:
    """未初始化项目（无 state.json）放行，不抛异常"""
    from agent.cli._shared import enforce_gate

    enforce_gate(str(tmp_path), "write")  # 不应抛异常


def test_enforce_gate_blocks_disallowed_state(tmp_path: Path) -> None:
    """enforce_gate 在错误阶段应以 exit_code=2 拦截"""
    from agent.cli._shared import enforce_gate

    sm = StateMachine(tmp_path)
    sm.state = State.INIT  # /write 在 INIT 不可用
    sm.save()
    with pytest.raises(typer.Exit) as exc:
        enforce_gate(str(tmp_path), "write")
    assert exc.value.exit_code == 2


def test_enforce_gate_allows_correct_state(tmp_path: Path) -> None:
    """enforce_gate 在正确阶段应放行（不抛异常）"""
    from agent.cli._shared import enforce_gate

    sm = StateMachine(tmp_path)
    sm.state = State.WRITING  # /write 在 WRITING 可用
    sm.save()
    enforce_gate(str(tmp_path), "write")  # 不应抛异常
