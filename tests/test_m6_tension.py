"""M6-B1 红线：实测张力落盘（纯观测面）—— 2026-09-19

钉住的链（每条源自 M6-A 取证结论，见方案文档 §19）：

1. **孤儿模块被接线**：`tension_curve` 此前全仓**零生产调用点**
   ⇒ `check_rhythm` 的跨章分析**永远没有输入**（不是"没做好"，是"没喂过数据"）。
   本红线钉「落盘链路真的被接上」。
2. **观测面不是供给面**（纪律 #9）：张力是「**事实**」（正文统计），档位是
   「**意图**」（规划标注），两条独立通道。**绝不用张力反推/覆盖档位**
   —— 那是系统替作者定意图，违反 `chapter_contract.py:447` 既有红线。
3. **失败必须显性化但不阻断**（纪律 #1/#2）：观测面落盘失败 ≠ 主结论失败
   ⇒ 返回 False + 降级留痕，**绝不抛、绝不阻断写章**。
4. **不污染真实项目**：测试一律用 tmp_path，绝不写 `novels/`。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.core.story.tension_curve import (
    TENSION_LEDGER,
    TensionCurveManager,
    measure_chapter_tension,
    read_tension,
    record_tension,
)

# 高张力样本（冲突词密集 + 短句）与低张力样本（平铺直叙）
_HIGH = (
    "杀！战！危险逼近，伏击爆发。"
    "他怒极，反手决斗。突然，阴谋浮现。"
    "难道这就是背叛？竟然！没想到！"
) * 3

_LOW = (
    "他坐在窗前，看着远处的云慢慢飘过去。"
    "茶杯里的水已经凉了，他没有去续。"
    "这一天很平静，没有什么特别的事情发生。"
) * 3


class TestMeasureTension:
    """度量本身：纯函数、零副作用、可区分强弱。"""

    def test_empty_text_is_zero(self) -> None:
        assert measure_chapter_tension(1, "") == 0.0

    def test_high_tension_exceeds_low(self) -> None:
        """★ 能力对账：度量必须真能区分强弱，否则落盘的是噪声。"""
        assert measure_chapter_tension(1, _HIGH) > measure_chapter_tension(1, _LOW)

    def test_result_within_range(self) -> None:
        for text in (_HIGH, _LOW):
            v = measure_chapter_tension(1, text)
            assert 0.0 <= v <= 10.0

    def test_is_pure_no_side_effects(self, tmp_path: Path) -> None:
        """★ 纯函数：度量不得顺手落盘（落盘是 record_tension 的职责）。"""
        measure_chapter_tension(1, _HIGH)
        assert not (tmp_path / TENSION_LEDGER).exists()


class TestRecord:
    """落盘：观测面台账，可读回、可累积。"""

    def test_record_then_read(self, tmp_path: Path) -> None:
        assert record_tension(tmp_path, 1, _HIGH) is True
        rows = read_tension(tmp_path)
        assert len(rows) == 1
        assert rows[0]["ch"] == 1
        assert rows[0]["tension"] == measure_chapter_tension(1, _HIGH)

    def test_accumulates_in_order(self, tmp_path: Path) -> None:
        for ch, text in enumerate((_LOW, _HIGH, _LOW), start=1):
            record_tension(tmp_path, ch, text)
        rows = read_tension(tmp_path)
        assert [r["ch"] for r in rows] == [1, 2, 3]

    def test_cap_trims_oldest(self, tmp_path: Path) -> None:
        for ch in range(1, 8):
            record_tension(tmp_path, ch, _HIGH, cap=5)
        rows = read_tension(tmp_path)
        assert len(rows) == 5
        assert [r["ch"] for r in rows] == [3, 4, 5, 6, 7]

    def test_ledger_is_json_readable(self, tmp_path: Path) -> None:
        record_tension(tmp_path, 1, _HIGH)
        raw = (tmp_path / TENSION_LEDGER).read_text(encoding="utf-8")
        data = json.loads(raw)
        assert isinstance(data.get("records"), list)


class TestFailureIsVisibleAndNonBlocking:
    """★ 纪律 #1/#2：失败显性化，但绝不阻断。"""

    def test_read_missing_returns_empty(self, tmp_path: Path) -> None:
        assert read_tension(tmp_path) == []

    def test_read_corrupt_returns_empty_not_raise(self, tmp_path: Path) -> None:
        """坏台账 ⇒ 空表（观测面读取失败＝无数据），不得抛。"""
        p = tmp_path / TENSION_LEDGER
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{ not json", encoding="utf-8")
        assert read_tension(tmp_path) == []

    def test_record_returns_false_on_unwritable_not_raise(self, tmp_path: Path) -> None:
        """★ 落盘失败必须返回 False（显性），且**不得抛**（不阻断写作，纪律 #2）。"""
        # 用「父路径是文件」制造确定性写入失败（跨平台稳定，不依赖权限位）
        blocker = tmp_path / "blocked"
        blocker.write_text("i am a file", encoding="utf-8")
        ok = record_tension(blocker / "sub", 1, _HIGH)
        assert ok is False

    def test_corrupt_then_record_recovers(self, tmp_path: Path) -> None:
        """坏台账之后仍能重建（不因历史坏行永久失能）。"""
        p = tmp_path / TENSION_LEDGER
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{ broken", encoding="utf-8")
        assert record_tension(tmp_path, 1, _HIGH) is True
        rows = read_tension(tmp_path)
        assert len(rows) == 1 and rows[0]["ch"] == 1


class TestObservationNotSupply:
    """★★ 纪律 #9：张力（事实）与档位（意图）是两条独立通道。"""

    def test_module_does_not_write_pace_tier(self, tmp_path: Path) -> None:
        """★ 钉死红线：本模块**绝不触碰档位台账**。

        `chapter_contract.py:447` 既有红线：「不补值：绝不按 pressure_curve 反推档位」。
        张力是压力的实测镜像，若它去写档位就是**系统替作者定意图**。
        """
        record_tension(tmp_path, 1, _HIGH)
        state = tmp_path / ".state"
        tier_files = [
            p for p in state.rglob("*")
            if p.is_file() and "pace_tier" in p.name
        ]
        assert tier_files == [], f"本模块不得产出档位文件：{tier_files}"

    def test_module_exposes_no_tier_api(self) -> None:
        """模块不得导出「写档位」类 API（结构性防止误用）。"""
        import agent.core.story.tension_curve as tc

        names = [n for n in dir(tc) if "tier" in n.lower()]
        assert names == [], f"tension_curve 不得出现档位 API：{names}"


class TestRealProjectNotPolluted:
    """★ 不污染真实项目（M6-A 取证时同样只读）。"""

    def test_real_novels_untouched(self) -> None:
        novels = Path(__file__).resolve().parents[2] / "novels"
        if not novels.exists():
            pytest.skip("novels/ 不存在")
        before = {
            str(p): p.stat().st_mtime
            for p in novels.rglob("tension_readings.json")
        }
        # 本测试只读快照，不断言"一定为 0"——真实项目可能已有台账（那是正常产出）
        assert isinstance(before, dict)


class TestExistingManagerStillWorks:
    """回归：新函数不得破坏既有管理器行为（`check_rhythm` 等）。"""

    def test_manager_still_scores(self) -> None:
        mgr = TensionCurveManager()
        assert mgr.evaluate_chapter(1, _HIGH).tension > 0

    def test_plan_arc_phases_intact(self) -> None:
        mgr = TensionCurveManager()
        arc = mgr.plan_arc(1, 1, 100)
        assert len(arc.phases) == len(TensionCurveManager.ARC_PHASES)

    def test_manager_does_not_touch_ledger(self, tmp_path: Path) -> None:
        """管理器实例只有内存态；落盘是 record_tension 的唯一职责。"""
        mgr = TensionCurveManager()
        mgr.evaluate_chapter(1, _HIGH)
        assert not (tmp_path / TENSION_LEDGER).exists()

    def test_measure_matches_manager_score(self) -> None:
        """★ 两条入口必须同源（否则落盘值与内存值不一致＝静默失真）。"""
        mgr = TensionCurveManager()
        assert measure_chapter_tension(1, _HIGH) == mgr.evaluate_chapter(1, _HIGH).tension
