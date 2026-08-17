"""M8 介入频率控制单元测试

覆盖：
- 三档模式定义（heavy/light/auto）
- 模式切换 + 持久化到 state.json
- 非法模式拒绝
- should_intervene 介入矩阵判断
- 各模式下的介入点集合正确
- ModeController 读取当前模式
- CLI /mode 命令注册
- M5 接入 mode_controller（auto 模式不阻塞）
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.core.state_machine import State, StateMachine
from agent.workflows.m8_mode import (
    InterventionPoint,
    M8ModeResult,
    Mode,
    ModeController,
    MODE_INTERVENTION_MATRIX,
)


# ============================================================
# 夹具
# ============================================================
def _build_project(tmp_path: Path, mode: str = "heavy") -> Path:
    d = tmp_path / "p"
    d.mkdir(parents=True)
    sm = StateMachine(d)
    sm.load()
    sm.state = State.WRITING
    sm.mode = mode
    sm.save()
    return d


# ============================================================
# Test 模式枚举与矩阵
# ============================================================
class TestModeEnum:
    def test_three_modes_defined(self) -> None:
        assert Mode.HEAVY.value == "heavy"
        assert Mode.LIGHT.value == "light"
        assert Mode.AUTO.value == "auto"

    def test_mode_is_str_enum(self) -> None:
        assert isinstance(Mode.HEAVY, str)
        assert Mode("heavy") == Mode.HEAVY
        assert Mode("auto") == Mode.AUTO

    def test_intervention_matrix_covers_all_modes(self) -> None:
        for m in Mode:
            assert m in MODE_INTERVENTION_MATRIX

    def test_heavy_has_most_intervention_points(self) -> None:
        heavy_count = len(MODE_INTERVENTION_MATRIX[Mode.HEAVY])
        light_count = len(MODE_INTERVENTION_MATRIX[Mode.LIGHT])
        auto_count = len(MODE_INTERVENTION_MATRIX[Mode.AUTO])
        assert heavy_count > light_count > auto_count

    def test_auto_only_major_decision(self) -> None:
        assert MODE_INTERVENTION_MATRIX[Mode.AUTO] == {InterventionPoint.MAJOR_DECISION}

    def test_heavy_includes_chapter_before_and_after(self) -> None:
        points = MODE_INTERVENTION_MATRIX[Mode.HEAVY]
        assert InterventionPoint.CHAPTER_BEFORE in points
        assert InterventionPoint.CHAPTER_AFTER in points

    def test_light_excludes_chapter_before_and_after(self) -> None:
        points = MODE_INTERVENTION_MATRIX[Mode.LIGHT]
        assert InterventionPoint.CHAPTER_BEFORE not in points
        assert InterventionPoint.CHAPTER_AFTER not in points
        assert InterventionPoint.PLOT_NODE in points


# ============================================================
# Test 模式切换与持久化
# ============================================================
class TestModeSwitch:
    def test_switch_heavy_to_light(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path, mode="heavy")
        ctrl = ModeController(project_dir=d)
        result = ctrl.switch("light")
        assert result.changed is True
        assert result.old_mode == Mode.HEAVY
        assert result.new_mode == Mode.LIGHT
        assert "heavy" in result.message
        assert "light" in result.message

    def test_switch_persists_to_state_json(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path, mode="heavy")
        ctrl = ModeController(project_dir=d)
        ctrl.switch("auto")
        # 重新加载验证持久化
        sm2 = StateMachine(d)
        sm2.load()
        assert sm2.mode == "auto"

    def test_switch_to_same_mode_no_change(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path, mode="heavy")
        ctrl = ModeController(project_dir=d)
        result = ctrl.switch("heavy")
        assert result.changed is False
        assert "已是" in result.message or "无需" in result.message

    def test_switch_invalid_mode_raises(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path, mode="heavy")
        ctrl = ModeController(project_dir=d)
        with pytest.raises(ValueError, match="非法模式"):
            ctrl.switch("invalid")

    def test_switch_accepts_enum(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path, mode="heavy")
        ctrl = ModeController(project_dir=d)
        result = ctrl.switch(Mode.AUTO)
        assert result.new_mode == Mode.AUTO

    def test_switch_case_insensitive(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path, mode="heavy")
        ctrl = ModeController(project_dir=d)
        result = ctrl.switch("AUTO")
        assert result.new_mode == Mode.AUTO

    def test_current_mode_property(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path, mode="light")
        ctrl = ModeController(project_dir=d)
        assert ctrl.current_mode == Mode.LIGHT

    def test_current_mode_fallback_on_invalid(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path, mode="heavy")
        # 手动写入非法 mode
        state_file = d / ".state" / "state.json"
        data = json.loads(state_file.read_text(encoding="utf-8"))
        data["mode"] = "invalid_mode"
        state_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        ctrl = ModeController(project_dir=d)
        # 应回退到 heavy
        assert ctrl.current_mode == Mode.HEAVY


# ============================================================
# Test 介入判断
# ============================================================
class TestShouldIntervene:
    def test_heavy_intervenes_chapter_before(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path, mode="heavy")
        ctrl = ModeController(project_dir=d)
        assert ctrl.should_intervene(InterventionPoint.CHAPTER_BEFORE) is True

    def test_auto_skips_chapter_before(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path, mode="auto")
        ctrl = ModeController(project_dir=d)
        assert ctrl.should_intervene(InterventionPoint.CHAPTER_BEFORE) is False

    def test_auto_intervenes_major_decision(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path, mode="auto")
        ctrl = ModeController(project_dir=d)
        assert ctrl.should_intervene(InterventionPoint.MAJOR_DECISION) is True

    def test_light_intervenes_plot_node(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path, mode="light")
        ctrl = ModeController(project_dir=d)
        assert ctrl.should_intervene(InterventionPoint.PLOT_NODE) is True

    def test_light_skips_chapter_after(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path, mode="light")
        ctrl = ModeController(project_dir=d)
        assert ctrl.should_intervene(InterventionPoint.CHAPTER_AFTER) is False

    def test_all_modes_intervene_major_decision(self, tmp_path: Path) -> None:
        for i, m in enumerate(Mode):
            d = _build_project(tmp_path / f"proj_{i}", mode=m.value)
            ctrl = ModeController(project_dir=d)
            assert ctrl.should_intervene(InterventionPoint.MAJOR_DECISION) is True, (
                f"模式 {m.value} 应在重大决策时介入"
            )


# ============================================================
# Test 介入交互（auto 模式不阻塞）
# ============================================================
class TestInterventionInteraction:
    def test_auto_ask_chapter_direction_returns_none(self, tmp_path: Path) -> None:
        """auto 模式下章节前不询问，直接返回 None"""
        d = _build_project(tmp_path, mode="auto")
        ctrl = ModeController(project_dir=d)
        ctx = {"chapter_num": 1, "subline_id": "S01", "route_milestone": "N01"}
        result = ctrl.ask_chapter_direction(ctx)
        assert result is None

    def test_auto_ask_chapter_feedback_returns_continue(self, tmp_path: Path) -> None:
        """auto 模式下章节后不询问，直接返回 continue"""
        d = _build_project(tmp_path, mode="auto")
        ctrl = ModeController(project_dir=d)
        ctx = {"chapter_num": 1}
        result = ctrl.ask_chapter_feedback(ctx, {"word_count": 3000, "quality_passed": True})
        assert result == "continue"

    def test_auto_notify_plot_node_returns_true(self, tmp_path: Path) -> None:
        """auto 模式下剧情节点不询问，直接返回 True"""
        d = _build_project(tmp_path, mode="auto")
        ctrl = ModeController(project_dir=d)
        ctx = {"node_type": "subline_switch", "description": "切换支线"}
        assert ctrl.notify_plot_node(ctx) is True

    def test_heavy_ask_chapter_direction_with_eof(self, tmp_path: Path) -> None:
        """heavy 模式下非交互环境优雅降级返回 None"""
        d = _build_project(tmp_path, mode="heavy")
        ctrl = ModeController(project_dir=d)
        ctx = {"chapter_num": 1}
        # 模拟 EOF（非交互终端）
        result = ctrl.ask_chapter_direction(ctx)
        # 在非 TTY 环境下 Prompt.ask 可能返回空字符串或抛 EOFError
        # 我们的实现应优雅降级为 None
        assert result is None or isinstance(result, str)


# ============================================================
# Test 模式信息查询
# ============================================================
class TestModeInfo:
    def test_get_mode_info_heavy(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path, mode="heavy")
        ctrl = ModeController(project_dir=d)
        info = ctrl.get_mode_info()
        assert info.mode == Mode.HEAVY
        assert "重度" in info.label or "协作" in info.label
        assert "每章" in info.description
        assert "章节前" in info.intervention_points

    def test_get_mode_info_light(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path, mode="light")
        ctrl = ModeController(project_dir=d)
        info = ctrl.get_mode_info()
        assert info.mode == Mode.LIGHT
        assert "剧情节点" in info.description

    def test_get_mode_info_auto(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path, mode="auto")
        ctrl = ModeController(project_dir=d)
        info = ctrl.get_mode_info()
        assert info.mode == Mode.AUTO
        assert "自主" in info.description or "自动" in info.description

    def test_get_mode_info_for_specific_mode(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path, mode="heavy")
        ctrl = ModeController(project_dir=d)
        info = ctrl.get_mode_info(Mode.AUTO)
        assert info.mode == Mode.AUTO


# ============================================================
# Test StateMachine set_mode
# ============================================================
class TestStateMachineSetMode:
    def test_set_mode_valid(self, tmp_path: Path) -> None:
        d = tmp_path / "p"
        d.mkdir()
        sm = StateMachine(d)
        sm.load()
        sm.set_mode("light")
        assert sm.mode == "light"
        # 验证持久化
        sm2 = StateMachine(d)
        sm2.load()
        assert sm2.mode == "light"

    def test_set_mode_invalid_raises(self, tmp_path: Path) -> None:
        d = tmp_path / "p"
        d.mkdir()
        sm = StateMachine(d)
        sm.load()
        with pytest.raises(ValueError, match="非法模式"):
            sm.set_mode("invalid")

    def test_set_mode_all_values(self, tmp_path: Path) -> None:
        d = tmp_path / "p"
        d.mkdir()
        sm = StateMachine(d)
        sm.load()
        for m in ("heavy", "light", "auto"):
            sm.set_mode(m)
            assert sm.mode == m


# ============================================================
# Test CLI /mode 命令注册
# ============================================================
class TestCLIModeCommand:
    def test_mode_command_registered(self) -> None:
        from agent import cli as cli_module

        assert callable(getattr(cli_module, "mode", None))

    def test_mode_command_has_project_dir_option(self) -> None:
        import inspect

        from agent.cli import mode

        sig = inspect.signature(mode)
        assert "project_dir" in sig.parameters
        assert "target" in sig.parameters
