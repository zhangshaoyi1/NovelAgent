"""M1 红线草稿：强度档位 SSOT 与三端可达（2026-09-18）。

命题（用户 2026-09-18）：
> 「整体有起伏，单章可以放松……甚至可以出现部分注水的，但是不影响质量」
> 「比如规划描述的是连续五章都是平平淡淡的，那么校验就不会把它当作注水打回」

被切断的因果链（纪律 #11：验收写成"某条因果链被切断"）
------------------------------------------------------
1. 规划产出档位 ⇒ **写手拿到本章档位**（不再只有一把"高潮尺"）；
2. 规划产出档位 ⇒ **评委拿到本章档位**（据此选尺）；
3. 档位词表在**提示词 / 契约真源 / 渲染端**三处同名同形
   （纪律 #19：跨模块共享常量必须有机器交叉核对）；
4. **未标档位 ⇒ 行为不变**（纪律 #4：口子只挡历史、不影响当前）。

★ 与既有红线的分工
-------------------
- `test_contract_labels_not_leaked.py:108` 已做「提示词字段名 ⊆ CONTRACT_FIELDS」核对
  ⇒ 本文件**不重复**，只验证档位真的进了该核对范围；
- `test_plan_consistency_chapter_level.py:229` 断言「窗口全覆盖 ⇒ 零告警」
  ⇒ 本文件断言档位覆盖率走**独立函数**，不得污染该告警流。
"""

from __future__ import annotations

import re
from pathlib import Path

import agent
import pytest

from agent.core.story.chapter_contract import (
    CONTRACT_ANNOTATION_RE,
    CONTRACT_FIELDS,
    HOOKS_SECTION,
    POINTS_SECTION,
    PACE_TIER_NAMES,
    PACE_TIERS,
    parse_pace_tier,
    pace_tier_of,
)

_PROMPTS = Path(agent.__file__).parent / "prompts"


def _prompt_text(name: str) -> str:
    from agent.core.infra.prompt_manager import pm

    pd = pm.get(name)
    chunks = [
        v
        for attr in ("system", "user", "raw", "body", "content", "text", "source")
        if isinstance(v := getattr(pd, attr, None), str)
    ]
    assert chunks, f"无法从 PromptDef 读取文本：{pd!r}"
    return "\n".join(chunks)


# ============================================================
# 一、档位 SSOT 自洽
# ============================================================
class TestPaceTierSSOT:
    def test_tiers_are_four_and_unique(self) -> None:
        assert len(PACE_TIERS) == 4, [t.name for t in PACE_TIERS]
        assert len(set(PACE_TIER_NAMES)) == 4, "档位名重复"

    def test_tier_bands_are_ordered_and_disjoint(self) -> None:
        """张力带必须有序且不重叠 —— 否则"本章该多燃"没有唯一答案。"""
        bands = sorted((t.tension_lo, t.tension_hi, t.name) for t in PACE_TIERS)
        for (_, hi_a, name_a), (lo_b, _, name_b) in zip(bands, bands[1:]):
            assert hi_a < lo_b, f"{name_a} 与 {name_b} 的张力带重叠"

    def test_relaxed_tiers_are_the_low_ones(self) -> None:
        """放松档必须正好是低张力两档 —— 高张力章不允许放松（否则爆点可省）。"""
        relaxed = {t.name for t in PACE_TIERS if t.relaxed}
        assert relaxed == {"垫片", "日常"}, relaxed
        for t in PACE_TIERS:
            if t.name in ("高潮", "推进"):
                assert not t.relaxed, f"{t.name} 不应被标为放松档"


# ============================================================
# 二、与提示词的语言锚对齐（纪律 #3 / #19）
# ============================================================
class TestPromptAnchorBinding:
    def test_prompt_declares_tier_field(self) -> None:
        """提示词必须声明 `档位=` 字段 —— 否则规划侧不会产出。"""
        text = _prompt_text("m3.outline")
        assert "档位=" in text, "提示词未要求章级强度档位"

    def test_prompt_tier_words_are_members_of_ssot(self) -> None:
        """★ 成员关系（非"数值相等"）：提示词里的档位取值必须都能在 SSOT 里找到。

        拦什么：提示词写「缓冲」而代码只认「垫片」⇒ 一次改名即双向破裂（纪律 #19）。

        判据形态（**精确，不靠宽松匹配**）：
          ``档位=X`` 的 X 只可能是三种：
            ① 单个档位词（``推进``）；
            ② 多个档位词用 ``/`` 并列（``垫片/日常``）；
            ③ **占位符**（``<四档之一>``，格式模板专用）。
          把 X 按 ``/`` 拆开后，每个元素要么精确等于登记档位名，要么是占位符形态。
        这样 ``档位=垫片/日常`` 通过、``档位=<四档之一>`` 通过，而 ``档位=缓冲`` 被拦。

        ⚠ 不靠"包含"匹配放宽：放宽会让真错词（``缓冲``）漏网。
        """
        text = _prompt_text("m3.outline")
        # 1) 剥 Markdown 行内装饰，避免反引号/星号混进取值
        clean = re.sub(r"[`*_]", "", text)
        # 2) 取 ``档位=X`` 的 X：止于空白/竖线/中英文标点/括号
        raw = re.findall(r"档位\s*[=:：]\s*([^\s｜|，,、。；;）)：:（(]+)", clean)
        # 3) X 允许 ``/`` 并列，逐个元素精确比对；占位符形态除外
        cands = {w for tok in raw for w in tok.split("/") if w}
        unknown = {
            w for w in cands
            if w not in PACE_TIER_NAMES and not (w.startswith("<") and w.endswith(">"))
        }
        assert not unknown, (
            f"提示词出现未登记的档位词 {sorted(unknown)} —— "
            f"必须在 chapter_contract.PACE_TIERS 登记，否则解析端不认"
        )

    def test_every_tier_is_documented_in_prompt(self) -> None:
        """每个档位都必须被提示词说明（否则规划者不知道还有这一档）。"""
        text = _prompt_text("m3.outline")
        for name in PACE_TIER_NAMES:
            assert name in text, f"提示词未说明档位「{name}」"

    def test_prompt_requires_batch_rolling_coverage(self) -> None:
        """★ 分批滚动：提示词不得再只要求"前 20 章"（那是 #21 静默失真的根因）。"""
        text = _prompt_text("m3.outline")
        assert "批次" in text, "提示词未要求分批滚动覆盖"


# ============================================================
# 三、解析与切分（粒度必须与 select_chapter_lines 一致）
# ============================================================
_TIER_LINE_7 = (
    "第7章：章首钩子=执事堂传唤｜章尾钩子=赃物被塞进铺位｜爽点=用账目反将一军"
    "｜目标情绪=紧张｜在场=林凡，周管事｜禁=不得出现外门长老｜验收=读者能说出三本账"
    "｜档位=推进"
)
_SUBLINE_WITH_TIER = f"""# 支线设定

## 章节钩子设计

{_TIER_LINE_7}
第8章：章首钩子=对决前夜｜档位=高潮

## 情节点序列

第7章：执事堂对质，出示三本账册；被诬陷的赃物当众翻案
"""


class TestTierParsing:
    def test_parse_from_line(self) -> None:
        assert parse_pace_tier(_TIER_LINE_7) == "推进"

    def test_parse_tolerant_of_separators(self) -> None:
        """容忍 `=`/`:`/`：` 与全角/半角竖线（LLM 对分隔符不稳定）。"""
        for line in (
            "第7章：a=1｜档位=推进",
            "第7章：a=1|档位=推进",
            "第7章：a=1｜档位:推进",
            "第7章：a=1｜档位：推进",
        ):
            assert parse_pace_tier(line) == "推进", line

    def test_unknown_value_returns_empty(self) -> None:
        """未登记档位词 ⇒ 空串（下游按"未标档位"处理，不猜）。"""
        assert parse_pace_tier("第7章：档位=缓冲") == ""
        assert parse_pace_tier("第7章：章尾钩子=悬念") == ""

    def test_pace_tier_of_is_chapter_scoped(self) -> None:
        assert pace_tier_of(_SUBLINE_WITH_TIER, 7) == "推进"
        assert pace_tier_of(_SUBLINE_WITH_TIER, 8) == "高潮"

    def test_pace_tier_of_ignores_phase_level_text(self) -> None:
        """★ 不得在**阶段级**文本里找档位（否则整段里的零星字样会被当本章档位）。

        select_chapter_lines 的兜底语义是「无逐章行 ⇒ 回退阶段行 ⇒ 整段」，
        若不做"必须是本章逐章行"的校验，回退出的整段里若含 `｜档位=` 就会
        被误当成"本章档位" ⇒ 静默失真（纪律 #21 同型）。
        """
        phase_only = (
            "## 章节钩子设计\n\n"
            "铺垫阶段：章尾=日常小悬念（弱）\n"
            "（示例格式：第N章：…｜档位=日常）\n"      # ← 模板示例混在阶段文本里
        )
        assert pace_tier_of(phase_only, 7) == "", "把阶段文本里的示例当成了本章档位"

    def test_absent_tier_returns_empty_for_legacy(self) -> None:
        """★ 未标档位 ⇒ 空串（纪律 #4：口子只挡历史、不影响当前）。"""
        legacy = "## 章节钩子设计\n\n第7章：章首钩子=传唤｜章尾钩子=赃物\n"
        assert pace_tier_of(legacy, 7) == ""


# ============================================================
# 四、泄漏防护：档位也被类级指纹覆盖（补既有漏洞）
# ============================================================
class TestTierLeakGuard:
    def test_tier_field_registered_in_contract_fields(self) -> None:
        """`档位` 必须在 CONTRACT_FIELDS 登记 ⇒ 自动进入既有交叉核对红线。"""
        assert "档位" in CONTRACT_FIELDS

    def test_tier_in_annotation_fingerprint(self) -> None:
        """类级指纹必须能拦「- 档位=推进」这类批注泄漏（否则新字段成了泄漏新入口）。"""
        assert "档位" in CONTRACT_ANNOTATION_RE.pattern
        assert CONTRACT_ANNOTATION_RE.match("- 档位=推进"), "指纹漏掉档位批注"

    def test_fingerprint_keywords_derivable_from_ssot(self) -> None:
        """★ 成员关系红线（补既有漏洞）：指纹关键词必须可由契约真源派生。

        ⚠ 此前该关键词集是**手写字面量**，与 CONTRACT_FIELDS 无核对
        ⇒ 正是纪律 #19 的原型（两边各写一份、无人核对 ⇒ 一次改名即双向破裂）。
        允许的来源：CONTRACT_FIELDS 的成员 + 两个小节名的词根。
        """
        allowed = set(CONTRACT_FIELDS) | {
            HOOKS_SECTION, POINTS_SECTION, "伏笔任务", "情节点", "钩子", "情绪",
        }
        # 从指纹里抠出候选关键词（分隔符之间的中文片段）
        body = CONTRACT_ANNOTATION_RE.pattern
        m = re.search(r"\(\?:([^)]+)\)\[", body)
        if not m:  # 指纹结构变了 ⇒ 本测试需同步（显式失败优于静默放过）
            pytest.fail("未能从 CONTRACT_ANNOTATION_RE 提取关键词集，请同步本测试")
        keys = {k for k in m.group(1).split("|") if k}
        unknown = {k for k in keys if k not in allowed}
        assert not unknown, (
            f"指纹关键词 {sorted(unknown)} 无法由契约真源派生 —— "
            "必须在 CONTRACT_FIELDS 或小节名里找到来源（禁止手写第二份清单）"
        )
