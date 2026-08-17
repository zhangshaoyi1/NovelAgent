"""设定集管理器单元测试"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.core.exceptions import FrozenFieldError
from agent.core.setting_manager import SettingManager


@pytest.fixture
def sm(tmp_path: Path) -> SettingManager:
    return SettingManager(project_dir=tmp_path)


# ------ world.md ------
def test_load_world_when_not_exists(sm: SettingManager) -> None:
    result = sm.load_world()
    assert result["exists"] is False
    assert result["metadata"] == {}
    assert result["content"] == ""


def test_save_and_load_world(sm: SettingManager) -> None:
    metadata = {"title": "测试小说", "genre": "xiuxian", "frozen_fields": ["realm_system"]}
    content = "# 总设定集\n\n世界观内容。"
    sm.save_world(metadata, content)

    result = sm.load_world()
    assert result["exists"] is True
    assert result["metadata"]["title"] == "测试小说"
    assert result["metadata"]["genre"] == "xiuxian"
    assert "总设定集" in result["content"]


def test_save_world_creates_parent_dir(tmp_path: Path) -> None:
    """项目目录不存在时应自动创建"""
    sm = SettingManager(project_dir=tmp_path / "nested" / "project")
    sm.save_world({"title": "x"}, "内容")
    assert (tmp_path / "nested" / "project" / "world.md").exists()


# ------ 冻结字段 ------
def test_frozen_field_blocks_change(sm: SettingManager) -> None:
    """冻结字段改动应抛 FrozenFieldError"""
    sm.save_world(
        {"title": "x", "frozen_fields": ["realm_system"], "realm_system": "炼气→筑基"},
        "内容",
    )
    with pytest.raises(FrozenFieldError, match="realm_system"):
        sm.save_world(
            {"title": "x", "frozen_fields": ["realm_system"], "realm_system": "改了"},
            "内容",
        )


def test_unfreeze_allows_change(sm: SettingManager) -> None:
    """解冻后可以修改"""
    sm.save_world(
        {"title": "x", "frozen_fields": ["realm_system"], "realm_system": "v1"},
        "内容",
    )
    sm.unfreeze("realm_system")
    sm.save_world(
        {"title": "x", "frozen_fields": ["realm_system"], "realm_system": "v2"},
        "内容",
    )
    assert sm.load_world()["metadata"]["realm_system"] == "v2"


def test_is_frozen(sm: SettingManager) -> None:
    sm.save_world(
        {"title": "x", "frozen_fields": ["realm_system"], "realm_system": "v1"},
        "内容",
    )
    assert sm.is_frozen("realm_system") is True
    assert sm.is_frozen("title") is False  # 不在 frozen_fields 列表
    sm.unfreeze("realm_system")
    assert sm.is_frozen("realm_system") is False


def test_freeze_after_unfreeze(sm: SettingManager) -> None:
    """重新冻结后又不允许改"""
    sm.save_world(
        {"title": "x", "frozen_fields": ["realm_system"], "realm_system": "v1"},
        "内容",
    )
    sm.unfreeze("realm_system")
    sm.freeze("realm_system")
    with pytest.raises(FrozenFieldError):
        sm.save_world(
            {"title": "x", "frozen_fields": ["realm_system"], "realm_system": "v2"},
            "内容",
        )


def test_update_frozen_field_requires_unfreeze(sm: SettingManager) -> None:
    sm.save_world(
        {"title": "x", "frozen_fields": ["realm_system"], "realm_system": "v1"},
        "内容",
    )
    with pytest.raises(FrozenFieldError):
        sm.update_frozen_field("realm_system", "v2")

    sm.unfreeze("realm_system")
    sm.update_frozen_field("realm_system", "v2")
    assert sm.load_world()["metadata"]["realm_system"] == "v2"


# ------ 支线设定集 ------
def test_save_and_load_subline(sm: SettingManager) -> None:
    sm.save_subline("S01_悟道", {"subline_name": "悟道之旅"}, "支线内容")
    result = sm.load_subline("S01_悟道")
    assert result["exists"] is True
    assert result["metadata"]["subline_name"] == "悟道之旅"
    assert "支线内容" in result["content"]


def test_load_subline_not_exists(sm: SettingManager) -> None:
    result = sm.load_subline("S99_不存在")
    assert result["exists"] is False


def test_list_sublines(sm: SettingManager) -> None:
    sm.save_subline("S01_a", {}, "")
    sm.save_subline("S02_b", {}, "")
    sublines = sm.list_sublines()
    assert sublines == ["S01_a", "S02_b"]


# ------ 角色档案 ------
def test_save_and_load_character(sm: SettingManager) -> None:
    sm.save_character("李逍遥", {"role": "protagonist"}, "主角档案")
    result = sm.load_character("李逍遥")
    assert result["exists"] is True
    assert result["metadata"]["role"] == "protagonist"


def test_character_name_sanitized(sm: SettingManager) -> None:
    """特殊字符应被替换为下划线"""
    sm.save_character("a/b\\c", {}, "x")
    # 文件名应为 a_b_c.md
    chars = sm.list_characters()
    assert len(chars) == 1
    assert "/" not in chars[0]


def test_list_characters(sm: SettingManager) -> None:
    sm.save_character("甲", {}, "")
    sm.save_character("乙", {}, "")
    chars = sm.list_characters()
    assert len(chars) == 2


# ------ 快照 ------
def test_create_snapshot(sm: SettingManager) -> None:
    sm.save_world({"title": "x"}, "world 内容")
    sm.save_character("甲", {}, "角色")
    sm.save_subline("S01_a", {}, "支线")

    snap = sm.create_snapshot("v1")
    assert snap.exists()
    assert (snap / "world.md").exists()
    assert (snap / "characters").exists()
    assert (snap / "sublines").exists()


def test_list_snapshots(sm: SettingManager) -> None:
    sm.save_world({"title": "x"}, "")
    snap1 = sm.create_snapshot("v1")
    snap2 = sm.create_snapshot("v2")
    snaps = sm.list_snapshots()
    assert len(snaps) == 2
    # 倒序：最新的在前
    assert snaps[0] == snap2


def test_rollback_to_snapshot(sm: SettingManager) -> None:
    """回滚后内容应恢复"""
    sm.save_world({"title": "v1"}, "版本1")
    snap = sm.create_snapshot("v1")

    # 修改
    sm.save_world({"title": "v2"}, "版本2")
    assert sm.load_world()["metadata"]["title"] == "v2"

    # 回滚
    sm.rollback_to_snapshot(snap)
    assert sm.load_world()["metadata"]["title"] == "v1"
    assert "版本1" in sm.load_world()["content"]


def test_rollback_nonexistent_raises(sm: SettingManager) -> None:
    with pytest.raises(FileNotFoundError):
        sm.rollback_to_snapshot(sm.snapshots_dir / "not_exist")
