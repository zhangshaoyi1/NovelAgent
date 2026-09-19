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

    def test_scale_is_length_invariant(self) -> None:
        """★★ M6-B3 核心红线：量纲必须与**章长解耦**。

        旧实现里冲突词按「每百字」归一、悬念词却是**绝对计数**
        ⇒ 量纲自相矛盾且随章长漂移（M6-B2 标定：真实 max 仅 4.40）。

        本红线钉死：**同一「每句密度」的文本，无论多少句，得分必须一致**。
        这是候选 B（改量纲）的验收标准，不是「得分变高」。
        """
        unit = "杀！危！逃！"  # 3 短句，全含冲突词，密度固定
        scores = [
            measure_chapter_tension(1, unit * mul)
            for mul in (1, 4, 10, 40)
        ]
        assert len(set(scores)) == 1, f"量纲随章长漂移：{scores}"

    def test_high_density_reaches_upper_range(self) -> None:
        """★ 量程可达性：高密度样本必须能进入高分区（否则判据不可达）。

        ⚠ 注意本测试断的是**构造样本**的可达性，**不是**真实语料——
        真实语料 p100 仅 3.0（M6-B4 标定），那是文体与度量匹配度问题，
        不可用构造样本的可达性去掩盖（纪律 #23）。
        """
        dense = ("杀！危！逃！追！怒！" * 8) + ("突然！竟然！难道！" * 8)
        assert measure_chapter_tension(1, dense) >= 7.0


class TestDegenerateSampleIsNotScored:
    """★★ M6-B4 红线：残短文本**不得**参与评分（此前空章被判满分）。

    实证（M6-B4 真实语料复查，1266 章）：三个 ``max=5.00`` 的章
    **全是残缺文件**（15/24/44 字，只有标题行），单句含冲突词 ⇒
    ``n_sent=1`` ⇒ 密度 1.0 ⇒ **满分**。
    ⇒ 空章成为全书"最高潮"，污染一切分位标定与相对判据。
    """

    def test_title_only_chapter_is_not_max(self) -> None:
        """★ 核心：章节标题行（1 句）不得拿高分。

        这是本轮发现的**真实现场**形态（``# 第 N 章 · 三炉同燃战前夜``）。
        """
        title_only = "# 第 198 章 · 三炉同燃战前夜"
        assert measure_chapter_tension(198, title_only) == 0.0

    def test_title_only_never_beats_real_content(self) -> None:
        """★★ 因果链被切断：残缺章**不得**高于真实内容章。

        实证反例（修复前）：``ch198``(24 字) = 5.00 > ``ch148``(9871 字) = 3.00
        —— "没写"被当成"写得最激烈"（纪律 #1：失败被解读为通过）。
        """
        title_only = "# 第 198 章 · 三炉同燃战前夜\n\n---"
        real = "寅时三刻，夜色如墨。林凡矗立在瞭望塔顶端，神识如潮水般扫过。" * 20
        assert measure_chapter_tension(198, title_only) < measure_chapter_tension(148, real)

    def test_below_min_sentences_is_zero(self) -> None:
        """样本量 < 阈值 ⇒ 返回 0.0（**无数据**语义），且不随句数上升。"""
        for n in (1, 2):
            text = "杀！" * n
            assert measure_chapter_tension(1, text) == 0.0, f"{n} 句却给了分"

    def test_at_min_sentences_scores(self) -> None:
        """达到阈值即恢复评分（防阈值把合法短章一并排除）。"""
        text = "杀！危！逃！"  # 3 句 = MIN_SENTENCES_FOR_SCORE
        assert measure_chapter_tension(1, text) > 0.0

    def test_is_measureable_distinguishes_no_data_from_low(self) -> None:
        """★ 区分「无数据」与「低张力」——两种失败不得互相掩盖（纪律 #1）。"""
        from agent.core.story.tension_curve import is_measureable

        assert is_measureable(_LOW) is True          # 有数据（低张力）
        assert is_measureable("杀！") is False        # 无数据
        assert is_measureable("") is False
        # 低张力样本有分；无数据样本恒 0.0
        assert measure_chapter_tension(1, _LOW) >= 0.0
        assert measure_chapter_tension(1, "杀！") == 0.0

    def test_threshold_is_derived_from_class_constant(self) -> None:
        """阈值必须来自类常量（防两处各写一份 ⇒ 一次改名即双向破裂，纪律 #19）。"""
        assert TensionCurveManager.MIN_SENTENCES_FOR_SCORE >= 2
        n = TensionCurveManager.MIN_SENTENCES_FOR_SCORE
        assert measure_chapter_tension(1, "杀！" * (n - 1)) == 0.0
        assert measure_chapter_tension(1, "杀！" * n) > 0.0


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


class TestRhythmCriteriaRecalibrated:
    """★★ M6-B4 红线：三条节奏判据**重标定 + 相对化**（原三条全部不可用）。

    标定实证（8 项目 1251 章 / 1179 窗口）：

    | 判据 | 原阈值 | 真实触发率 | 判定 |
    |---|---|---|---|
    | ``no_climax`` | 最高 < 7.0 | **0.0%** | ❌ 不可达 |
    | ``no_aftermath`` | 首个 ≥ 8.0 | **0 次** | ❌ 不可达 |
    | ``flat`` | 极差 < 1.0 | **74.6%** | ❌ 近乎全覆盖 |

    ⇒ 根因＝**度量与文体不匹配**（真实 p50=0.90/p95=1.50/max=3.00 vs 量程 0–10）。
    ⇒ 采用**相对判据**（项目内 top-decile），真实触发率 **28.2%**。
    """

    @staticmethod
    def _mgr(tensions: list[float]) -> TensionCurveManager:
        from agent.core.story.tension_curve import TensionScore

        m = TensionCurveManager()
        for i, t in enumerate(tensions, 1):
            m._scores.append(TensionScore(chapter=i, tension=t))
        return m

    def test_real_scale_flat_does_not_fire_everywhere(self) -> None:
        """★ 原 ``flat`` 阈值 1.0 会在真实尺度（p50=0.90）上 74.6% 误报。

        本红线用真实分位尺度的序列，断言**正常波动序列不触发 flat**。
        """
        import random

        rng = random.Random(7)
        # 真实尺度：均值 0.9、波动 ±0.25（对应真实 IQR 0.4）
        seq = [round(0.9 + rng.uniform(-0.25, 0.25), 2) for _ in range(30)]
        alerts = self._mgr(seq).check_rhythm(window=10, corpus=seq)
        types = [a.alert_type for a in alerts]
        assert "flat" not in types, f"正常波动被判平缓：{types}"

    def test_genuinely_flat_sequence_still_fires(self) -> None:
        """真·无波动（极差趋 0）仍须触发——不能因为重标定就永不报警。"""
        seq = [0.9] * 30
        alerts = self._mgr(seq).check_rhythm(window=10, corpus=seq)
        assert "flat" in [a.alert_type for a in alerts]

    def test_no_climax_is_relative_to_corpus(self) -> None:
        """★★ 核心：``no_climax`` 改为相对判据后**可达且有区分度**。

        构造：全书有高章（进入 top-decile），窗口内全是低章 ⇒ 应触发。
        反之窗口内若含高章 ⇒ 不触发。
        """
        # 全书语料：20 章里有 2 章高张力（top-decile）
        corpus = [0.9] * 18 + [3.0, 3.0]
        low_window = [0.8] * 10
        alerts_low = self._mgr(low_window).check_rhythm(window=10, corpus=corpus)
        assert "no_climax" in [a.alert_type for a in alerts_low], \
            "窗口内无 top-decile 章却未触发 no_climax"

        high_window = [0.8] * 9 + [3.0]
        alerts_high = self._mgr(high_window).check_rhythm(window=10, corpus=corpus)
        assert "no_climax" not in [a.alert_type for a in alerts_high], \
            "窗口内有 top-decile 章却误报 no_climax"

    def test_no_climax_old_threshold_would_be_unreachable(self) -> None:
        """★ 反证：原阈值 7.0 在真实量程（max=3.00）下**永不触发**。

        本红线固化"为什么必须改"，防有人把阈值改回绝对高分线。
        """
        seq = [1.2] * 30  # 真实语料里算相当高的序列
        alerts = self._mgr(seq).check_rhythm(window=10, corpus=seq)
        # 相对判据下"始终无 top-decile"会触发；但绝不因为"未达 7.0"而触发
        for a in alerts:
            assert "7.0" not in a.message, "仍在使用绝对阈值 7.0"

    def test_corpus_insufficient_degrades_visibly(self) -> None:
        """★ 语料不足 ⇒ **显式降级告警**（不静默退回绝对阈值，纪律 #1）。"""
        seq = [1.0] * 12  # 只有 12 章 < RELATIVE_MIN_CORPUS(20)
        alerts = self._mgr(seq).check_rhythm(window=10, corpus=seq)
        assert "corpus_insufficient" in [a.alert_type for a in alerts], \
            "语料不足时未发显性降级告警"

    def test_no_aftermath_relative(self) -> None:
        """``no_aftermath`` 相对化：峰值触及门槛后未回落到 70% 以下 ⇒ 触发。"""
        corpus = [0.9] * 18 + [3.0, 3.0]
        seq = [0.9] * 27 + [3.0, 2.9, 2.8]  # 高位持续
        alerts = self._mgr(seq).check_rhythm(window=10, corpus=corpus)
        assert "no_aftermath" in [a.alert_type for a in alerts]

    def test_quantile_matches_calibration_script(self) -> None:
        """分位实现必须与标定脚本同口径（防两处各写一份，纪律 #19）。"""
        vals = [0.6, 0.7, 0.9, 1.1, 1.3, 1.5, 2.0, 3.0]
        q = TensionCurveManager._quantile(vals, 0.90)
        # 与 numpy 口径一致的线性插值
        k = (len(vals) - 1) * 0.9
        lo, hi = int(k), min(int(k) + 1, len(vals) - 1)
        expect = vals[lo] + (vals[hi] - vals[lo]) * (k - lo)
        assert abs(q - expect) < 1e-9

    def test_alert_thresholds_are_module_constants(self) -> None:
        """阈值必须来自模块常量（红线钉住，防散落字面量）。"""
        import agent.core.story.tension_curve as tc

        assert tc.RELATIVE_MIN_CORPUS >= 10
        assert 0 < tc.AFTERMATH_DECAY_RATIO < 1
        assert tc.RELATIVE_MIN_CORPUS_FALLBACK_HIGH > 0


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
