"""P1 PlanStore 测试（复盘根治 2026-09-07，纯离线）。

覆盖：
- 唯一写入口 mutate：创建 / 变更 / 无变更不落盘；
- 推导关系强制：total_chapters 自动钳制为 scope.estimated_chapters；
- override 语义：允许偏离但必须携带 reason，日志留痕；
- 变更日志 plan_history.json（append-only、上限 50）；
- total 变化触发派生重算（mainline horizon/share 对齐）；
- 读侧防线 load_validated（直改破坏检测）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.core.plan_store import HISTORY_LIMIT, PlanInvariantError, PlanStore


def _write_plan(d: Path, plan: dict) -> None:
    (d / ".state").mkdir(parents=True, exist_ok=True)
    (d / ".state" / "plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _read_plan(d: Path) -> dict:
    return json.loads((d / ".state" / "plan.json").read_text(encoding="utf-8"))


def _read_history(d: Path) -> list[dict]:
    f = d / ".state" / "plan_history.json"
    if not f.exists():
        return []
    return json.loads(f.read_text(encoding="utf-8"))


def test_mutate_creates_plan_and_history(tmp_path: Path) -> None:
    ps = PlanStore(tmp_path)
    out = ps.mutate(
        lambda old: {
            **old,
            "version": 1,
            "total_chapters": 100,
            "scope": {"estimated_chapters": 100},
        },
        reason="创建测试",
    )
    assert out["total_chapters"] == 100
    assert _read_plan(tmp_path)["total_chapters"] == 100
    hist = _read_history(tmp_path)
    assert len(hist) == 1
    assert hist[0]["reason"] == "创建测试"
    assert "total_chapters" in hist[0]["keys_changed"]
    assert hist[0]["override"] is False


def test_mutate_total_clamped_to_scope(tmp_path: Path) -> None:
    # 直改破坏场景：total=18 vs scope.estimated=1200 → 写侧强制推导校正
    _write_plan(tmp_path, {"total_chapters": 18, "scope": {"estimated_chapters": 1200}})
    ps = PlanStore(tmp_path)
    out = ps.mutate(lambda p: {**p, "route": {"nodes": []}}, reason="M6 同步")
    assert out["total_chapters"] == 1200, "total 必须钳制为 scope.estimated_chapters"
    assert _read_plan(tmp_path)["total_chapters"] == 1200


def test_mutate_override_keeps_mismatch_with_reason(tmp_path: Path) -> None:
    _write_plan(tmp_path, {"total_chapters": 18, "scope": {"estimated_chapters": 1200}})
    ps = PlanStore(tmp_path)
    out = ps.mutate(
        lambda p: {**p, "total_chapters": 999},
        reason="人工拍板修复",
        override=True,
    )
    assert out["total_chapters"] == 999
    hist = _read_history(tmp_path)
    assert hist[-1]["override"] is True
    assert hist[-1]["total_chapters"] == {"old": 18, "new": 999}


def test_override_without_reason_rejected(tmp_path: Path) -> None:
    ps = PlanStore(tmp_path)
    with pytest.raises(PlanInvariantError):
        ps.mutate(lambda p: {**p, "total_chapters": 5}, override=True)


def test_invalid_total_rejected(tmp_path: Path) -> None:
    ps = PlanStore(tmp_path)
    with pytest.raises(PlanInvariantError):
        ps.mutate(lambda p: {**p, "total_chapters": "abc"}, reason="x")


def test_nochange_skips_write_and_history(tmp_path: Path) -> None:
    _write_plan(tmp_path, {"total_chapters": 10})
    ps = PlanStore(tmp_path)
    out = ps.mutate(lambda p: None, reason="无变更")
    assert out == {"total_chapters": 10}
    assert _read_history(tmp_path) == []
    assert _read_plan(tmp_path) == {"total_chapters": 10}


def test_total_change_triggers_mainline_realign(tmp_path: Path) -> None:
    _write_plan(
        tmp_path, {"total_chapters": 12, "scope": {"estimated_chapters": 12}}
    )
    mainline_file = tmp_path / ".state" / "mainline.json"
    mainline_file.write_text(
        json.dumps(
            {
                "version": 1,
                "horizon_chapters": 12,
                "subline_share": {"S01": 8, "S02": 4},
            }
        ),
        encoding="utf-8",
    )
    ps = PlanStore(tmp_path)
    ps.mutate(
        lambda p: {**p, "total_chapters": 18, "scope": {**p["scope"], "estimated_chapters": 18}},
        reason="扩写",
    )
    aligned = json.loads(mainline_file.read_text(encoding="utf-8"))
    assert aligned["horizon_chapters"] == 18, "total 变化必须触发 mainline 派生重算"
    assert sum(aligned["subline_share"].values()) == 18


def test_load_validated_detects_direct_edits(tmp_path: Path) -> None:
    _write_plan(tmp_path, {"total_chapters": 18, "scope": {"estimated_chapters": 1200}})
    plan, warnings = PlanStore(tmp_path).load_validated()
    assert plan is not None and plan["total_chapters"] == 18
    assert len(warnings) == 1
    assert "不一致" in warnings[0]

    _write_plan(tmp_path, {"total_chapters": 1200, "scope": {"estimated_chapters": 1200}})
    _, warnings = PlanStore(tmp_path).load_validated()
    assert warnings == []


def test_history_capped_at_limit(tmp_path: Path) -> None:
    _write_plan(tmp_path, {"total_chapters": 1})
    ps = PlanStore(tmp_path)
    for i in range(HISTORY_LIMIT + 10):
        ps.mutate(lambda p: {**p, "version": i}, reason=f"r{i}")
    assert len(_read_history(tmp_path)) == HISTORY_LIMIT


def test_scope_missing_skips_clamp(tmp_path: Path) -> None:
    # 无 scope（如 M3 阶段 MasterPlan）→ 不钳制，total 保留
    ps = PlanStore(tmp_path)
    out = ps.mutate(lambda old: {**old, "total_chapters": 350}, reason="M3")
    assert out["total_chapters"] == 350
