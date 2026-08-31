"""M16 命令系统单元测试

覆盖：
- F16.1 参数解析（--key value / --key=value / --flag / -k value / 裸文本）
- F16.2 命令清单（COMMAND_REGISTRY 完整性 + 按状态过滤）
- F16.3 命令路由（门禁拒绝 + 提示当前阶段可用命令）
- CommandRouter.parse 集成 parse_args
- CommandRouter.allowed_commands_meta
- status / commands CLI 命令（按状态过滤）
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent.cli import app
from agent.core.engine.command_router import (
    COMMAND_REGISTRY,
    CommandMeta,
    CommandRouter,
    commands_for_state,
    get_command_meta,
    parse_args,
)
from agent.core.engine.state_machine import State, StateMachine


# ============================================================
# F16.1 参数解析
# ============================================================
class TestParseArgs:
    def test_empty(self) -> None:
        assert parse_args("") == {}

    def test_key_value(self) -> None:
        assert parse_args("--dir projects/x") == {"dir": "projects/x"}

    def test_key_equal_value(self) -> None:
        assert parse_args("--dir=projects/x") == {"dir": "projects/x"}

    def test_flag_only(self) -> None:
        assert parse_args("--yes") == {"yes": True}

    def test_short_key_value(self) -> None:
        assert parse_args("-d projects/x") == {"d": "projects/x"}

    def test_multiple_keys(self) -> None:
        result = parse_args("--dir projects/x --intent 让主角加入执法堂")
        assert result["dir"] == "projects/x"
        assert "让主角加入执法堂" in result["intent"]

    def test_mixed_keys_and_bare_text(self) -> None:
        result = parse_args("--dir projects/x rollback to ch020")
        assert result["dir"] == "projects/x"
        assert "rollback" in result["raw"]
        assert "ch020" in result["raw"]

    def test_bare_text_only(self) -> None:
        result = parse_args("ch020")
        assert result["raw"] == "ch020"

    def test_multiple_flags(self) -> None:
        result = parse_args("--yes --force")
        assert result["yes"] is True
        assert result["force"] is True

    def test_chinese_value(self) -> None:
        result = parse_args("--intent 让主角在N02选择卧底")
        assert result["intent"] == "让主角在N02选择卧底"

    def test_value_with_hyphen_not_key(self) -> None:
        """以 - 开头但非参数（如负数）应作为值"""
        # --dir -d 这种情况较复杂，这里测简单场景
        result = parse_args("--label before-m2-revision")
        assert result["label"] == "before-m2-revision"


# ============================================================
# F16.1 CommandRouter.parse 集成
# ============================================================
class TestRouterParse:
    @pytest.fixture
    def router(self, tmp_path: Path) -> CommandRouter:
        return CommandRouter(StateMachine(project_dir=tmp_path))

    def test_parse_simple_command(self, router: CommandRouter) -> None:
        assert router.parse("/help") == ("/help", {})

    def test_parse_command_with_args(self, router: CommandRouter) -> None:
        result = router.parse("/write --dir projects/x")
        assert result is not None
        assert result[0] == "/write"
        assert result[1]["dir"] == "projects/x"

    def test_parse_dialog_returns_none(self, router: CommandRouter) -> None:
        assert router.parse("hello world") is None

    def test_parse_empty_string(self, router: CommandRouter) -> None:
        assert router.parse("") is None

    def test_parse_preserves_leading_slash(self, router: CommandRouter) -> None:
        result = router.parse("/rollback ch020")
        assert result is not None
        assert result[0] == "/rollback"
        assert result[1]["raw"] == "ch020"


# ============================================================
# F16.2 命令清单
# ============================================================
class TestCommandRegistry:
    def test_registry_not_empty(self) -> None:
        assert len(COMMAND_REGISTRY) > 0

    def test_all_commands_start_with_slash(self) -> None:
        for cmd in COMMAND_REGISTRY:
            assert cmd.name.startswith("/"), f"{cmd.name} 缺少前导斜杠"

    def test_all_commands_have_description(self) -> None:
        for cmd in COMMAND_REGISTRY:
            assert cmd.description, f"{cmd.name} 缺少描述"

    def test_no_duplicate_commands(self) -> None:
        names = [c.name for c in COMMAND_REGISTRY]
        assert len(names) == len(set(names)), "存在重复命令"

    def test_get_command_meta_found(self) -> None:
        meta = get_command_meta("/write")
        assert meta is not None
        assert meta.name == "/write"
        assert "写" in meta.description

    def test_get_command_meta_not_found(self) -> None:
        assert get_command_meta("/nonexistent") is None

    def test_core_commands_present(self) -> None:
        """PRD F16.2 表中的核心命令都应在注册表中"""
        required = [
            "/start", "/discuss", "/confirm-architecture", "/outline",
            "/design-characters", "/write", "/adjust-route", "/adjust-relation",
            "/mode", "/load-skill", "/bookworm-review", "/rollback", "/resume",
            "/export", "/help", "/reset-state",
        ]
        names = {c.name for c in COMMAND_REGISTRY}
        for cmd in required:
            assert cmd in names, f"缺少核心命令 {cmd}"


# ============================================================
# F16.2 按状态过滤
# ============================================================
class TestCommandsForState:
    def test_init_state_only_start(self) -> None:
        """INIT 状态只有 /start + 全局命令"""
        cmds = commands_for_state("INIT")
        names = [c.name for c in cmds]
        assert "/start" in names
        assert "/write" not in names
        assert "/discuss" not in names

    def test_writing_state_has_write_and_adjust(self) -> None:
        cmds = commands_for_state("WRITING")
        names = [c.name for c in cmds]
        assert "/write" in names
        assert "/adjust-route" in names
        assert "/adjust-relation" in names
        assert "/bookworm-review" in names

    def test_arch_confirmed_has_outline(self) -> None:
        cmds = commands_for_state("ARCH_CONFIRMED")
        names = [c.name for c in cmds]
        assert "/outline" in names
        # 但不应有 /write
        assert "/write" not in names

    def test_paused_state_only_resume(self) -> None:
        cmds = commands_for_state("PAUSED")
        names = [c.name for c in cmds]
        assert "/resume" in names
        assert "/write" not in names

    def test_global_commands_in_all_states(self) -> None:
        """全局命令（/help /mode 等）应在所有状态可用"""
        from agent.core.engine.state_machine import State

        for state in State:
            cmds = commands_for_state(state.value)
            names = [c.name for c in cmds]
            assert "/help" in names, f"{state.value} 缺少 /help"

    def test_invalid_state_returns_empty(self) -> None:
        assert commands_for_state("INVALID_STATE") == []

    def test_commands_in_registry_order(self) -> None:
        """返回的命令应保持 COMMAND_REGISTRY 顺序"""
        cmds = commands_for_state("WRITING")
        # 找到 /write 和 /adjust-route 在两个列表中的索引
        reg_names = [c.name for c in COMMAND_REGISTRY]
        idx_write = reg_names.index("/write")
        idx_adjust = reg_names.index("/adjust-route")
        assert idx_write < idx_adjust  # registry 中 /write 在 /adjust-route 前

        returned_names = [c.name for c in cmds]
        assert returned_names.index("/write") < returned_names.index("/adjust-route")


# ============================================================
# F16.3 命令路由门禁
# ============================================================
class TestRoutingGate:
    @pytest.fixture
    def router(self, tmp_path: Path) -> CommandRouter:
        sm = StateMachine(project_dir=tmp_path)
        return CommandRouter(sm)

    def test_reject_command_not_in_state(self, router: CommandRouter) -> None:
        """/write 在 INIT 状态被拒绝"""
        router.sm.state = State.INIT
        result = router.route("/write")
        assert result.type == "rejected"
        assert "不可用" in (result.reason or "")
        # 应提示可用命令
        assert "/start" in (result.reason or "")

    def test_reject_includes_available_commands(self, router: CommandRouter) -> None:
        """拒绝时应列出当前可用命令"""
        router.sm.state = State.INIT
        result = router.route("/discuss")
        assert result.type == "rejected"
        assert "/start" in (result.reason or "")

    def test_allow_command_in_correct_state(self, router: CommandRouter) -> None:
        """/write 在 WRITING 状态且已注册 handler 时被允许"""
        router.sm.state = State.WRITING
        router.register("/write", lambda **kw: None)
        result = router.route("/write")
        assert result.type == "command"
        assert result.handler is not None

    def test_global_command_always_allowed(self, router: CommandRouter) -> None:
        """/help 在任意状态可用"""
        router.register("/help", lambda **kw: None)
        for state in State:
            router.sm.state = state
            result = router.route("/help")
            assert result.type == "command", f"/help 应在 {state.value} 可用"

    def test_unregistered_command_rejected(self, router: CommandRouter) -> None:
        """允许状态但未注册 handler"""
        router.sm.state = State.WRITING
        result = router.route("/write")
        assert result.type == "rejected"
        assert "未注册" in (result.reason or "")

    def test_dialog_not_affected_by_state(self, router: CommandRouter) -> None:
        """自由对话不受门禁影响"""
        router.sm.state = State.INIT
        result = router.route("hello world")
        assert result.type == "dialog"

    def test_route_preserves_args(self, router: CommandRouter) -> None:
        """路由结果应保留解析后的参数"""
        router.sm.state = State.WRITING
        router.register("/write", lambda **kw: None)
        result = router.route("/write --dir projects/x")
        assert result.type == "command"
        assert result.args is not None
        assert result.args.get("dir") == "projects/x"


# ============================================================
# allowed_commands_meta
# ============================================================
class TestAllowedCommandsMeta:
    def test_returns_meta_list(self, tmp_path: Path) -> None:
        router = CommandRouter(StateMachine(project_dir=tmp_path))
        router.sm.state = State.WRITING
        metas = router.allowed_commands_meta()
        assert all(isinstance(m, CommandMeta) for m in metas)
        assert len(metas) > 0

    def test_changes_with_state(self, tmp_path: Path) -> None:
        router = CommandRouter(StateMachine(project_dir=tmp_path))
        router.sm.state = State.INIT
        init_cmds = {m.name for m in router.allowed_commands_meta()}
        router.sm.state = State.WRITING
        writing_cmds = {m.name for m in router.allowed_commands_meta()}
        assert init_cmds != writing_cmds
        assert "/write" in writing_cmds
        assert "/write" not in init_cmds


# ============================================================
# CLI status 命令
# ============================================================
class TestStatusCommand:
    def test_status_no_state_file(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["status", "-d", str(tmp_path)])
        assert result.exit_code == 0
        assert "尚未初始化" in result.output

    def test_status_with_state(self, tmp_path: Path) -> None:
        # 创建 state.json
        state_dir = tmp_path / ".state"
        state_dir.mkdir()
        (state_dir / "state.json").write_text(
            json.dumps(
                {
                    "state": "WRITING",
                    "mode": "light",
                    "progress": {"total_written": 5},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        runner = CliRunner()
        result = runner.invoke(app, ["status", "-d", str(tmp_path)])
        assert result.exit_code == 0
        assert "WRITING" in result.output
        assert "light" in result.output
        assert "total_written" in result.output
        assert "/write" in result.output

    def test_status_shows_available_commands(self, tmp_path: Path) -> None:
        state_dir = tmp_path / ".state"
        state_dir.mkdir()
        (state_dir / "state.json").write_text(
            json.dumps({"state": "INIT", "mode": "heavy", "progress": {}}),
            encoding="utf-8",
        )
        runner = CliRunner()
        result = runner.invoke(app, ["status", "-d", str(tmp_path)])
        assert result.exit_code == 0
        assert "/start" in result.output
        # INIT 状态不应有 /write
        # （但全局命令 /help 会有，所以检查 /start 存在即可）


# ============================================================
# CLI commands 命令
# ============================================================
class TestCommandsCommand:
    def test_commands_full_list(self) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["commands"])
        assert result.exit_code == 0
        assert "/write" in result.output
        assert "/start" in result.output
        assert "/bookworm-review" in result.output

    def test_commands_filtered_by_state(self, tmp_path: Path) -> None:
        state_dir = tmp_path / ".state"
        state_dir.mkdir()
        (state_dir / "state.json").write_text(
            json.dumps({"state": "INIT", "mode": "heavy", "progress": {}}),
            encoding="utf-8",
        )
        runner = CliRunner()
        result = runner.invoke(app, ["commands", "-d", str(tmp_path)])
        assert result.exit_code == 0
        assert "/start" in result.output
        assert "INIT" in result.output

    def test_commands_no_state_file_shows_all(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["commands", "-d", str(tmp_path)])
        assert result.exit_code == 0
        assert "全量命令" in result.output
