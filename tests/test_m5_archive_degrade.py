"""F-1 降级可见化补口（2026-09-12）：``_archive_chapter`` 失败必须接 ``degrade()``。

旧实现整体 ``except Exception: logger.debug(...)``——连续性账本/伏笔标记整链
失败只有 debug 级日志，绕过了降级可见化棘轮（对比同文件 ``_sync_setting_canon``
的正确写法）。本测试锁定：归档链内任何异常 → degrade 被调用一次（标签
``m5_persist.archive_chapter``），且不向写章主流程抛异常。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from agent.workflows.writing.m5_persist import M5PersistMixin


def _mixin(tmp_path: Path) -> M5PersistMixin:
    m = M5PersistMixin.__new__(M5PersistMixin)
    m.project_dir = tmp_path
    m.chapters_dir = tmp_path / "chapters"
    return m


def test_archive_failure_calls_degrade_and_does_not_raise(tmp_path: Path) -> None:
    m = _mixin(tmp_path)
    captured: list[tuple[str, str]] = []

    def _fake_degrade(point: str, message: str, exc: Exception) -> None:
        captured.append((point, message))

    with (
        patch("agent.workflows.writing.m5_persist.degrade", side_effect=_fake_degrade),
        patch(
            "agent.workflows.writing.m5_persist._extract_chapter_facts",
            side_effect=RuntimeError("抽取器炸了"),
        ),
    ):
        m._archive_chapter({"chapter_num": 1}, "测试章", "正文……")

    assert captured, "归档失败未接 degrade()，仍是不可观测的静默降级"
    point, message = captured[0]
    assert point == "m5_persist.archive_chapter"
    assert "不影响本章产出" in message


def test_archive_success_does_not_call_degrade(tmp_path: Path) -> None:
    """回归保护：正常归档不得误报降级。"""
    m = _mixin(tmp_path)

    with (
        patch("agent.workflows.writing.m5_persist.degrade") as fake_degrade,
        patch(
            "agent.workflows.writing.m5_persist._extract_chapter_facts",
            return_value=([], [], []),
        ),
        patch("agent.workflows.writing.m5_persist.M5PersistMixin._sync_setting_canon"),
    ):
        # ledger/foresight 走真实 tmp_path 项目（空账本，commit 后 save 正常）
        m._archive_chapter({"chapter_num": 1}, "测试章", "正文……")

    fake_degrade.assert_not_called()
