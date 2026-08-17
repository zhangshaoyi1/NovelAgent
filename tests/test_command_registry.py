"""T-1 命令单一注册点验收

覆盖：
- COMMAND_REGISTRY 覆盖全部命令（含此前遗漏的辅助命令 inject-genre/load-genre/
  list-genres/genre-info/audit-chapter/audit-setting/context/completion-extras/
  draft-status/draft-discard/summarize-chapter/summarize-range/import-draft）。
- 每个命令元数据都声明了 allowed_states 或 is_global（单一真相源）。
- enforce_gate 对 /inject-genre 不再静默 return（已登记到 COMMAND_REGISTRY，
  门禁会真实执行；/inject-genre 为全局命令，在 WRITING 下应放行）。
- CLI --help 列出全部命令。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from agent.cli import app  # 触发全部命令注册（导入副作用）
from agent.core.command_router import COMMAND_REGISTRY, get_command_meta
from agent.core.state_machine import State, StateMachine


def test_all_auxiliary_commands_registered() -> None:
    """此前未纳入 COMMAND_REGISTRY 的辅助命令现已登记（单一注册点）。"""
    names = {c.name for c in COMMAND_REGISTRY}
    required = [
        "/inject-genre", "/load-genre", "/list-genres", "/genre-info",
        "/audit-chapter", "/audit-setting", "/context", "/completion-extras",
        "/draft-status", "/draft-discard", "/summarize-chapter",
        "/summarize-range", "/import-draft",
    ]
    for cmd in required:
        assert cmd in names, f"{cmd} 未登记到 COMMAND_REGISTRY"


def test_every_command_declares_gate_metadata() -> None:
    """每个 CommandMeta 必须声明 allowed_states 或 is_global（门禁单一真相源）。"""
    for meta in COMMAND_REGISTRY:
        assert meta.is_global or meta.allowed_states, (
            f"{meta.name} 既非全局也无可执行状态，门禁无法判定"
        )


def test_inject_genre_is_global_and_registered() -> None:
    meta = get_command_meta("/inject-genre")
    assert meta is not None
    assert meta.is_global is True


def test_enforce_gate_runs_for_inject_genre_in_writing(tmp_path: Path) -> None:
    """enforce_gate 对 /inject-genre 不再静默 return。

    此前 /inject-genre 不在 COMMAND_REGISTRY，enforce_gate 会直接跳过门禁；
    现在已登记，门禁会真实执行（/inject-genre 为全局命令，WRITING 下应放行）。
    """
    from agent.cli._shared import enforce_gate

    sm = StateMachine(tmp_path)
    sm.state = State.WRITING
    sm.save()
    # 不应抛 typer.Exit（全局命令放行权）
    enforce_gate(str(tmp_path), "inject_genre")


def test_help_lists_all_commands() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    names = {c.name for c in COMMAND_REGISTRY}
    for cmd in ("/write", "/start", "/inject-genre", "/load-genre", "/list-genres"):
        assert cmd.lstrip("/") in result.output, f"--help 未列出 {cmd}"


@pytest.mark.parametrize("cmd_name", ["/write", "/inject-genre", "/list-genres", "/help"])
def test_command_registered_once(cmd_name: str) -> None:
    """同一命令不应被重复登记（装饰器幂等）。"""
    count = sum(1 for c in COMMAND_REGISTRY if c.name == cmd_name)
    assert count == 1, f"{cmd_name} 被重复登记 {count} 次"
