"""红线：规划评委（M5，采样模式，**恒不阻断**）。

## 这个模块是什么

``core/story/plan_critic.py`` 是 ``plan_managers.py:11`` 自认缺口的落地：

    「语义类判断（"这条弧线是否合理推进传承线"）留给 LLM 管理者（**未落地**）」

它把规划层评审补齐为**两层**：
  - 机器统计判据（零 LLM，全量）：G1 段级+章级双级粒度 / G2 段内落差；
  - LLM 抽样语义判据：S1/S2/S3（母题推进 / 同质化 / 因果断链）。

## 为什么必须「只记录不拦」（方案 §8.3 关键纪律）

    ① 先「只记录不拦」采样（零风险）
    ② 跑一本新书前 20 章，统计：误报率 + ★历史达成率
    ③ 据 ①② 定档（blocking / 告警）

⚠ 两条硬约束（纪律 #13）：**历史达成率 < 50% 的判据不可升 blocking**；
   **不可达判据**先修判据本身。

## 本文件钉住的因果链（纪律 #11）

1. **恒不阻断**：``review_plan`` 是采样模式，**不存在"致命错误"这个概念**
   —— 调用方拿不到任何能阻断写作的返回值/异常（动作强度 ≤ 判据，纪律 #2）；
2. **失败必须显性化**（纪律 #1，**本模块最贵的一条**）：语义评审没跑/失败时
   必须给出 ``semantic_ran=False`` + ``semantic_error``，**绝不允许**被解读为
   "评审通过、没问题"——这是"假失败/静默通过"的一号病在评审层的复现点；
3. **留痕失败不转致命**：落盘失败只降级为"观测缺失"，不授权拦截
   （与 ``plan_managers.save_audit_report`` 的刻意差异）；
4. **误报率不得猜**（纪律 #22）：无人工复核结论时 ``false_positive_rate`` 必须
   为 ``None``，不得由任何自动信号反推；
5. **历史达成率 < 50% ⇒ ``unreachable_risk``**（纪律 #13 的机器化）；
6. **判据 key 稳定**：``judge`` 是历史达成率统计的分组键，改名即断历史
   （红线钉住它的字面量集合）；
7. **与 ``plan_managers`` 作用域不重叠**：本模块**不产 BLOCK**、不重复报
   弧线衔接/重叠/空洞（那归确定性四管理者）。
8. **无数据 ≠ 通过**（2026-09-19 D 项 D1 追加）：``read_plan_critic`` 以
   ``status`` 区分「没审」（``no_data``/``empty``）与「审了没问题」（``ok``），
   ``aggregate_readings`` 无样本时返回 ``{"__status__": "no_data", ...}``
   **而非 `{}`** —— 空表会被读成"全判据可达"（纪律 #1）。
   该语义的专项红线见 ``tests/architecture/test_plan_review_reachability.py``。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from agent.core.story.plan_critic import (
    CRITIC_LEDGER,
    LEVEL_NOTE,
    LEVEL_UNREACHABLE,
    LEVEL_WARN,
    PlanCriticReport,
    aggregate_readings,
    judge_plan_granularity,
    judge_rhythm_peaks,
    read_plan_critic,
    record_plan_critic,
    review_plan,
)

# ---------------------------------------------------------------- 样本

_HEAD = """---
subline_id: "S01"
---

# 支线设定

## 剧集压力曲线

| 阶段 | 章节 | 张力等级 |
|---|---|---|
| 铺垫 | 1-10 | 低 |
| 冲突 | 11-20 | 中 |
| 高潮 | 21-25 | 高 |
| 舒缓 | 26-30 | 低 |

## 章节钩子设计

### 章节钩子设计

{hooks}

## 章节强度档位

{tiers}

## 情节点序列

{points}
"""

#: 双级齐全：段级 4 阶段 + 章级 3 行 + 档位 3 行
_DUAL_LEVEL = _HEAD.format(
    hooks="\n".join(f"第{i}章：章首钩子=甲{i}｜章尾钩子=乙{i}" for i in (1, 2, 3)),
    tiers="\n".join(f"第{i}章：{t}" for i, t in zip((1, 2, 3), ("推进", "垫片", "日常"))),
    points="\n".join(f"第{i}章：情节{i}" for i in (1, 2, 3)),
)

#: 只有段级（历史书的合法降级形态，实测 6 本靠它写成 150-350 章）
_STAGE_ONLY = _HEAD.format(
    hooks="铺垫阶段：章尾=日常小悬念（弱）",
    tiers="",
    points="铺垫阶段：主角适应环境",
)

#: 有逐章行但零档位（v5 存量形态；M1-Fix 前的真实项目形态）
_NO_TIER = _HEAD.format(
    hooks="\n".join(f"第{i}章：章首钩子=甲{i}" for i in (1, 2, 3)),
    tiers="",
    points="\n".join(f"第{i}章：情节{i}" for i in (1, 2, 3)),
)

#: 段级缺一阶段（压力曲线只有 3 个阶段）
_STAGE_INCOMPLETE = (
    _HEAD.format(
        hooks="\n".join(f"第{i}章：章首钩子=甲{i}" for i in (1, 2, 3)),
        tiers="\n".join(f"第{i}章：推进" for i in (1, 2, 3)),
        points="\n".join(f"第{i}章：情节{i}" for i in (1, 2, 3)),
    ).replace("| 舒缓 | 26-30 | 低 |", "")
)

#: 档位全程同一档（段内零落差）
_FLAT_TIERS = _HEAD.format(
    hooks="\n".join(f"第{i}章：章首钩子=甲{i}" for i in range(1, 7)),
    tiers="\n".join(f"第{i}章：推进" for i in range(1, 7)),
    points="\n".join(f"第{i}章：情节{i}" for i in range(1, 7)),
)

#: 多档交替（有起伏）⇒ 不应触发 flat
_VARIED_TIERS = _HEAD.format(
    hooks="\n".join(f"第{i}章：章首钩子=甲{i}" for i in range(1, 7)),
    tiers="\n".join(
        f"第{i}章：{t}"
        for i, t in zip(range(1, 7), ("推进", "垫片", "高潮", "日常", "推进", "垫片"))
    ),
    points="\n".join(f"第{i}章：情节{i}" for i in range(1, 7)),
)


#: 档位覆盖不全（3 章逐章行、只有 2 章标档）
_PARTIAL_TIER = _HEAD.format(
    hooks="\n".join(f"第{i}章：章首钩子=甲{i}" for i in (1, 2, 3)),
    tiers="第1章：推进\n第2章：垫片",
    points="\n".join(f"第{i}章：情节{i}" for i in (1, 2, 3)),
)


def _arc(name: str, start: int, end: int, goal: str = "推进母题") -> NS:
    return NS(name=name, chapter_start=start, chapter_end=end, goal=goal)


def _proj(tmp_path: Path, sublines: dict[str, str]) -> Path:
    """建最小项目：只有 sublines/<id>/subline.md。"""
    for sid, content in sublines.items():
        d = tmp_path / "sublines" / sid
        d.mkdir(parents=True, exist_ok=True)
        (d / "subline.md").write_text(content, encoding="utf-8")
    return tmp_path


# ============================================================
# 1. 双级粒度判据
# ============================================================
class TestGranularityJudge:
    def test_dual_level_has_no_finding(self) -> None:
        assert judge_plan_granularity(_DUAL_LEVEL, name="S01") == []

    def test_stage_only_is_note_not_failure(self) -> None:
        """★ 只有段级 ⇒ 记为 NOTE（历史书的合法降级模式），**不是**不合格。

        证据：实测 6 本在写的书靠阶段级供给写成 150-350 章
        （`五灵破归档` 211 章 / `灵荒薪传` 59 章，支线逐章行均为 0）
        ⇒「阶段级 ⇒ 不合格」不成立（纪律 #20：闸门强度必须与证据匹配）。
        """
        fs = judge_plan_granularity(_STAGE_ONLY, name="S01")
        assert fs and all(f.level == LEVEL_NOTE for f in fs)
        assert [f.judge for f in fs] == ["G1.chapter_level_absent"]

    def test_chapter_lines_without_tier_is_warn(self) -> None:
        """★ 有逐章行但**零档位** ⇒ WARN（D2「档位必填」的直接违反）。"""
        fs = judge_plan_granularity(_NO_TIER, name="S01")
        assert [f.judge for f in fs] == ["G1.tier_level_absent"]
        assert fs[0].level == LEVEL_WARN

    def test_stage_incomplete_is_warn(self) -> None:
        fs = judge_plan_granularity(_STAGE_INCOMPLETE, name="S01")
        assert any(f.judge == "G1.stage_level" and f.level == LEVEL_WARN for f in fs)

    def test_partial_tier_coverage_is_note_with_missing_detail(self) -> None:
        """档位覆盖不全 ⇒ NOTE 且**必须带 missing 章号明细**（不只是一个比例）。"""
        fs = judge_plan_granularity(_PARTIAL_TIER, name="S01")
        hit = [f for f in fs if f.judge == "G1.tier_level_partial"]
        assert hit and hit[0].evidence.get("missing") == [3]


# ============================================================
# 2. 段内落差判据
# ============================================================
class TestRhythmJudge:
    def test_flat_tier_sequence_is_warn(self) -> None:
        fs = judge_rhythm_peaks(_FLAT_TIERS, name="S01")
        assert [f.judge for f in fs] == ["G2.flat_tier_sequence"]

    def test_varied_tier_sequence_is_clean(self) -> None:
        assert judge_rhythm_peaks(_VARIED_TIERS, name="S01") == []

    def test_no_tier_section_defers_to_granularity(self) -> None:
        """无档位小节 ⇒ 本判据**静默**（归 G1 报障），不重复刷屏。"""
        assert judge_rhythm_peaks(_NO_TIER, name="S01") == []

    def test_short_sequence_not_judged(self) -> None:
        """样本 < 4 章 ⇒ 不足以判"有无起伏"，不报。"""
        content = _HEAD.format(hooks="", tiers="第1章：推进\n第2章：推进", points="")
        assert judge_rhythm_peaks(content, name="S01") == []


# ============================================================
# 3. ★ 恒不阻断（本模块存在的全部意义）
# ============================================================
class TestNeverBlocks:
    def test_review_plan_returns_report_not_errors(self, tmp_path: Path) -> None:
        """★★ 核心：无论多少发现，``review_plan`` 只返回报告，**不产生致命错误**。"""
        proj = _proj(tmp_path, {"S01": _NO_TIER, "S02": _STAGE_ONLY})
        rep = review_plan(proj, arcs=[], current_chapter=0, llm=None)
        assert isinstance(rep, PlanCriticReport)
        assert rep.findings, "样本应产生发现（否则本测试空转）"
        # 采样模式：没有"错误列表"这个概念；报告里只有 note/warn
        assert all(f.level in (LEVEL_NOTE, LEVEL_WARN, LEVEL_UNREACHABLE) for f in rep.findings)
        assert not any(f.level == "block" for f in rep.findings)

    def test_never_raises_on_broken_project(self, tmp_path: Path) -> None:
        """项目残缺（无 sublines / 坏文件）⇒ 不抛（观测面不该阻断规划）。"""
        rep = review_plan(tmp_path, arcs=[], current_chapter=0, llm=None)
        assert isinstance(rep, PlanCriticReport)

    def test_never_raises_when_arcs_bad(self, tmp_path: Path) -> None:
        """弧线对象形状错（缺属性）⇒ 不抛（LLM 语义评审 side 的鲁棒性）。"""
        proj = _proj(tmp_path, {"S01": _DUAL_LEVEL})
        broken = [NS(name="x")]  # 缺 chapter_start/chapter_end
        rep = review_plan(proj, arcs=broken, current_chapter=0, llm=None)
        assert isinstance(rep, PlanCriticReport)

    def test_module_has_no_block_level(self) -> None:
        """★ 结构性判据：本模块**不导出** BLOCK 常量（采样期不可能拦截）。"""
        from agent.core.story import plan_critic

        assert not hasattr(plan_critic, "BLOCK")
        assert "block" not in {LEVEL_NOTE, LEVEL_WARN, LEVEL_UNREACHABLE}


# ============================================================
# 4. ★ 失败必须显性化（纪律 #1，本模块最贵的一条）
# ============================================================
class TestFailureIsVisible:
    def test_llm_absent_is_recorded_not_silent(self, tmp_path: Path) -> None:
        """★★ LLM 缺席 ⇒ ``semantic_ran=False`` + 自述原因，**不得**当成"没问题"。"""
        proj = _proj(tmp_path, {"S01": _DUAL_LEVEL})
        rep = review_plan(proj, arcs=[_arc("弧一", 1, 20)], current_chapter=0, llm=None)
        assert rep.semantic_ran is False
        assert rep.semantic_error, "LLM 缺席却没有给出原因 ⇒ 静默（纪律 #1 违规）"
        assert "LLM" in rep.semantic_error

    def test_llm_exception_is_recorded(self, tmp_path: Path) -> None:
        """LLM 调用抛异常 ⇒ 记为未跑 + 原因，不把异常当"通过"。"""
        proj = _proj(tmp_path, {"S01": _DUAL_LEVEL})

        class _Boom:
            def chat(self, *a: object, **k: object) -> str:
                raise RuntimeError("上游 404 No available provider")

        rep = review_plan(proj, arcs=[_arc("弧一", 1, 20)], current_chapter=0, llm=_Boom())
        assert rep.semantic_ran is False
        assert rep.semantic_error

    def test_render_marks_semantic_not_run(self, tmp_path: Path) -> None:
        """渲染必须**看得见**"语义评审未运行"（运维读到的是人话，不是沉默）。"""
        proj = _proj(tmp_path, {"S01": _DUAL_LEVEL})
        rep = review_plan(proj, arcs=[], current_chapter=0, llm=None)
        assert "语义评审未运行" in rep.render()

    def test_no_future_arcs_is_self_described(self, tmp_path: Path) -> None:
        """无未来弧线 ⇒ 自述"无可评审对象"（区分"没问题"与"没审"）。"""
        proj = _proj(tmp_path, {"S01": _DUAL_LEVEL})
        rep = review_plan(proj, arcs=[], current_chapter=10, llm=None)
        assert rep.semantic_ran is False and "无未来弧线" in rep.semantic_error

    def test_semantic_json_broken_is_recorded(self, tmp_path: Path) -> None:
        """LLM 返回非 JSON ⇒ 记为未跑（不得把解析失败当"无问题"）。"""
        from agent.core.story import plan_critic as pc

        class _Bad:
            pass

        # 直接打桩 chat_creative 让解析失败
        import agent.client.gateway_adapter as ga

        orig = ga.chat_creative
        ga.chat_creative = lambda *a, **k: "这不是 JSON"
        try:
            fs, err = pc.judge_arc_semantics(
                tmp_path, [_arc("弧一", 1, 20)], current_chapter=0, llm=_Bad()
            )
        finally:
            ga.chat_creative = orig
        assert fs == [] and err and "JSON" in err


# ============================================================
# 5. 留痕
# ============================================================
class TestLedger:
    def test_review_writes_ledger(self, tmp_path: Path) -> None:
        proj = _proj(tmp_path, {"S01": _NO_TIER})
        review_plan(proj, arcs=[], current_chapter=0, llm=None)
        assert (proj / CRITIC_LEDGER).exists()
        data = read_plan_critic(proj)
        assert data["total"] == 1

    def test_ledger_records_sample_mode(self, tmp_path: Path) -> None:
        """★ 台账必须自标 ``mode=sample``：下游统计要能区分"观测项"与"拦截项"。"""
        proj = _proj(tmp_path, {"S01": _NO_TIER})
        review_plan(proj, arcs=[], current_chapter=0, llm=None)
        rec = json.loads((proj / CRITIC_LEDGER).read_text(encoding="utf-8").strip().splitlines()[0])
        assert rec["mode"] == "sample"
        assert rec["gate"] == "plan_critic"

    def test_ledger_failure_does_not_raise(self, tmp_path: Path) -> None:
        """★★ 落盘失败**不转致命**（纯观测面；动作强度不得超过判据，纪律 #2）。"""
        # 把 .state 造成**文件**（而非目录）⇒ mkdir 必失败
        (tmp_path / ".state").write_text("x", encoding="utf-8")
        ok = record_plan_critic(tmp_path, PlanCriticReport(current_chapter=1))
        assert ok is False  # 报失败，但不抛

    def test_read_broken_ledger_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / ".state").mkdir(parents=True, exist_ok=True)
        (tmp_path / CRITIC_LEDGER).write_text("{坏行\n{也坏\n", encoding="utf-8")
        data = read_plan_critic(tmp_path)
        assert data["total"] == 0 and data["latest"] == {}

    def test_read_missing_ledger_returns_empty(self, tmp_path: Path) -> None:
        data = read_plan_critic(tmp_path)
        assert data["total"] == 0


# ============================================================
# 6. ★ 两个读数（M8 定档的唯一输入，§8.3）
# ============================================================
class TestReadings:
    def _seed(self, proj: Path, judges: list[list[str]], chapter: int = 1) -> None:
        for i, js in enumerate(judges):
            rep = PlanCriticReport(current_chapter=chapter, scope="seed")
            from agent.core.story.plan_critic import Finding

            for j in js:
                rep.findings.append(Finding(LEVEL_WARN, j, "granularity", "x"))
            record_plan_critic(proj, rep)

    def test_achieved_rate_and_unreachable_risk(self, tmp_path: Path) -> None:
        """★ 历史达成率 < 50% ⇒ ``unreachable_risk=True``（纪律 #13 机器化）。"""
        # 判据 A：4 次采样命中 1 次 ⇒ 达成率 75%（可达）
        # 判据 B：4 次采样命中 4 次 ⇒ 达成率 0%（不可达）
        self._seed(tmp_path, [["A"], ["A"], ["A"], ["A", "B"], ["B"], ["B"], ["B"]])
        out = aggregate_readings(tmp_path)
        assert out["A"]["samples"] == 7 and out["A"]["flagged"] == 4
        assert out["A"]["achieved_rate"] == pytest.approx(3 / 7, abs=1e-4)
        assert out["A"]["unreachable_risk"] is True  # 42.9% < 50%
        assert out["B"]["achieved_rate"] == 0.0
        assert out["B"]["unreachable_risk"] is True

    def test_reachable_judge_not_flagged(self, tmp_path: Path) -> None:
        # 7 次采样只命中 1 次 ⇒ 达成率 85.7% ≥ 50% ⇒ 可达
        self._seed(tmp_path, [["A"], [], [], [], [], [], []])
        out = aggregate_readings(tmp_path)
        assert out["A"]["achieved_rate"] == pytest.approx(6 / 7, abs=1e-4)
        assert out["A"]["unreachable_risk"] is False

    def test_false_positive_is_none_without_human_review(self, tmp_path: Path) -> None:
        """★★ 误报率**不得猜**（纪律 #22）：无人工复核 ⇒ ``None`` + ``known=False``。"""
        self._seed(tmp_path, [["A"], ["A"]])
        out = aggregate_readings(tmp_path)
        assert out["A"]["false_positive_rate"] is None
        assert out["A"]["false_positive_known"] is False

    def test_false_positive_computed_only_with_review(self, tmp_path: Path) -> None:
        """★ 有人工复核结论时才计算，且**只对该判据**计算（不得跨判据外推）。"""
        self._seed(tmp_path, [["A"], ["B"]])
        out = aggregate_readings(tmp_path, confirmed_by_judge={"A": True})
        assert out["A"]["false_positive_rate"] == 0.0
        assert out["A"]["false_positive_known"] is True
        # B 未被复核 ⇒ 仍然是 None（不得拿 A 的结论估 B）
        assert out["B"]["false_positive_rate"] is None

    def test_confirmed_wrong_gives_fp_one(self, tmp_path: Path) -> None:
        self._seed(tmp_path, [["A"], ["A"]])
        out = aggregate_readings(tmp_path, confirmed_by_judge={"A": False})
        assert out["A"]["false_positive_rate"] == 1.0

    def test_empty_ledger_gives_no_data_sentinel(self, tmp_path: Path) -> None:
        """★ 无台账 ⇒ **不得**返回 `{}`（D1：`{}` 会被读成"全判据可达"）。

        2026-09-19 D 项修正：旧实现返回 `{}`，其天然读法是「没有任何判据有问题」
        ⇒ 若 M8 据此定档，会把「从未采样」读成「所有判据都可达」，
        正是纪律 #13 最怕的「阈值变摧毁扳机」的前置条件。
        ⇒ 改为返回自曝其缺的哨兵 ``{"__status__": "no_data", "total": 0}``。
        """
        out = aggregate_readings(tmp_path)
        assert out == {"__status__": "no_data", "total": 0}
        assert "__status__" not in {k for k in out if k != "__status__"}


# ============================================================
# 7. 判据 key 稳定性（历史达成率的分组键）
# ============================================================
def test_judge_keys_are_stable() -> None:
    """★ `judge` 是历史达成率统计的**分组键** ⇒ 改名即断历史（必须显式核对）。

    这是纪律 #19 的应用：跨模块共享的**字面量**必须有机器核对。
    本测试钉住已登记判据的全集——新增判据必须同步加到这里（有意设计成
    "加判据要动两处"，强制作者意识到这会影响历史统计口径）。
    """
    judges = set()
    for content in (
        _DUAL_LEVEL, _STAGE_ONLY, _NO_TIER, _STAGE_INCOMPLETE,
        _FLAT_TIERS, _PARTIAL_TIER,
    ):
        judges |= {f.judge for f in judge_plan_granularity(content, name="S")}
        judges |= {f.judge for f in judge_rhythm_peaks(content, name="S")}
    assert judges == {
        "G1.stage_level",
        "G1.chapter_level_absent",
        "G1.tier_level_absent",
        "G1.tier_level_partial",
        "G2.flat_tier_sequence",
    }


def test_semantic_judge_ids_are_documented() -> None:
    """语义判据 id 由提示词产出 ⇒ 必须在 system 提示里登记（语言锚=解析式的两半）。"""
    from agent.core.story.plan_critic import _SEMANTIC_SYSTEM

    for jid in ("S1.motif_advance", "S2.homogeneous", "S3.causal_gap"):
        assert jid in _SEMANTIC_SYSTEM


# ============================================================
# 8. 与 plan_managers 的分工（不重复报障）
# ============================================================
def test_does_not_duplicate_plan_managers(tmp_path: Path) -> None:
    """★ 本模块**不产 BLOCK**、不报弧线衔接/重叠 —— 那归确定性四管理者。

    结构性判据：``plan_managers`` 的能力（BLOCK 级）在本模块的产出里
    **不可能出现**（避免"两个闸报同一件事"污染误报率统计）。
    """
    proj = _proj(tmp_path, {"S01": _DUAL_LEVEL})
    # 故意给重叠弧线：plan_managers 会 BLOCK，本模块应当完全不提
    arcs = [_arc("弧一", 1, 30), _arc("弧二", 20, 50)]
    rep = review_plan(proj, arcs=arcs, current_chapter=0, llm=None)
    text = " ".join(f.message for f in rep.findings)
    assert "重叠" not in text and "衔接" not in text and "空洞" not in text


# ============================================================
# 9. ★ 真实项目样本（纪律 #23：真实项目验证，不造假）
# ============================================================
#: 真实语料目录：``<repo>/agent/tests/x.py`` → parents[2] = ``<repo>``
_REAL = Path(__file__).resolve().parents[2] / "novels"


def test_real_project_linghuang_restart_tier_gap() -> None:
    """真实项目回归：``灵荒薪传-重启/S01`` 有 5 章逐章行、**零档位** ⇒ 必须被抓到。

    ★ 这不是构造样本，是 2026-09-19 真实跑闸取证的结果（M1-Fix 前的存量形态，
      v5 行内档位被长 `验收=` 挤掉的现场）。判据若在此放行，就说明
      「有逐章行但零档位」这一最该被抓的形态被漏掉（纪律 #21：缺口 ⇒ 静默）。
    """
    f = _REAL / "灵荒薪传-重启" / "sublines" / "S01_工坊崛起" / "subline.md"
    if not f.exists():
        pytest.skip("真实项目样本不存在（仅在有语料的仓库跑）")
    content = f.read_text(encoding="utf-8")
    judges = {x.judge for x in judge_plan_granularity(content, name="S01_工坊崛起")}
    assert "G1.tier_level_absent" in judges


def test_real_projects_do_not_write_ledger(tmp_path: Path) -> None:
    """★ 跑闸不得污染真实项目（读-算-写只在临时目录）。"""
    if not _REAL.is_dir():
        pytest.skip("无真实语料")
    # 选一个真实项目，复制其 subline 内容到 tmp，跑 review_plan
    src = next(
        (p for p in sorted(_REAL.iterdir()) if (p / "sublines").is_dir()),
        None,
    )
    if src is None:
        pytest.skip("无真实项目")
    sub = next((s for s in sorted((src / "sublines").iterdir()) if (s / "subline.md").exists()), None)
    if sub is None:
        pytest.skip("无真实支线")
    proj = _proj(tmp_path, {"S01": (sub / "subline.md").read_text(encoding="utf-8")})
    review_plan(proj, arcs=[], current_chapter=0, llm=None)
    assert (proj / CRITIC_LEDGER).exists()
    assert not (src / CRITIC_LEDGER).exists(), "跑闸写入了真实项目（必须只读）"
