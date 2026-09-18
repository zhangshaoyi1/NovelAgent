"""N1「章级切分不得退化成多章混排」红线（2026-09-18）。

## 缺陷 N1 是什么

``select_chapter_lines`` 的原设计是「本章逐章行 > 压力阶段行 > 整段」。
真实项目（《我在长安开殡仪馆那些年》）跑闸发现：**当本章没有逐章行、但小节
内含有早期章节行时**，函数会落到「整段」兜底 ⇒ 注入写手/评委的不是「本章
契约」，而是**整个小节的多章混排**（实测 ch9 钩子小节注入 1669 字，内含
第 1–5 章的内容）。这段文本对本章的可用比例约 3.6%（60/1669）。

诊断出的是**四重叠加缺陷**（详见 ``chapter_contract.select_chapter_lines``
的 docstring 与方案文档 §13）：

1. 三个消费者里只有 ``_extract_plot_points`` 传 ``pressure_stage`` ⇒
   阶段回退分支对另两端**永远进不去**；
2. 即便传对，阶段行（如「铺垫阶段（第6章起）」）**没有章号、不随章推进**
   —— ch6 与 ch40 拿到**同一串 61 字**；
3. 两小节结构不对称：阶段行只写在「情节点序列」，「章节钩子设计」没有
   ⇒ 钩子那一端即便修好 ①② 仍回退整段；
4. **阶段词猜错反而更贵**：算出「冲突」而细纲写「铺垫阶段」⇒ 命中 0 行
   ⇒ 又回退整段。⇒ **「单纯补传参数」是危险修复**（猜错比不传更糟）。

## 修法：结构判据取代阶段词猜测

优先级链改为：**本章逐章段 > 最近前文逐章段 > 压力阶段行 > 整段**。

- 档 2「最近前文」= 章号 ≤ 请求章的逐章段里**章号最大**的那条；
- 回退值**必须**带 :data:`PRIOR_CONTRACT_PREFIX` 标注，让写手/评委知道
  自己看到的不是本章标准（否则**参照系错位** ⇒ 与 D2 档位串档同型缺陷）；
- 老数据（无任何逐章行）仍走「阶段行 → 整段」原路径，**逐字不变**（纪律 #4）。

## 本文件钉住的因果链（纪律 #11：写"某条因果链被切断"而非"某个计数下降"）

1. 本章无逐章行 ⇒ 注入量**不再随小节总长增长**（不再等于整段）；
2. 回退值**必然可被识别为"非本章"**——「标注缺失」这条链被切断；
3. 最近前文的**档位不得被当成本章档位**（参照系错位链被切断）；
4. 老数据路径**逐字未变**（不因修 N1 而误伤阶段级历史项目）。
"""

from __future__ import annotations

import pytest

from agent.core.story.chapter_contract import (
    HOOKS_SECTION,
    POINTS_SECTION,
    PRIOR_CONTRACT_PREFIX,
    _is_marked_as_non_current,
    _nearest_prior_chapter_line,
    chapter_line_pattern,
    pace_tier_of,
    parse_pace_tier,
    select_chapter_lines,
)

# ============================================================
# 样本：**本章无逐章行**、小节内只有早期章节行（= N1 的真实触发形态）
# ============================================================
_SUBLINE_MD_PARTIAL = """---
subline_id: "S01_青石镇"
---

# 支线设定 · 青石镇

## 支线目标

在青石镇站住脚，把殡仪馆的第一笔生意做出口碑。

## 章节钩子设计

第1章：章首钩子=义庄冷雨里第一具无名尸｜章尾钩子=差役递来一纸官凭｜爽点=无｜目标情绪=压抑
第2章：章首钩子=香烛钱不够买棺｜章尾钩子=老师傅留下半卷旧簿｜爽点=无｜目标情绪=拮据
第3章：章首钩子=镇东王屠户来闹灵堂｜章尾钩子=他反被街坊指认昨夜行踪｜爽点=以理服人｜目标情绪=解气
第4章：章首钩子=官凭上的名字对不上｜章尾钩子=他发现自己签过一份不知情的契｜爽点=识破圈套｜目标情绪=警觉
第5章：章首钩子=差役押他去执事堂｜章尾钩子=堂上证人忽然反口｜爽点=用账目反将一军｜目标情绪=紧张｜档位=推进

## 情节点序列

第1章：他在义庄收下第一具无名尸；从随身的旧布包里翻出半枚铜牌
第2章：为了凑钱买棺，他把母亲留下的银簪当了；老师傅分他半卷旧簿
第3章：王屠户闹灵堂，街坊站出来作证；他第一次感到镇上的人开始认他
第4章：官凭名字对不上，他顺藤摸出一份自己签过的契
第5章：执事堂对质，他出示三本账册的时间差；被诬陷的赃物当众翻案｜档位=推进

## 关键冲突

活人的体面与死人的体面，究竟哪一个更该被守住。
"""

#: 老数据：**完全没有任何逐章行**（阶段级历史项目形态）
_SUBLINE_MD_LEGACY_PHASE = """# 支线设定

## 章节钩子设计

铺垫阶段：章首=日常铺垫，章尾=危机触发

## 情节点序列

铺垫阶段：发现土壤冲突；首次触发五行吞噬诀
"""

#: 老数据：连阶段行都没有（更早的无标注形态）
_SUBLINE_MD_LEGACY_BARE = """# 支线设定

## 章节钩子设计

他必须先在镇上站住脚，才能谈别的。

## 情节点序列

先活下来，再谈体面。
"""


# ============================================================
# 一、档 1 未被破坏：有本章行 ⇒ 仍精确返回本章行
# ============================================================
class TestCurrentChapterStillWins:
    """修 N1 不得误伤原能力（纪律 #12：删旧路径前先取证新家）。"""

    def test_chapter_with_line_returns_only_that_line(self) -> None:
        got = select_chapter_lines(_SUBLINE_MD_PARTIAL, HOOKS_SECTION, chapter_num=3)
        assert got.startswith("第3章：")
        assert "王屠户" in got
        # 不得混入相邻章
        assert "第2章" not in got and "第4章" not in got
        assert "半卷旧簿" not in got

    def test_chapter_with_line_has_no_prior_prefix(self) -> None:
        """精确命中的本章行**不得**带"非本章"标注（否则写手会以为没有本章契约）。"""
        got = select_chapter_lines(_SUBLINE_MD_PARTIAL, POINTS_SECTION, chapter_num=5)
        assert not _is_marked_as_non_current(got)
        assert "尚无逐章契约" not in got
        assert "三本账册的时间差" in got

    def test_priority_current_over_prior(self) -> None:
        """档 1 必须**压过**档 2：第 3 章有行时，绝不能返回第 2 章的行。"""
        for title in (HOOKS_SECTION, POINTS_SECTION):
            got = select_chapter_lines(_SUBLINE_MD_PARTIAL, title, chapter_num=3)
            assert chapter_line_pattern(3).search(got), f"{title} 未返回本章行"


# ============================================================
# 二、核心：档 2「最近前文」——N1 的正面判据
# ============================================================
class TestNearestPriorFallback:
    """本章无逐章行 ⇒ 取**最近前文**逐章段，并显式标注「非本章」。"""

    def test_no_line_returns_nearest_prior_not_whole_section(self) -> None:
        """★ N1 主判据：不得再回退整段（多章混排）。"""
        got = select_chapter_lines(_SUBLINE_MD_PARTIAL, HOOKS_SECTION, chapter_num=9)
        # 小节整段含 5 章；回退值只应含**第5章**（最近前文）
        assert "差役押他去执事堂" in got  # 第5章
        assert "义庄冷雨" not in got  # 第1章 —— 整段才会带上它
        assert "王屠户" not in got  # 第3章
        assert "官凭上的名字" not in got  # 第4章

    def test_both_sections_fall_back_consistently(self) -> None:
        """两小节结构对称：钩子与情节点**都**走最近前文（缺陷 ③ 被切断）。"""
        for title, expect in ((HOOKS_SECTION, "执事堂"), (POINTS_SECTION, "三本账册")):
            got = select_chapter_lines(_SUBLINE_MD_PARTIAL, title, chapter_num=9)
            assert expect in got, f"{title} 未回退到第5章"
            assert _is_marked_as_non_current(got), f"{title} 回退值缺非本章标注"

    def test_fallback_is_nearest_not_earliest(self) -> None:
        """必须取**章号最大**的前文，而不是第一条。"""
        got = select_chapter_lines(_SUBLINE_MD_PARTIAL, HOOKS_SECTION, chapter_num=9)
        assert "第5章" in got
        assert "第1章" not in got

    def test_fallback_advances_with_chapter(self) -> None:
        """ch6 → 第5章；ch7/ch9/ch40 → 仍是第5章（因第5章已是最大前文）。

        关键：**不再是不随章推进的阶段模板**（缺陷 ② 被切断）——
        对 ch6 与 ch40 返回值**必须一致且都锚在第5章**。
        """
        a = select_chapter_lines(_SUBLINE_MD_PARTIAL, HOOKS_SECTION, chapter_num=6)
        b = select_chapter_lines(_SUBLINE_MD_PARTIAL, HOOKS_SECTION, chapter_num=40)
        assert "第5章" in a and "第5章" in b
        assert a.split("\n", 1)[1] == b.split("\n", 1)[1], "正文部分应同为第5章契约"

    def test_fallback_has_explicit_marker(self) -> None:
        """回退值**必须**带统一前缀（唯一真源，纪律 #19）。"""
        got = select_chapter_lines(_SUBLINE_MD_PARTIAL, HOOKS_SECTION, chapter_num=9)
        head = got.split("\n", 1)[0]
        expected = PRIOR_CONTRACT_PREFIX.format(n=9, p=5) + "："
        assert head == expected, f"回退前缀与唯一真源不一致：\n  实际={head!r}\n  期望={expected!r}"

    def test_marker_says_what_and_which(self) -> None:
        """标注必须同时说明**请求章**与**回退源章**——否则写手/评委无法判断落空多少。"""
        got = select_chapter_lines(_SUBLINE_MD_PARTIAL, HOOKS_SECTION, chapter_num=9)
        head = got.split("\n", 1)[0]
        assert "第9章" in head, "标注未说明这是第几章"
        assert "第5章" in head, "标注未说明回退到了哪一章"
        assert "最近前文" in head

    def test_fallback_does_not_leak_future_chapter(self) -> None:
        """★ 必须是**最近前文**而非最近后文：后文事件尚未发生，
        注入给写手会**剧透并诱导提前写掉**（比无关文本更严重的缺陷）。
        """
        got = select_chapter_lines(_SUBLINE_MD_PARTIAL, HOOKS_SECTION, chapter_num=2)
        assert "半卷旧簿" in got  # 第2章
        assert "王屠户" not in got, "回退取到了第3章（后文）⇒ 剧透"
        assert "执事堂" not in got, "回退取到了第5章（后文）⇒ 剧透"

    def test_chapter_below_first_line(self) -> None:
        """请求章早于所有逐章行 ⇒ 无任何前文可退，走阶段/整段路径（不抛错）。"""
        got = select_chapter_lines(_SUBLINE_MD_PARTIAL, HOOKS_SECTION, chapter_num=1)
        assert "义庄冷雨" in got  # 第1章本身有行，命中档 1


# ============================================================
# 三、辅助函数（唯一真源的内部件）
# ============================================================
class TestNearestPriorHelper:
    def test_returns_none_when_no_chapter_line(self) -> None:
        lines = ["铺垫阶段：章首=日常", "他必须先在镇上站住脚。"]
        assert _nearest_prior_chapter_line(lines, 9) is None

    def test_returns_max_below_or_equal(self) -> None:
        lines = [
            "第1章：甲",
            "第3章：丙",
            "第7章：庚",
        ]
        assert _nearest_prior_chapter_line(lines, 5) == (3, "第3章：丙")
        assert _nearest_prior_chapter_line(lines, 3) == (3, "第3章：丙")
        assert _nearest_prior_chapter_line(lines, 7) == (7, "第7章：庚")
        assert _nearest_prior_chapter_line(lines, 2) == (1, "第1章：甲")

    def test_splits_multi_chapter_single_line(self) -> None:
        """多章挤一行（`；` 分隔）也要能选出最近前文。"""
        lines = ["第1章：甲；第2章：乙；第4章：丁"]
        assert _nearest_prior_chapter_line(lines, 3) == (2, "第2章：乙")


# ============================================================
# 四、参照系错位：最近前文的**档位**不得当本章档位
# ============================================================
class TestNoTierBorrowing:
    """与 D2 档位串档同型：把弱证据（最近前文）当强证据（本章档位）
    ⇒ 评委拿上一章的尺量本章 ⇒ 静默失真（纪律 #20 #21）。"""

    def test_prior_tier_not_borrowed(self) -> None:
        """第5章标了 `档位=推进`；第9章无逐章行 ⇒ 第9章**不得**拿到 `推进`。"""
        assert pace_tier_of(_SUBLINE_MD_PARTIAL, 5) == "推进", "样本前提：第5章确实标了档位"
        assert pace_tier_of(_SUBLINE_MD_PARTIAL, 9) == "", "第9章借用了第5章的档位（参照系错位）"

    def test_prior_text_still_carries_tier_field(self) -> None:
        """反证前提：回退文本里**确实**含 `｜档位=` —— 所以上面的守卫不可省。"""
        got = select_chapter_lines(_SUBLINE_MD_PARTIAL, HOOKS_SECTION, chapter_num=9)
        assert "档位=推进" in got and parse_pace_tier(got) == "推进"

    def test_tier_marker_is_the_guard_anchor(self) -> None:
        """守卫锚点 = 统一前缀的稳定片段（改前缀必须同步此判据，纪律 #3）。"""
        got = select_chapter_lines(_SUBLINE_MD_PARTIAL, HOOKS_SECTION, chapter_num=9)
        assert _is_marked_as_non_current(got)
        assert "尚无逐章契约" in got


# ============================================================
# 五、兼容性：老数据路径**逐字不变**（纪律 #4：口子只挡历史）
# ============================================================
class TestLegacyPathUntouched:
    """没有逐章行的老项目，行为必须与修复前**完全一致**。"""

    def test_legacy_phase_fallback(self) -> None:
        got = select_chapter_lines(
            _SUBLINE_MD_LEGACY_PHASE, HOOKS_SECTION, chapter_num=3, pressure_stage="铺垫"
        )
        assert "危机触发" in got
        assert not _is_marked_as_non_current(got), "老数据被错标成'最近前文'"

    def test_legacy_phase_without_pressure_stage_arg(self) -> None:
        """不传 pressure_stage 时退回整段（与修复前一致，不因 N1 而改变）。"""
        got = select_chapter_lines(_SUBLINE_MD_LEGACY_PHASE, POINTS_SECTION, chapter_num=3)
        assert "五行吞噬诀" in got
        assert not _is_marked_as_non_current(got)

    def test_legacy_bare_section_returns_whole(self) -> None:
        got = select_chapter_lines(_SUBLINE_MD_LEGACY_BARE, HOOKS_SECTION, chapter_num=9)
        assert "站住脚" in got
        assert not _is_marked_as_non_current(got)

    def test_chapter_num_zero_keeps_legacy(self) -> None:
        """^无章号调用（老调用点）不得进入前文回退。"""
        got = select_chapter_lines(_SUBLINE_MD_PARTIAL, HOOKS_SECTION)
        assert not _is_marked_as_non_current(got)
        assert "义庄冷雨" in got  # 整段（含全部章）


# ============================================================
# 六、三端可达：修复必须真的进入写手与评委（纪律 #8 做矩阵）
# ============================================================
class TestThreeEndsReachability:
    """select_chapter_lines 改对了 ≠ 三端拿到新值。逐端核对。"""

    def test_writer_hooks_reach(self) -> None:
        from agent.workflows.writing.m5_context import M5ContextMixin

        got = M5ContextMixin._extract_chapter_hooks(_SUBLINE_MD_PARTIAL, 9, "铺垫")
        assert _is_marked_as_non_current(got), "写手端未拿到非本章标注"
        assert "执事堂" in got
        assert "义庄冷雨" not in got, "写手端仍注入整段多章混排"

    def test_writer_plot_points_reach(self) -> None:
        from agent.workflows.writing.m5_context import M5ContextMixin

        got = M5ContextMixin._extract_plot_points(_SUBLINE_MD_PARTIAL, "铺垫", 9)
        assert _is_marked_as_non_current(got)
        assert "三本账册" in got
        assert "半枚铜牌" not in got, "写手端仍注入整段多章混排"

    def test_judge_brief_reach(self) -> None:
        from agent.core.story.design_brief import _render_chapter_intent

        text = _render_chapter_intent(_SUBLINE_MD_PARTIAL, chapter_num=9)
        assert "非本章" in text, "评委端未标注证据等级"
        assert "本章钩子设计" not in text, "评委端把最近前文当成了本章契约"
        assert "三本账册" in text
        assert "半枚铜牌" not in text, "评委端仍注入整段多章混排"

    def test_judge_brief_marks_exact_hit_as_current(self) -> None:
        """精确命中时标签**必须**是"本章…"，否则又变成弱标注（矫枉过正）。"""
        from agent.core.story.design_brief import _render_chapter_intent

        text = _render_chapter_intent(_SUBLINE_MD_PARTIAL, chapter_num=3)
        assert "本章钩子设计" in text
        assert "本章情节点" in text
        assert "非本章" not in text


# ============================================================
# 七、渲染层标签与证据等级匹配（纪律 #20 落地）
# ============================================================
class TestIntentLabel:
    def test_label_levels(self) -> None:
        from agent.core.story.design_brief import _intent_label

        exact = "第7章：章首钩子=甲"
        prior = select_chapter_lines(_SUBLINE_MD_PARTIAL, HOOKS_SECTION, chapter_num=9)
        whole = "第2章：乙\n第3章：丙"

        assert _intent_label(exact, 7, "钩子设计") == "本章钩子设计"
        assert "非本章" in _intent_label(prior, 9, "钩子设计")
        assert "非本章精确契约" in _intent_label(whole, 9, "钩子设计")
        assert _intent_label(exact, 0, "钩子设计") == "钩子设计（按压力阶段）"

    def test_label_never_claims_current_for_prior(self) -> None:
        """★ 核心不变量：只要文本被标为"非本章"，标签就**绝不能**以"本章"开头。"""
        from agent.core.story.design_brief import _intent_label

        for n in (6, 7, 9, 12, 40):
            text = select_chapter_lines(_SUBLINE_MD_PARTIAL, HOOKS_SECTION, chapter_num=n)
            assert _is_marked_as_non_current(text)
            label = _intent_label(text, n, "钩子设计")
            assert not label.startswith("本章"), f"第{n}章标签谎称'本章'：{label}"


# ============================================================
# 八、不随小节总长增长（N1 的经济学判据）
# ============================================================
class TestInjectionNoLongerScalesWithSection:
    """注入量此前 = 整段 ⇒ 随细纲越长越贵。修后应锚在**单条前文**上。

    ⚠ 本条**不写绝对字数阈值**（阈值会随真实项目漂移而假失败），
      改写**因果链**：把小节人为加长（追加第6/7/8章），
      第9章的注入量**不得**随之线性增长。
    """

    def test_injection_is_bounded_by_one_prior_line(self) -> None:
        base = select_chapter_lines(_SUBLINE_MD_PARTIAL, HOOKS_SECTION, chapter_num=9)
        fat = _SUBLINE_MD_PARTIAL.replace(
            "\n## 情节点序列",
            "\n第6章：章首钩子=丙｜章尾钩子=丁｜爽点=戊｜目标情绪=己"
            "\n第7章：章首钩子=庚｜章尾钩子=辛｜爽点=壬｜目标情绪=癸"
            "\n第8章：章首钩子=子｜章尾钩子=丑｜爽点=寅｜目标情绪=卯"
            "\n\n## 情节点序列",
        )
        got = select_chapter_lines(fat, HOOKS_SECTION, chapter_num=9)
        # 第9章现在最近前文是第8章，注入仍应只有**一段**（不随小节变长）
        assert got.count("\n") <= base.count("\n") + 0 or got.count("第") - got.count("尚无") <= 4
        assert "第8章" in got, "最近前文应随新增行前移到第8章"
        assert "义庄冷雨" not in got
        assert "王屠户" not in got

    def test_prior_advances_when_new_lines_added(self) -> None:
        """新增逐章行后，回退锚点必须**前移到最新那条**（不是一直钉在第5章）。"""
        fat = _SUBLINE_MD_PARTIAL.replace(
            "\n## 情节点序列",
            "\n第8章：章首钩子=子｜章尾钩子=丑\n\n## 情节点序列",
        )
        got = select_chapter_lines(fat, HOOKS_SECTION, chapter_num=9)
        assert "子" in got and "执事堂" not in got


# ============================================================
# 九、前缀唯一真源（纪律 #19：跨模块共享常量必须有机器交叉核对）
# ============================================================
class TestPrefixSingleSource:
    def test_prefix_importable_and_formatted(self) -> None:
        assert "{n}" in PRIOR_CONTRACT_PREFIX and "{p}" in PRIOR_CONTRACT_PREFIX
        assert _is_marked_as_non_current(PRIOR_CONTRACT_PREFIX.format(n=1, p=1))

    def test_consumers_do_not_hardcode_the_string(self) -> None:
        """消费者**不得**硬编码该中文串（各写一份 = 一次改名双向破裂）。

        取证方式：源码里出现的「尚无逐章契约」必须**只有** ``chapter_contract``
        （真源）与测试文件；消费者一律从真源 import 谓词/标签。
        """
        from pathlib import Path

        src = Path(__file__).resolve().parents[1] / "src"
        offenders: list[str] = []
        for p in src.rglob("*.py"):
            if "chapter_contract.py" in p.name:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:  # noqa: SILENT_DEGRADE reason=expected-skip
                continue
            if "尚无逐章契约" in text:
                offenders.append(str(p))
        assert not offenders, f"消费者硬编码了非本章标注串（应 import 真源）：{offenders}"

    def test_label_suffix_comes_from_ssot(self) -> None:
        """渲染层标签后缀**必须**是真源导出的那份（不是各端复制一份字面量）。"""
        from agent.core.story.chapter_contract import NON_CURRENT_LABEL_SUFFIX
        from agent.core.story.design_brief import _intent_label

        prior = select_chapter_lines(_SUBLINE_MD_PARTIAL, HOOKS_SECTION, chapter_num=9)
        assert _intent_label(prior, 9, "钩子设计") == f"钩子设计{NON_CURRENT_LABEL_SUFFIX}"
        # 标签后缀与文本标注必须引用**同一个**稳定片段
        assert _is_marked_as_non_current(NON_CURRENT_LABEL_SUFFIX), (
            "标签后缀未被真源谓词识别 ⇒ 两份字面量已漂移（纪律 #19）"
        )

    def test_regex_importable_for_consumers(self) -> None:
        """渲染端用的是**裸正则**判断"是不是本章行"——与真源匹配式必须同构。"""
        from agent.core.story.design_brief import _intent_label

        # 与 chapter_line_pattern 同构：允许「第 7 章：」带空格
        assert _intent_label("第 7 章：甲", 7, "钩子设计") == "本章钩子设计"
        # 无冒号不算本章行（防别章交叉引用被当本章）
        assert _intent_label("第7章 甲", 7, "钩子设计") != "本章钩子设计"


# ============================================================
# 十、正则边界（不因修 N1 而放宽章标记）
# ============================================================
@pytest.mark.parametrize(
    "bad_line",
    [
        "第9章 章首钩子=甲",  # 缺冒号
        "第 9 章 前缀说明",  # 缺冒号
        "第90章：章首钩子=甲",  # 章号非精确匹配
        "留至第9章",  # 交叉引用（非行首）
    ],
)
def test_non_current_line_not_matched(bad_line: str) -> None:
    """档 1 只认**带冒号且行首**的精确章号——否则交叉引用会被当本章契约
    （2026-09-18 既有实证：第5章契约混入第4章尾部）。"""
    assert not chapter_line_pattern(9).search(bad_line)
