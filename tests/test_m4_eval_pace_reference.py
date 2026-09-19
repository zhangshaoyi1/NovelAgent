"""红线：质检侧「强度档位参照系」（M4，2026-09-18）——「按规划判」的评委半边。

命题（用户 2026-09-18）
----------------------
> 「整体有起伏，单章可以放松……甚至可以出现部分注水的，但是不影响质量」
> 「比如规划描述的是连续五章都是平平淡淡的，那么校验就不会把它当作注水打回」

M1–M3 已完成**供给侧**（规划产出档位 → 三端装配 → 写手侧平权规则分叉）。
本文件钉的是**质检侧**（M4）：评委拿到档位、并按档位判。

为什么必须「窗口逐章」而不是「本章一档」（★ 本轮最贵发现）
----------------------------------------------------------
评委一次判 ``eval_window``（默认 5）章，而 ``pace_tier_of`` 只给**本章**一档
⇒ 供给粒度（1 章）≠ 消费粒度（N 章）⇒ 评委拿**最后一章**的尺量整窗
（纪律 #15「章级槽位由阶段级文本供给＝没供给」同型）。
用户命题里的「连续五章平平淡淡」若只给第 5 章的档位，其余四章**仍然**
被高强度尺判注水 ⇒ 回退重写且**输入不变** ⇒ 死循环原样复现。

三块内容
--------
===================  =======================================================
改造                 红线
===================  =======================================================
窗口逐章档位块       ``DesignBrief.window_pace_tiers`` → 只进评委端
强度维判定前提       ``DESIGN_EXEMPTION_PACE``（**仅在有档位时**追加）
提示词参照系条款     ``quality.reader_appeal_eval`` 的 system 段第 4 条
===================  =======================================================

★ 纪律 #25：本文件每条断言都落在**真被渲染**的对象上——
``render_for_judge`` 的唯一生产消费者是 ``reader_appeal._design_facts``（进
user 段），``reader_appeal_eval.md`` 的 system 段由 ``reader_appeal.score``
渲染（已取证），不是改了没用的死文件。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from agent.core.story import chapter_contract as cc
from agent.core.story.design_brief import (
    DESIGN_EXEMPTION,
    DESIGN_EXEMPTION_PACE,
    build_design_brief,
)

_SRC = Path(__file__).resolve().parents[1] / "src" / "agent"

#: 「放松档名」——**只有这类名字**被点名才会造成「哪些档算放松」的第二份真源。
#:
#: ⚠ 为什么不全量查 ``PACE_TIER_NAMES``：``推进``/``高潮`` 同时是常用中文词
#:   （"阅读推进""剧情推进""高潮迭起"），朴素子串检测会误伤正常措辞
#:   ⇒ 用"数值相等/子串"做判据正是纪律 #19 警告的反例。
#:   真正会分裂的是**放松档名单**（它决定谁免检），故只查它。
RELAXED_TIER_NAMES: tuple[str, ...] = tuple(t.name for t in cc.PACE_TIERS if t.relaxed)

#: 改造前快照（老数据：细纲无逐章档位）——由 ``.workbuddy/diag/m4_snapshot.py``
#: 在 ``pre-m4-pace-ref-20260918`` 上采样生成，**禁手抄**（纪律 #23）。
JUDGE_BEFORE = "【设定真源·境界体系（冻结）】杂役 → 引灵 → 栖气 → 淳真 → 开玄 → 铸府 → 凝曜 → 归一\n\n【设定真源·金手指登记】五行吞噬诀：吞噬灵力转化为催化能源，用于流水线生产。\n\n【角色真源（含设计意图：内核/弧光/关系演变；判定人设/设定时以此为准，但**沿此处登记的弧光推进不算崩坏**）】\n- **林凡**：基础：身份：穿越者，天灵宗灵植杂役；境界：杂役→归一期\n  内核：- **核心动机**：改变修仙界资源掠夺模式\n- **弧光**：\n  - 起始状态：隐忍求存的底层杂役\n  - 终结状态：退居幕后的文明守护者\n  关系（演变方向）：['- 与沈长风：合作→导师', '- 与楚寒烟：从排斥到认可']\n  语言指纹：**口头禅**：这不是命，是算式。\n\n【角色弧线轨迹（设计轨，非漂移）】\n弧线按下列档位**有序推进**；本窗口处于标注档。\n- N01（1-150） 里程碑：杂役觉醒 ← **本窗口在此档**\n    主线：五行吞噬诀解封\n    成长：境界：杂役→引灵→栖气；心性：隐忍→果敢；能力：五行吞噬诀Lv.1\n- N02（151-300） 里程碑：外门立足（后续）\n    主线：工坊品牌建立\n    成长：境界：开玄初期→开玄后期；心性：务实→自信\n【本窗口章级设计意图】\n- 支线目标：从灵植杂役晋升外门弟子，建立第一条人工作坊流水线。\n- 主线方向：五行吞噬诀解封｜结果预期：灵田产量翻倍\n- 钩子设计（按压力阶段/整段，非本章精确契约）：铺垫章：章首=日常铺垫，章尾=危机触发；冲突章：章尾=两难抉择\n- 情节点（按压力阶段/整段，非本章精确契约）：首次触发五行吞噬诀；打造第一具粗劣傀儡；建立人工作坊。\n\n【本作达标判据（判定时对标，勿自设更严口径）】\n合格线 = **无实质硬伤、可直接连载**（不是「惊艳」）。硬指标不达标会触发整窗回退重写；软维度不达标只告警。\n- 人设稳定：0 条（人设稳定性（角色言行/动机是否与角色档案、**弧光轨迹**冲突——沿弧光登记轨迹的有序推进属设计内成长，**不算矛盾**；仅倒退/跳档/无契机/与档案直接冲突才计；逐项列举崩坏处数量。**value 只填整数条数（无硬伤填 0），禁止填 0–100 分数/百分比**）） —— **硬指标**\n- 设定一致：0 条（设定一致性（境界/金手指/世界观规则是否被打破——以**设定台账＋设计意图**为准，设计轨内允许的变化不算打破；逐项列举冲突数量。**value 只填整数条数（无冲突填 0），禁止填 0–100 分数/百分比**）） —— **硬指标**\n- 连贯性：≥ 85 分（连贯性（章节衔接/叙事流畅度，0-100 评分））\n- 追读力：≥ 80 分（追读力/可读性（让人想继续读的欲望，0-100 评分））\n\n【判定前提·设计内转变 ≠ 崩坏】以下为规划端登记的设计轨（含路线节点成长、角色弧光、关系演变、章级设计意图）。本窗口内角色性格/境界/能力/关系若沿该轨迹**有序推进**（顺序一致、有可指认的触发事件、落在登记的章区间内），属**设计内变化**，不计人设崩坏 / 设定冲突 / 逻辑漏洞；仅当变化**超出**登记轨迹（倒退、跳档、无契机、与设定台账直接冲突）才计 issue。判 issue 前先确认它**不是**设计轨里已登记的内容。"

WRITER_BEFORE = '【本章设计意图（规划端已登记，本章须落实；不得自行改道）】\n- 支线目标：从灵植杂役晋升外门弟子，建立第一条人工作坊流水线。\n- 主线方向：五行吞噬诀解封｜结果预期：灵田产量翻倍\n- 钩子设计（按压力阶段/整段，非本章精确契约）：铺垫章：章首=日常铺垫，章尾=危机触发；冲突章：章尾=两难抉择\n- 情节点（按压力阶段/整段，非本章精确契约）：首次触发五行吞噬诀；打造第一具粗劣傀儡；建立人工作坊。\n\n【角色弧线轨迹（**设计内转变 ≠ 人设崩坏**：按此轨迹的推进是设计要求，不是前后不一致）】\n弧线按下列档位**有序推进**；本窗口处于标注档。\n- N01（1-150） 里程碑：杂役觉醒 ← **本窗口在此档**\n    主线：五行吞噬诀解封\n    成长：境界：杂役→引灵→栖气；心性：隐忍→果敢；能力：五行吞噬诀Lv.1\n- N02（151-300） 里程碑：外门立足（后续）\n    主线：工坊品牌建立\n    成长：境界：开玄初期→开玄后期；心性：务实→自信\n\n【达标判据（批末体检口径；写作时按此自检，避免写完被打回）】\n合格线 = **无实质硬伤、可直接连载**（不是「惊艳」）。硬指标不达标会触发整窗回退重写；软维度不达标只告警。\n- 人设稳定：0 条（人设稳定性（角色言行/动机是否与角色档案、**弧光轨迹**冲突——沿弧光登记轨迹的有序推进属设计内成长，**不算矛盾**；仅倒退/跳档/无契机/与档案直接冲突才计；逐项列举崩坏处数量。**value 只填整数条数（无硬伤填 0），禁止填 0–100 分数/百分比**）） —— **硬指标**\n- 设定一致：0 条（设定一致性（境界/金手指/世界观规则是否被打破——以**设定台账＋设计意图**为准，设计轨内允许的变化不算打破；逐项列举冲突数量。**value 只填整数条数（无冲突填 0），禁止填 0–100 分数/百分比**）） —— **硬指标**\n- 连贯性：≥ 85 分（连贯性（章节衔接/叙事流畅度，0-100 评分））\n- 追读力：≥ 80 分（追读力/可读性（让人想继续读的欲望，0-100 评分））'


# ============================================================
# 夹具：与快照脚本逐字同源（否则 BEFORE/AFTER 的差异来自夹具而非改造）
# ============================================================
_WORLD_MD = """# 灵荒薪传

## 故事简介

林凡穿越到灵荒大陆，以生产力革命冲击修仙界的资源掠夺模式。

## 修炼境界体系

杂役 → 引灵 → 栖气 → 淳真 → 开玄 → 铸府 → 凝曜 → 归一

## 金手指登记

五行吞噬诀：吞噬灵力转化为催化能源，用于流水线生产。
"""

_PLAN = {
    "quality_targets": {
        "character_stability_high": 0,
        "setting_consistency_high": 0,
        "coherence": 85.0,
        "readability": 80.0,
    },
    "route": {
        "nodes": [
            {
                "id": "N01",
                "chapter_range": "1-150",
                "milestone": "杂役觉醒",
                "main_branch": {
                    "title": "五行吞噬诀解封",
                    "result": "灵田产量翻倍",
                    "growth": "境界：杂役→引灵→栖气；心性：隐忍→果敢；能力：五行吞噬诀Lv.1",
                },
            },
            {
                "id": "N02",
                "chapter_range": "151-300",
                "milestone": "外门立足",
                "main_branch": {
                    "title": "工坊品牌建立",
                    "result": "达成规模化生产",
                    "growth": "境界：开玄初期→开玄后期；心性：务实→自信",
                },
            },
        ]
    },
}

#: 老数据形态：钩子/情节点均为**阶段级**文本，无逐章行、更无档位。
_OLD_SUBLINE_MD = """---
subline_id: "S01_工坊崛起"
---

# 支线设定 · 工坊崛起

## 支线目标

从灵植杂役晋升外门弟子，建立第一条人工作坊流水线。

## 章节钩子设计

铺垫章：章首=日常铺垫，章尾=危机触发；冲突章：章尾=两难抉择

## 情节点序列

首次触发五行吞噬诀；打造第一具粗劣傀儡；建立人工作坊。
"""

#: 新数据形态：逐章行带 ``档位=``（ch6 推进 / ch7 垫片 / ch8 高潮 / ch9 未标）
_NEW_SUBLINE_MD = """---
subline_id: "S01_工坊崛起"
---

# 支线设定 · 工坊崛起

## 支线目标

从灵植杂役晋升外门弟子，建立第一条人工作坊流水线。

## 章节钩子设计

第6章：章首=余波｜章尾=新线索｜档位=推进｜爽点=无｜目标情绪=紧绷
第7章：章首=日常｜章尾=小疑问｜档位=垫片｜爽点=无｜目标情绪=舒缓
第8章：章首=对峙｜章尾=绝境｜档位=高潮｜爽点=反杀｜目标情绪=燃
第9章：章首=收拾｜章尾=去向｜爽点=无｜目标情绪=平缓

## 情节点序列

第6章：清点战利品
第7章：安置伤员，与女儿吃饭
第8章：与长老摊牌
第9章：启程前往外门
"""

_CHARACTER_MD = """---
name: "林凡"
role: "protagonist"
realm: "杂役→归一期"
---

# 角色档案 · 林凡

## 基础

- 身份：穿越者，天灵宗灵植杂役
- 境界：杂役→归一期

## 内核

- **核心动机**：改变修仙界资源掠夺模式
- **弧光**：
  - 起始状态：隐忍求存的底层杂役
  - 终结状态：退居幕后的文明守护者

## 关系

['- 与沈长风：合作→导师', '- 与楚寒烟：从排斥到认可']

## 语言指纹

- **口头禅**：这不是命，是算式。
"""


def _mk_project(root: Path, subline: str, chapter: int = 27) -> Path:
    (root / "world.md").write_text(_WORLD_MD, encoding="utf-8")
    (root / "characters").mkdir(exist_ok=True)
    (root / "characters" / "林凡.md").write_text(_CHARACTER_MD, encoding="utf-8")
    d = root / "sublines" / "S01_工坊崛起"
    d.mkdir(parents=True, exist_ok=True)
    (d / "subline.md").write_text(subline, encoding="utf-8")
    (root / ".state").mkdir(exist_ok=True)
    (root / ".state" / "plan.json").write_text(
        json.dumps(_PLAN, ensure_ascii=False), encoding="utf-8"
    )
    (root / "chapters").mkdir(exist_ok=True)
    (root / "chapters" / f"ch{chapter:03d}.md").write_text(
        f"第{chapter}章 试炼\n\n林凡睁开眼。", encoding="utf-8"
    )
    return root


@pytest.fixture()
def legacy(tmp_path: Path) -> Path:
    """老数据项目（细纲无逐章档位）。"""
    return _mk_project(tmp_path, _OLD_SUBLINE_MD, chapter=27)


@pytest.fixture()
def with_tiers(tmp_path: Path) -> Path:
    """新数据项目：ch6 推进 / ch7 垫片 / ch8 高潮 / ch9 未标。"""
    return _mk_project(tmp_path, _NEW_SUBLINE_MD, chapter=9)


# ============================================================
# 1) 老数据：逐字等同改造前（纪律 #4 —— 新机制只对新数据生效）
# ============================================================
class TestLegacyIsByteIdentical:
    def test_judge_render_is_unchanged(self, legacy: Path) -> None:
        """无档位 ⇒ 评委端文本**逐字不变**（不放松也不收紧）。"""
        text = build_design_brief(legacy, eval_window=5).render_for_judge()
        assert text == JUDGE_BEFORE

    def test_writer_render_is_unchanged(self, legacy: Path) -> None:
        text = build_design_brief(legacy, eval_window=5).render_for_writer()
        assert text == WRITER_BEFORE

    def test_window_tier_block_is_empty(self, legacy: Path) -> None:
        brief = build_design_brief(legacy, eval_window=5)
        assert brief.window_pace_tiers == ""
        assert "本窗口各章强度档位" not in brief.render_for_judge()

    def test_pace_exemption_absent_without_tiers(self, legacy: Path) -> None:
        """★ 强度维前提**不得**无条件追加：老数据没有档位就该没有这条前提。

        若无条件追加，评委会在「根本没给档位」的情况下被要求"按档位判"
        ⇒ 参照系悬空 ⇒ 只能靠猜（纪律 #22：猜错比不猜更贵）。
        """
        text = build_design_brief(legacy, eval_window=5).render_for_judge()
        assert DESIGN_EXEMPTION in text, "原判定前提必须仍在"
        assert DESIGN_EXEMPTION_PACE not in text


# ============================================================
# 2) 新数据：窗口**逐章**档位参照系
# ============================================================
class TestWindowTierReference:
    def test_block_present_for_tiered_window(self, with_tiers: Path) -> None:
        brief = build_design_brief(with_tiers, eval_window=5)
        assert brief.window_pace_tiers, "有登记档位就必须渲染参照系块"
        assert brief.window_pace_tiers in brief.render_for_judge()

    def test_every_tiered_chapter_appears(self, with_tiers: Path) -> None:
        """★ 核心断言：窗口内**每一章**有档位 ⇒ 块里必须逐章出现。

        只给最后一章 = 供给粒度 ≠ 消费粒度（纪律 #15）⇒ 其余章仍被高强度尺判。
        """
        brief = build_design_brief(with_tiers, eval_window=5)
        lo, hi = brief.window
        for n in range(lo, hi + 1):
            tier = cc.pace_tier_of(_NEW_SUBLINE_MD, n)
            if tier:
                assert f"第{n}章" in brief.window_pace_tiers, (
                    f"ch{n} 登记了档位 {tier} 却没进参照系块 ⇒ 该章仍会被通用尺判"
                )

    def test_untiered_chapter_is_omitted(self, with_tiers: Path) -> None:
        """未标档位的章**不得**出现（编造档位 = 更严重的参照系错位）。"""
        brief = build_design_brief(with_tiers, eval_window=5)
        assert "第9章" not in brief.window_pace_tiers
        assert "第5章" not in brief.window_pace_tiers

    def test_relaxed_chapters_are_marked(self, with_tiers: Path) -> None:
        """放松章必须被**正名**（否则评委仍按高强度尺判它注水 ⇒ 死循环）。

        标记由 ``PaceTier.relaxed`` 派生，不是写死的名单。
        """
        brief = build_design_brief(with_tiers, eval_window=5)
        block = brief.window_pace_tiers
        assert cc.PACE_TIER_BY_NAME["垫片"].relaxed
        assert "第7章" in block and "放松章" in block

    def test_block_warns_against_single_ruler(self, with_tiers: Path) -> None:
        """块必须显式禁止「整窗套用同一把尺」——否则评委仍会取一档量全窗。"""
        assert "不得整窗套用同一把尺" in build_design_brief(
            with_tiers, eval_window=5
        ).window_pace_tiers

    def test_block_is_bounded(self, with_tiers: Path) -> None:
        brief = build_design_brief(with_tiers, eval_window=5)
        assert len(brief.window_pace_tiers) <= 1200 + 32

    def test_wider_window_covers_more_chapters(self, with_tiers: Path) -> None:
        """窗口放大 ⇒ 覆盖章数随之增加（参照系随消费粒度走）。"""
        narrow = build_design_brief(with_tiers, eval_window=2).window_pace_tiers
        wide = build_design_brief(with_tiers, eval_window=5).window_pace_tiers
        assert "第6章" not in narrow
        assert "第6章" in wide


# ============================================================
# 3) 作用域：只进评委端（纪律 #18 —— 供给必须定义作用域）
# ============================================================
class TestScopeIsJudgeOnly:
    def test_writer_does_not_get_window_tiers(self, with_tiers: Path) -> None:
        """写手一次只写一章：给整窗档位既无用又剧透后文节奏。"""
        text = build_design_brief(with_tiers, eval_window=5).render_for_writer()
        assert "本窗口各章强度档位" not in text

    def test_persist_does_not_get_window_tiers(self, with_tiers: Path) -> None:
        text = build_design_brief(with_tiers, eval_window=5).render_for_persist()
        assert "本窗口各章强度档位" not in text

    def test_writer_still_gets_current_chapter_tier(self, with_tiers: Path) -> None:
        """本章档位（M1 落点）不得因本次改造而丢失——写手侧仍需它分叉平权规则。"""
        brief = build_design_brief(with_tiers, eval_window=5, chapter_num=7)
        assert brief.pace_tier == "垫片"
        assert brief.pace_relaxed is True


# ============================================================
# 4) 强度维判定前提
# ============================================================
class TestPaceExemption:
    def test_appended_when_tiers_exist(self, with_tiers: Path) -> None:
        text = build_design_brief(with_tiers, eval_window=5).render_for_judge()
        assert DESIGN_EXEMPTION in text
        assert DESIGN_EXEMPTION_PACE in text

    def test_says_exemption_is_about_deviation(self) -> None:
        """必须是「**偏离**登记档位才计缺陷」，不是"放松章一律免检"。

        写成后者 = 放松档彻底失守（漏报上升），与「不是放宽阈值、是换参照系」
        的设计约束相悖。
        """
        assert "偏离" in DESIGN_EXEMPTION_PACE
        assert "不放松也不收紧" in DESIGN_EXEMPTION_PACE

    def test_does_not_name_any_relaxed_tier(self) -> None:
        """★ 前提文案**不得**点名放松档：哪些档算放松由档位表派生。

        提示词里抄一份放松档名单 ⇒ 一次改名即双向破裂（纪律 #19）。
        """
        for name in RELAXED_TIER_NAMES:
            assert name not in DESIGN_EXEMPTION_PACE, (
                f"强度维前提点名了放松档「{name}」——放松与否必须由 PaceTier.relaxed 派生"
            )


# ============================================================
# 5) 单一真源：档位只由 chapter_contract 定义（纪律 #19）
# ============================================================
def _string_constants(rel: str) -> list[str]:
    """取模块内所有**字符串常量**（含 f-string 的常量片段；注释与标识符不算）。"""
    src = (_SRC / rel).read_text(encoding="utf-8")
    tree = ast.parse(src)
    return [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]


class TestSingleSourceOfTiers:
    def test_renderer_has_no_relaxed_tier_literals(self) -> None:
        """渲染端不得出现**放松档名**字面量（放松与否的唯一真源是档位表）。"""
        for const in _string_constants("core/story/design_brief.py"):
            for name in RELAXED_TIER_NAMES:
                assert name not in const, (
                    f"design_brief.py 出现了放松档名「{name}」：{const[:40]}……"
                    "放松与否只能由 chapter_contract.PaceTier.relaxed 派生"
                )

    def test_relaxed_marks_are_derived_not_hardcoded(self) -> None:
        """★ 派生关系判据（纪律 #19 推荐形态）：放松标记必须读 ``.relaxed``。

        只查"有没有出现档位名"是**数值相等**型判据，会被「阅读推进」这类
        正常措辞绕过/误伤；查"是否从真源派生"才是**成员/派生关系**判据。
        """
        src = (_SRC / "core/story/design_brief.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        for fn_name in ("_render_window_pace_tiers", "_render_chapter_intent"):
            func = next(
                n
                for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == fn_name
            )
            attrs = {
                n.attr
                for n in ast.walk(func)
                if isinstance(n, ast.Attribute)
            }
            assert "relaxed" in attrs, (
                f"{fn_name} 未从 PaceTier.relaxed 派生放松标记"
                "——硬编码名单会在档位表改名时双向破裂"
            )

    def test_relaxed_tier_names_come_from_the_table(self) -> None:
        """红线自身也不得写死名单：放松档名必须从档位表派生。"""
        assert RELAXED_TIER_NAMES
        for name in RELAXED_TIER_NAMES:
            assert cc.PACE_TIER_BY_NAME[name].relaxed is True

    def test_window_helper_derives_from_pace_tier_of(self) -> None:
        """窗口档位必须与本章档位**同源**（同一解析式），不得另写一套匹配。"""
        src = (_SRC / "core/story/chapter_contract.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        func = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "pace_tiers_of_window"
        )
        calls = {
            n.func.id
            for n in ast.walk(func)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "pace_tier_of" in calls, "窗口档位必须复用 pace_tier_of（同源解析）"

    def test_rendered_block_matches_pace_tier_of(self, with_tiers: Path) -> None:
        """★ 机器交叉核对：块里出现的章 == ``pace_tier_of`` 非空的章。"""
        brief = build_design_brief(with_tiers, eval_window=5)
        lo, hi = brief.window
        expected = {
            n for n in range(lo, hi + 1) if cc.pace_tier_of(_NEW_SUBLINE_MD, n)
        }
        rendered = {
            int(num)
            for num in __import__("re").findall(r"- 第(\d+)章", brief.window_pace_tiers)
        }
        assert rendered == expected, (
            f"渲染出的章 {sorted(rendered)} 与档位真源 {sorted(expected)} 不一致"
        )


# ============================================================
# 6) 提示词：质检侧真的有「按档位判」这条（纪律 #25 —— 该段真被渲染）
# ============================================================
class TestJudgePromptCarriesReference:
    @staticmethod
    def _system() -> str:
        from agent.core.infra.prompt_manager import pm

        return pm.get("quality.reader_appeal_eval").system

    def test_system_mentions_pace_tier(self) -> None:
        """system 段必须出现「强度档位」——否则档位块只是被看到、没有被要求照做。"""
        assert "强度档位" in self._system()

    def test_system_disambiguates_from_score_tier(self) -> None:
        """★ 标尺里已有「档位」（分数档）一词，必须显式声明二者不是同一件事。

        同一词两种语义 ⇒ 评委把"分数档"读成"强度档"（语义混淆 = 判据失真）。
        """
        sys_txt = self._system()
        assert "档位" in sys_txt
        assert "不是同一件事" in sys_txt

    def test_system_does_not_name_relaxed_tiers(self) -> None:
        """质检提示词不得抄一份放松档名单（名单只能住在档位表）。"""
        for name in RELAXED_TIER_NAMES:
            assert name not in self._system(), (
                f"质检提示词点名了放松档「{name}」——放松档名单只能有一份"
            )

    def test_system_says_untiered_falls_back_to_original_ruler(self) -> None:
        """未给档位 ⇒ 回到原标尺（不放松也不收紧）——保住历史路径零改动。"""
        assert "不放松也不收紧" in self._system()


# ============================================================
# 7) 端到端：评委**真的**拿到参照系（行为级，不只断言装配）
# ============================================================
class TestJudgeActuallyReceivesIt:
    @pytest.mark.parametrize(
        "dimension",
        ["coherence", "readability", "logic_holes", "character_stability_high"],
    )
    def test_every_dimension_sees_window_tiers(
        self, with_tiers: Path, dimension: str
    ) -> None:
        """★ 纪律 #10：断言**内容真出现在送给 LLM 的文本里**，不只断言装配成功。"""
        from agent.core.quality.scoring.reader_appeal import ReaderAppealScorer

        scorer = ReaderAppealScorer(llm_client=None, eval_window=5)
        text = scorer._gather_for_eval(dimension, str(with_tiers))
        assert "本窗口各章强度档位" in text, f"{dimension} 未拿到档位参照系"
        assert "第7章" in text, f"{dimension} 未拿到窗口内逐章档位"
        assert DESIGN_EXEMPTION_PACE in text, f"{dimension} 未拿到强度维判定前提"

    def test_legacy_project_is_untouched_end_to_end(self, legacy: Path) -> None:
        """老数据端到端：评委文本不含任何档位参照系（逐字回到旧行为）。"""
        from agent.core.quality.scoring.reader_appeal import ReaderAppealScorer

        scorer = ReaderAppealScorer(llm_client=None, eval_window=5)
        text = scorer._gather_for_eval("readability", str(legacy))
        assert "本窗口各章强度档位" not in text
        assert DESIGN_EXEMPTION_PACE not in text
        assert DESIGN_EXEMPTION in text
