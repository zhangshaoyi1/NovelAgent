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

        ★★ 2026-09-19 判据收紧（M1-Fix 抓出的红线缺陷）：
          原判据扫**全文**的 ``档位=X``，误把正文里的**元描述**当成取值 ——
          v6 提示词写了两句散文（``档位 必须写在行首`` / ``档位 必须一致``），
          被当成了"未登记档位词"而误报。
          ⇒ 现只扫**格式模板行**（``第N章：档位=…``）里的取值。
          配套新增反向红线：散文里**不得**出现 ``档位=X`` 的裸写法
          （见 ``test_prose_does_not_use_bare_tier_assignment``）——正文用 ``「档位」``
          引用字段名，避免与取值混淆。
        """
        text = _prompt_text("m3.outline")
        # 1) 剥 Markdown 行内装饰，避免反引号/星号混进取值
        clean = re.sub(r"[`*_]", "", text)
        # 2) ★ 只取**格式模板**里的取值：``第N章：档位=X``（行首位置，v6 形态）
        #    以及 ``…｜档位=X``（行内位置，v5 兼容形态）
        raw = re.findall(r"第N章：档位\s*[=:：]\s*([^\s｜|，,、。；;）)：:（(]+)", clean)
        raw += re.findall(r"｜档位\s*[=:：]\s*([^\s｜|，,、。；;）)：:（(]+)", clean)
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

    def test_prose_does_not_use_bare_tier_assignment(self) -> None:
        """★ 反向红线：正文里不得出现 ``档位=X`` 裸写法（只能引用 ``「档位」``）。

        为什么：正文里写 ``档位= 必须写在行首`` 这类**元描述**，一是会让
        "取值域"判据误报（本条红线就是为了钉死这个缺陷），二是**会迷惑 LLM**
        —— 格式模板与正文说明混在一起，模型可能照抄元描述当取值。

        判据：把**格式模板行**剔除后，剩余正文里不应再有 ``档位=``。
        """
        text = _prompt_text("m3.outline")
        clean = re.sub(r"[`*_]", "", text)
        # 剔除两类合法格式模板：行首形态与行内形态
        stripped = re.sub(r"第N章：档位\s*[=:：]\s*\S+", "", clean)
        stripped = re.sub(r"｜档位\s*[=:：]\s*\S+", "", stripped)
        # JSON 骨架里的 ``"chapter_tiers": …`` 不含 ``档位=``，无需豁免
        leaked = re.findall(r".{0,20}档位\s*[=:：].{0,20}", stripped)
        assert not leaked, (
            "提示词正文里出现裸写法 `档位=`（应改用「档位」引用字段名）—— "
            "会同时污染取值域判据与 LLM 理解：\n" + "\n".join(leaked)
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


# ============================================================
# 五、端到端跑闸（2026-09-18 真实项目上发现，纪律 #20）
#
# ★ 这条是**验收级**红线，不是单元级：
#   上面四组判据全绿时，真实项目上 D2 依然**完全失效** ——
#   ``chapter_intent`` 块 1200 字预算被靠前的钩子/情点行长文吃满，
#   档位声明行位于其后 ⇒ 被 ``_clip`` 从**尾部**切断 ⇒ 评委拿不到参照系
#   ⇒ 仍按高潮尺判放松章注水 ⇒ 死循环原样复现。
#   （纪律 #20：「单点单测绿 ≠ 链路口子通」；纪律 #11：验收须写成因果链）
#
#   判据形态刻意选「单调性 + 保全」而不是「数值相等」：
#     - 截断发生时，档位锚行**必须仍在**（保全）；
#     - 文本越长，档位锚行**越不能被挤掉**（单调）；
#     - 老数据无档位 ⇒ 截断形态**逐字不变**（纪律 #4）。
# ============================================================
class TestIntentBudgetPreservesTier:
    """档位声明必须在字符预算截断下**活下来**（否则"注入了"＝没注入）。"""

    @staticmethod
    def _render(tier_name: str, filler_words: int) -> str:
        from agent.core.story.design_brief import _render_chapter_intent

        filler = "支线铺垫与人物关系推进的细碎事件，" * filler_words
        md = (
            "## 支线目标\n目标。\n\n"
            "## 章节钩子设计\n\n"
            f"第7章：章首钩子=传唤｜章尾钩子=赃物｜爽点={filler}｜档位={tier_name}\n\n"
            "## 情节点序列\n\n"
            f"第7章：动作一；动作二；{filler}\n"
        )
        return _render_chapter_intent(md, chapter_num=7)

    @pytest.mark.parametrize("tier_name", ["高潮", "推进", "垫片", "日常"])
    @pytest.mark.parametrize("filler_words", [0, 20, 120])
    def test_tier_survives_clip(self, tier_name: str, filler_words: int) -> None:
        from agent.core.story.design_brief import _BUDGET, _clip

        text = self._render(tier_name, filler_words)
        clipped = _clip(text, "chapter_intent")
        assert len(clipped) <= _BUDGET["chapter_intent"] + 16, "截断后仍超预算"
        assert f"本章强度档位：{tier_name}" in clipped, (
            f"档位 {tier_name}（填充 {filler_words} 词）被截断吃掉 ⇒ 评委拿不到参照系"
        )

    def test_clip_is_monotonic_in_filler(self) -> None:
        """反单调＝缺陷：填充越多，档位越不该消失。"""
        from agent.core.story.design_brief import _clip

        seen = []
        for words in (0, 10, 40, 90, 160, 260):
            clipped = _clip(self._render("日常", words), "chapter_intent")
            seen.append("本章强度档位：日常" in clipped)
        assert all(seen), f"填充增加后档位消失（单调性破裂）：{seen}"

    def test_no_tier_no_anchor_legacy_unchanged(self) -> None:
        """★ 纪律 #4：老数据（无档位）⇒ 无锚 ⇒ 截断行为与修复前**逐字相同**。

        ⚠ 本测试初版三度写错，每次都是**没先取证就断言**（值得记下来的教训）：
        1. 以为 ``_clip`` 从末尾删字 —— 实为**截头**（保留前 limit 字）；
        2. 以为多行填充能撑到 1200 —— hooks 段先被 ``[:400]``、
           points 段被 ``[:500]`` 各自截过 ⇒ 加起来只有 ~400+500，
           **`chapter_intent` 单块根本到不了 1200** ⇒ 撑不动的方向错了；
        3. 正确做法：直接**单独**验证 ``_clip`` 的"无锚"路径（那才是本次改动的
           行为面），不要绕渲染器。
        """
        from agent.core.story.design_brief import _BUDGET, _clip

        plain = "- 章节钩子设计：" + "填充，" * 900
        assert len(plain) > _BUDGET["chapter_intent"], "样本未超预算 ⇒ 没在测截断"
        assert _clip(plain, "chapter_intent") == (
            plain[: _BUDGET["chapter_intent"]] + "…（已截断）"
        ), "无档位锚时截断形态改变 ⇒ 影响了当前路径（口子只应挡历史）"

    def test_render_output_is_never_clipped_in_practice(self) -> None:
        """★ 反证上面那条：真实渲染路径的 ``chapter_intent`` **到不了** 1200 预算。

        这是本次排查的一个副产品认识（值得钉住，防止后人误以为"渲染已经在截断了"）：
        hooks 段 ``[:400]`` + points 段 ``[:500]`` + 档位行 ~120 + 支线目标 ~300
        ⇒ 单块上限约 1300，实测老数据（无档位行）约 410–920 ⇒ **多数情况下不触发
        ``_clip``**；触发时只可能是"长档位声明 + 放松说明"（约 250 字）把总量推过线。
        故档位锚的保全红线（``TestIntentBudgetPreservesTier.test_tier_survives_clip``）
        是**必需的**：那才是档位行真会消失的那条路径。
        """
        from agent.core.story.design_brief import _BUDGET, _render_chapter_intent

        long_line = "第7章：章首钩子=a｜章尾钩子=b｜" + "填充，" * 260 + "\n"
        legacy = "## 章节钩子设计\n\n" + long_line * 4
        text = _render_chapter_intent(legacy, chapter_num=7)
        assert len(text) < _BUDGET["chapter_intent"], (
            f"老数据渲染竟达 {len(text)} 字（预算 {_BUDGET['chapter_intent']}）"
            "—— 若确实超了，说明各段上限被调大，本测试与护栏需同步复核"
        )
