"""P4 测试：结局模式派生态记源 + 自愈泛化（复盘根治 2026-09-07，纯离线）。

覆盖：
- 进入结局模式时记录 ending_mode_book_total（来源）；
- 总章数调大 → 当前章跌出结局区间 → 自动退出（清除全部派生标记）；
- 总章数调小 → 当前章仍在结局区间 → 保留模式（拍板 4），仅跟随记录新总章数；
- 无记源的旧数据（兼容）：维持原「一旦进入不退出」语义。
"""

from __future__ import annotations

import pytest
from pathlib import Path

from agent.core.engine.state_machine import StateMachine
from tests.test_g8_mainline import _FakeWriter, _make_g8_project, _make_pipeline, _read_progress, S01


def _project_with_plan(tmp_path: Path, total: int) -> Path:
    return _make_g8_project(
        tmp_path, n_sublines=2, target=total,
        plan_json={"total_chapters": total, "subline_share": {}},
    )


def _set_progress(d: Path, **fields) -> None:
    sm = StateMachine(d)
    sm.load()
    sm.progress = {
        "current_subline": S01,
        "last_written_at": "",
        **fields,
    }
    sm.save()


def _pipeline(d: Path, total: int):
    return _make_pipeline(
        d, _FakeWriter(d), ending_gate=True, ending_ratio=0.25, target=total
    )


def test_enter_records_book_total_source(tmp_path: Path) -> None:
    d = _project_with_plan(tmp_path, total=30)
    _set_progress(d, total_written=22)
    _pipeline(d, 30)._maybe_enter_ending_mode()
    p = _read_progress(d)
    assert p["ending_mode"] is True
    assert p["ending_mode_at"] == 23
    assert p["ending_mode_book_total"] == 30, "进入结局模式必须记录来源总章数"


def test_total_raised_exits_ending_mode(tmp_path: Path) -> None:
    # 进入时 30 章书（@23）；扩写到 40 章 → 触发点 31，ch24 跌出结局区间 → 退出
    d = _project_with_plan(tmp_path, total=40)
    _set_progress(
        d, total_written=23, ending_mode=True, ending_mode_at=23,
        ending_mode_book_total=30,
    )
    _pipeline(d, 40)._maybe_enter_ending_mode()
    p = _read_progress(d)
    assert p["ending_mode"] is False, "总章数调大后当前章不在结局区间，应自动退出"
    assert "ending_mode_at" not in p
    assert "ending_mode_book_total" not in p


def test_total_lowered_keeps_mode_and_follows_source(tmp_path: Path) -> None:
    # 进入时 30 章书（@23）；縮到 24 章 → 触发点 19，ch24 仍在结局区间 → 保留
    d = _project_with_plan(tmp_path, total=24)
    _set_progress(
        d, total_written=23, ending_mode=True, ending_mode_at=23,
        ending_mode_book_total=30,
    )
    _pipeline(d, 24)._maybe_enter_ending_mode()
    p = _read_progress(d)
    assert p["ending_mode"] is True, "仍在结局区间 → 拍板 4：不退出"
    assert p["ending_mode_book_total"] == 24, "来源总章数应跟随新值"


def test_legacy_entry_without_source_stays(tmp_path: Path) -> None:
    # 旧数据无 ending_mode_book_total（bt=0）且触发点未变 → 不退出（拍板 4 兼容）
    d = _project_with_plan(tmp_path, total=30)
    _set_progress(d, total_written=24, ending_mode=True, ending_mode_at=23)
    _pipeline(d, 30)._maybe_enter_ending_mode()
    p = _read_progress(d)
    assert p["ending_mode"] is True
    assert p["ending_mode_at"] == 23
