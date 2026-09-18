"""红线：章级强度档位供给采样闸（M1/D2 配套，2026-09-19）。

## 这个闸是什么

`check_pace_tier_supply()` 采样「**已经按章供给的章**里有多少同时标了强度档位」，
是 D2（档位作为评委参照系）的**供给侧观测面**。设计定稿见方案文档 §11.9 / §11.10。

## 为什么独立成闸（不与 `check_subline_plot_source` 合并）

1. **既有红线会破**：`test_plan_consistency_chapter_level.py::test_full_window_coverage_passes`
   断言「窗口已全覆盖 ⇒ 零告警」（`assert not console.lines`）。把档位告警塞进
   同一函数，该断言必破（2026-09-18 读红线时发现 ⇒ §11.10 修正 A）。
2. **不同量级**：历史书逐章行为 0（阶段级供给）⇒ 档位覆盖率必然为 0。
   与逐章行覆盖率共用阈值/告警 = 把"历史书没有档位"和"新书忘标"混为一谈
   （纪律 #20：闸门强度必须与证据匹配）。
3. **D3 未定档** ⇒ 本闸**只采样、恒不阻断**。

## 本文件钉住的因果链（纪律 #11）

1. **分母语义**：分母 = 窗口内**有逐章行**的章；纯阶段级支线**不进分母**（不采样）
   —— 否则指标恒为 0、失去分辨力；
2. **零分母 ⇒ 静默**：没有逐章行的支线**不告警**（那是判据 2 的管辖范围，
   重复报障无增量信息，且会对所有历史书刷屏）；
3. **恒不阻断**：无论覆盖率多低，`check_pace_tier_supply` 返回 `[]`，
   `prepare_for_write` 放行 —— 强度不得超过判据（D3 前无阈值，纪律 #2/#13）；
4. **留痕必有**：每次采样落 `.state/plan_gate_pace_tier.jsonl`，且含 `missing`
   章号明细（"哪几章没标"必须可事后核对，不能只有一个比例数字）；
5. **与判据 2 解耦**：档位告警**不得**污染 `check_subline_plot_source` 的告警面；
6. **作用域＝写作窗口 ∩ 支线区间**（远期支线不污染当前采样）。
"""

from __future__ import annotations

import json
from pathlib import Path

from agent.workflows.pipeline.plan_consistency import (
    _PACE_TIER_LEDGER,
    check_pace_tier_supply,
    check_subline_plot_source,
    prepare_for_write,
    read_pace_tier_supply,
)

_HEAD = """---
subline_id: "S01"
---

# 支线设定

## 剧集压力曲线

| 阶段 | 章节 | 张力等级 |
|---|---|---|
| 铺垫 | 1-40 | 低 |

## 章节钩子设计

{lines}

## 情节点序列

第1章：情节一
"""

#: 3 章有逐章行，其中 2 章标了档位 ⇒ 覆盖率 2/3
_PARTIAL_TIER = _HEAD.format(
    lines="\n".join(
        [
            "第1章：章首钩子=甲｜章尾钩子=乙｜档位=推进",
            "第2章：章首钩子=丙｜章尾钩子=丁｜档位=垫片",
            "第3章：章首钩子=戊｜章尾钩子=己",  # 无档位
        ]
    )
)

#: 全标档位 ⇒ 零告警
_FULL_TIER = _HEAD.format(
    lines="\n".join(
        [
            "第1章：章首钩子=甲｜章尾钩子=乙｜档位=推进",
            "第2章：章首钩子=丙｜章尾钩子=丁｜档位=垫片",
            "第3章：章首钩子=戊｜章尾钩子=己｜档位=日常",
        ]
    )
)

#: 有逐章行但**一个档位都没标**（M1 落地前的存量项目形态）
_NO_TIER = _HEAD.format(
    lines="\n".join(f"第{i}章：章首钩子=甲{i}｜章尾钩子=乙{i}" for i in range(1, 4))
)

#: ★ 覆盖**满窗口 1-20** 的逐章行。用于「解耦」判据：只有逐章行覆盖满窗口
#:   （判据 3 零告警）时，才能干净地验证"档位缺位不污染判据 2 的告警面"。
#:   ⚠ 写作样本时踩过一次：首版用 3 章逐章行 ⇒ 判据 3（覆盖率 3/20 < 90%）
#:   本就告警，失败原因与档位无关 ⇒ 判据的**前置条件必须自己先满足**。
_WINDOW_FULL_NO_TIER = _HEAD.format(
    lines="\n".join(f"第{i}章：章首钩子=甲{i}｜章尾钩子=乙{i}" for i in range(1, 21))
)

#: 覆盖满窗口、其中第 20 章缺档位（用于解耦判据的"档位闸要响"一侧）
_WINDOW_FULL_PARTIAL_TIER = _HEAD.format(
    lines="\n".join(
        f"第{i}章：章首钩子=甲{i}｜章尾钩子=乙{i}"
        + ("" if i == 20 else "｜档位=推进")
        for i in range(1, 21)
    )
)

#: 纯阶段级（无任何逐章行）⇒ 不进分母、不采样、不告警
_STAGE_ONLY = """---
subline_id: "S01"
---

# 支线设定

## 剧集压力曲线

| 阶段 | 章节 | 张力等级 |
|---|---|---|
| 铺垫 | 1-40 | 低 |

## 章节钩子设计

铺垫阶段：章尾=日常小悬念（弱）

## 情节点序列

铺垫阶段：主角适应环境
"""

#: 逐章行全在**远期**（窗口 1-20 之外）⇒ 作用域过滤后分母为 0 ⇒ 静默
_FAR_FUTURE = _HEAD.format(
    lines="\n".join(f"第{i}章：章首钩子=甲{i}" for i in (30, 31, 32))
).replace("| 铺垫 | 1-40 | 低 |", "| 铺垫 | 30-40 | 低 |")


class _CollectConsole:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, *a, **k) -> None:  # noqa: ANN002, ANN003
        self.lines.append(" ".join(str(x) for x in a))


def _make_project(tmp_path: Path, content: str, *, total_written: int = 0) -> Path:
    d = tmp_path / "sublines" / "S01_test"
    d.mkdir(parents=True, exist_ok=True)
    (d / "subline.md").write_text(content, encoding="utf-8")
    state = tmp_path / ".state" / "state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(
        json.dumps({"progress": {"total_written": total_written}}, ensure_ascii=False),
        encoding="utf-8",
    )
    return tmp_path


# ============================================================
# 一、恒不阻断（最重要：本闸不得授权任何拦截）
# ============================================================
class TestNeverBlocks:
    def test_returns_empty_even_with_zero_coverage(self, tmp_path: Path) -> None:
        """★ 一个档位都没标 ⇒ 仍然返回 []（D3 定档前没有阈值可拦）。"""
        p = _make_project(tmp_path, _NO_TIER)
        assert check_pace_tier_supply(p, console=_CollectConsole()) == []

    def test_returns_empty_with_partial_coverage(self, tmp_path: Path) -> None:
        p = _make_project(tmp_path, _PARTIAL_TIER)
        assert check_pace_tier_supply(p, console=_CollectConsole()) == []

    def test_prepare_for_write_not_blocked(self, tmp_path: Path) -> None:
        """端点断言：写前统一入口不得因"没标档位"拒绝开工。"""
        p = _make_project(tmp_path, _NO_TIER)
        assert not prepare_for_write(p, console=_CollectConsole())

    def test_ledger_failure_does_not_block(self, tmp_path: Path) -> None:
        """★ 台账落盘失败**不转致命**——与 `record_stage_level_supply` 刻意不同。

        理由：本闸恒不阻断，留痕失败只意味着"这次没记上"（观测缺失），
        不构成对"能否开工"的证据 ⇒ 用它拦人＝动作强度超过判据（纪律 #2）。
        手法：把 `.state` 位置占成一个**文件**，使其下无法建目录/写文件。
        """
        p = tmp_path
        (p / "sublines" / "S01_test").mkdir(parents=True)
        (p / "sublines" / "S01_test" / "subline.md").write_text(_PARTIAL_TIER, encoding="utf-8")
        (p / ".state").write_text("占位：使其无法作为目录写入", encoding="utf-8")
        assert check_pace_tier_supply(p, console=_CollectConsole()) == [], "留痕失败却转成了致命"
        assert not (p / _PACE_TIER_LEDGER).exists()


# ============================================================
# 二、分母语义（本闸最贵的设计点）
# ============================================================
class TestDenominatorSemantics:
    def test_stage_only_warns_not_but_records(self, tmp_path: Path) -> None:
        """★ 纯阶段级支线：**不告警**，但**要留台账**（记 no_chapter_line）。

        ⚠ 本条在 2026-09-19 真实项目跑闸后修正过一次口径：
        初版"不进分母 ⇒ 连台账都不记"会让「为什么窗口内拿不到档位」在观测面上
        **不可见**（纪律 #21：缺口 ＝ 静默失真）。现为：分母＝窗口内全部应写章，
        无逐章契约的章记入 `no_chapter_line`——不告警（归判据 2 管），但留痕。
        """
        p = _make_project(tmp_path, _STAGE_ONLY)
        c = _CollectConsole()
        assert check_pace_tier_supply(p, console=c) == []
        assert not c.lines, f"纯阶段级支线被采样告警了（应归判据 2）：{c.lines}"
        rec = _last_record(p)
        assert rec["covered"] == 0, "纯阶段级不应有'有逐章契约的章'"
        assert rec["no_chapter_line"], "窗口内无逐章契约的事实必须留痕（否则不可见）"

    def test_denominator_counts_only_chapter_lines(self, tmp_path: Path) -> None:
        """`covered`（有逐章行的章数）与 `no_chapter_line` 是两个不同字段。"""
        p = _make_project(tmp_path, _PARTIAL_TIER)
        check_pace_tier_supply(p, console=_CollectConsole())
        rec = _last_record(p)
        assert rec["chapter_lines"] == [1, 2, 3]
        assert rec["tiered"] == [1, 2]
        # 窗口 1-20、只有 1-3 有逐章行 ⇒ 4-20 共 17 章无契约
        assert rec["no_chapter_line"] == list(range(4, 21)), rec["no_chapter_line"]

    def test_missing_chapter_numbers_are_recorded(self, tmp_path: Path) -> None:
        """missing 必须是**章号明细**而非比例——"哪几章没标"要能事后核对。"""
        p = _make_project(tmp_path, _PARTIAL_TIER)
        check_pace_tier_supply(p, console=_CollectConsole())
        assert _last_record(p)["missing"] == [3]

    def test_no_chapter_line_does_not_warn(self, tmp_path: Path) -> None:
        """★ 职责边界：本闸只管"**有**章级契约却忘标档位"；
        窗口内**没有**契约的那些章（no_chapter_line）**不告警**（归判据 2）。

        ⚠ 写作样本时踩过一次：首版第1章写成「章首钩子=甲」（没带档位）⇒
        它自己进了 missing ⇒ 告警，**失败原因与被测意图无关**。
        ⇒ 判据样本必须先满足"不该响的那些条件"（第1章要带档位）。
        """
        one_tiered = _HEAD.format(lines="第1章：章首钩子=甲｜章尾钩子=乙｜档位=推进")
        # 窗口 1-20：第1章有行且带档位 ⇒ missing 为空 ⇒ 静默；
        # 2-20 无逐章契约 ⇒ 只记 no_chapter_line，不告警。
        p = _make_project(tmp_path, one_tiered)
        c = _CollectConsole()
        check_pace_tier_supply(p, console=c)
        assert not c.lines, f"唯一有契约的章已带档位，不应告警：{c.lines}"
        rec = _last_record(p)
        assert rec["tiered"] == [1] and rec["missing"] == []
        assert rec["no_chapter_line"] == list(range(2, 21)), rec["no_chapter_line"]

    def test_far_future_chapters_excluded_by_scope(self, tmp_path: Path) -> None:
        """作用域过滤后空区间 ⇒ 静默（远期支线不污染当前采样）。"""
        p = _make_project(tmp_path, _FAR_FUTURE)
        c = _CollectConsole()
        check_pace_tier_supply(p, console=c)
        assert not c.lines, f"远期支线被采样告警：{c.lines}"


# ============================================================
# 三、告警形态（覆盖不足必须"报得准"）
# ============================================================
class TestWarningContent:
    def test_partial_coverage_warns_with_chapter_numbers(self, tmp_path: Path) -> None:
        p = _make_project(tmp_path, _PARTIAL_TIER)
        c = _CollectConsole()
        check_pace_tier_supply(p, console=c)
        assert c.lines, "覆盖不足却零告警"
        joined = "\n".join(c.lines)
        assert "第3章" in joined, "告警没点名缺档位的章"
        assert "1-20" in joined, "告警没说明作用域"

    def test_warning_says_not_blocking(self, tmp_path: Path) -> None:
        """告警文本必须自陈「只采样、不阻断」——否则运维会以为该修到能过闸。"""
        p = _make_project(tmp_path, _PARTIAL_TIER)
        c = _CollectConsole()
        check_pace_tier_supply(p, console=c)
        assert "不阻断" in "\n".join(c.lines)

    def test_full_coverage_is_silent(self, tmp_path: Path) -> None:
        """全覆盖 ⇒ 零告警（本闸的"健康"语义就是静默）。"""
        p = _make_project(tmp_path, _FULL_TIER)
        c = _CollectConsole()
        check_pace_tier_supply(p, console=c)
        assert not c.lines, f"全覆盖仍告警：{c.lines}"
        # 但仍应留痕（采样送达）
        assert _last_record(p)["missing"] == []


# ============================================================
# 四、与判据 2 解耦（§11.10 修正 A 的红线化）
# ============================================================
class TestDecoupledFromPlotSourceGate:
    def test_does_not_touch_plot_source_console(self, tmp_path: Path) -> None:
        """★ 档位告警**不得**出现在 `check_subline_plot_source` 的告警面。

        既有红线 `test_full_window_coverage_passes` 断言「窗口全覆盖 ⇒ 零告警」；
        若两闸共用 console/告警，该断言必破。

        ⚠ 样本必须**覆盖满窗口**：用 3 章逐章行的样本会让判据 3（覆盖率不足）
        本来就有告警，那样就分不清"告警来自档位还是来自覆盖率" ⇒ 判据的
        前置条件必须先自己满足（本文件写作时踩过一次）。
        """
        p = _make_project(tmp_path, _WINDOW_FULL_NO_TIER)  # 窗口覆盖满、档位全缺
        c = _CollectConsole()
        errs = check_subline_plot_source(p, console=c)
        assert not errs
        assert not c.lines, f"档位告警污染了判据 2 的控制台：{c.lines}"

    def test_both_gates_coexist_in_console(self, tmp_path: Path) -> None:
        """两闸各自独立：档位闸告警时，判据 2 仍然零告警（前置条件已满足）。"""
        p = _make_project(tmp_path, _WINDOW_FULL_PARTIAL_TIER)
        c1 = _CollectConsole()
        check_pace_tier_supply(p, console=c1)
        assert c1.lines, "窗口覆盖满但有一章缺档位 ⇒ 档位闸应告警"
        assert "第20章" in "\n".join(c1.lines), "档位闸未点名缺档位的章"
        c2 = _CollectConsole()
        errs = check_subline_plot_source(p, console=c2)
        assert not errs
        assert not c2.lines, f"判据 2 被档位问题带出告警：{c2.lines}"


# ============================================================
# 五、留痕（观测面必须有可核对的落盘）
# ============================================================
class TestLedger:
    def test_ledger_written_with_schema(self, tmp_path: Path) -> None:
        p = _make_project(tmp_path, _PARTIAL_TIER)
        check_pace_tier_supply(p, console=_CollectConsole())
        rec = _last_record(p)
        for key in ("ts", "subline", "gate", "scope", "window", "covered", "tiered_count"):
            assert key in rec, f"台账缺字段 {key}"
        assert rec["gate"] == "pace_tier_supply"

    def test_reader_roundtrip(self, tmp_path: Path) -> None:
        p = _make_project(tmp_path, _PARTIAL_TIER)
        check_pace_tier_supply(p, console=_CollectConsole())
        got = read_pace_tier_supply(p)
        assert got["total"] == 1
        assert "S01_test" in got["latest"]
        assert got["latest_ts"], "读取器未给出最近时间戳"

    def test_reader_tolerates_missing_and_corrupt(self, tmp_path: Path) -> None:
        """观测面不该反过来阻断：缺失/坏行一律返回空表，不抛。"""
        p = _make_project(tmp_path, _PARTIAL_TIER)
        assert read_pace_tier_supply(p)["total"] == 0
        led = p / _PACE_TIER_LEDGER
        led.write_text("{坏行}\n\n{又一行坏}\n", encoding="utf-8")
        got = read_pace_tier_supply(p)
        assert got["total"] == 0, "坏行不该被计入"

    def test_appends_not_overwrites(self, tmp_path: Path) -> None:
        """台账是**追加式**：两次采样必须两条（用于看趋势/收敛）。"""
        p = _make_project(tmp_path, _PARTIAL_TIER)
        check_pace_tier_supply(p, console=_CollectConsole())
        check_pace_tier_supply(p, console=_CollectConsole())
        assert read_pace_tier_supply(p)["total"] == 2

    def test_acknowledged_absent_by_design(self, tmp_path: Path) -> None:
        """本闸**没有 acknowledged 字段**——因为恒不阻断 ⇒ 无闸门即无需豁免。

        （对比 `record_stage_level_supply` 有 `acknowledged`：那是 fail-fast 闸的豁免。）
        若后人给它加上豁免语义，说明强度被误升了 ⇒ 本判据先钉住。
        """
        p = _make_project(tmp_path, _PARTIAL_TIER)
        check_pace_tier_supply(p, console=_CollectConsole())
        assert "acknowledged" not in _last_record(p)


# ============================================================
# 六、编码期发现：真源委托（禁止重写正则，纪律 #19）
# ============================================================
def test_tier_parsing_delegates_to_single_source() -> None:
    """档位解析必须委托 `chapter_contract.pace_tier_of`，不在此重写正则（纪律 #19）。"""
    import inspect

    from agent.workflows.pipeline import plan_consistency as pc

    src = inspect.getsource(pc._pace_tier_at)
    assert "pace_tier_of" in src, "未委托唯一真源"
    assert "re.compile" not in src, "在此重写了正则（应为唯一真源的调用点）"
    assert "PACE_TIER" not in src, "在此复制了档位表"


def test_ledger_path_under_state() -> None:
    """台账路径约定：`.state/` 下（与同族台账一致，便于统一巡检）。"""
    assert _PACE_TIER_LEDGER == Path(".state") / "plan_gate_pace_tier.jsonl"


# ============================================================
# 辅助
# ============================================================
def _last_record(project_dir: Path) -> dict:
    led = project_dir / _PACE_TIER_LEDGER
    assert led.exists(), f"未写台账：{led}"
    lines = [ln for ln in led.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert lines, "台账为空"
    return json.loads(lines[-1])
