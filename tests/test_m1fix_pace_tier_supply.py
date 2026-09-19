"""M1-Fix 红线：档位供给解耦（独立小节 + 行首字段），2026-09-19。

事故（真实项目取证，非构造）
----------------------------
`novels/灵荒薪传-重启`（09-18 12:31 规划，提示词已 v5）实测：

    subline.md 53 份｜章级行 16 条｜**档位字面 0 个｜解析命中 0 条**

其中 S01 产出 15 条**高质量**章级行（`在场=` `禁=` `验收=` 都具体可判定），
**唯独丢了 `档位=`** —— 因为 v5 把档位放在**行尾**，而 `验收=` 本身要写很长，
LLM 写完验收就当作"行结束"。S02–S05 更完全没走第 7 条（产出阶段级模板）。

根因（纪律 #15 供给粒度 ≠ 消费粒度 的**第三种形态**）
-----------------------------------------------------
- M3 的形态：**阶段级**文本供 **章级**槽位 ⇒ 消费者编造；
- M4 的形态：**单章**档位供 **窗口**消费 ⇒ 参照系错位；
- 本次形态：档位**在场但被同一行的长字段挤掉** ⇒ **供给存在性依赖 LLM 的行长控制**
  ⇒ 整条档位链路（M2 装配 / M3 写手分叉 / M4 评委参照系）**全部空转**。

被切断的因果链（纪律 #11）
--------------------------
1. 独立小节 ``## 章节强度档位`` ⇒ **解析端能读回**（不再与长钩子文本共命运）；
2. 档位写在**行首** ⇒ 不再被 ``验收=`` 的长文本挤掉；
3. v5 历史形态（行内 ``｜档位=X``）**仍可读**（纪律 #4：口子只挡历史）；
4. 未标档位 ⇒ **不编造**（不得按 pressure_curve 反推 = 系统替作者定意图）；
5. 采样闸的"供给小节表"与真源**同源派生**（纪律 #19，否则新增小节即静默失真）。

★ 与既有红线的分工
-------------------
- `test_pace_tier_ssot.py`：断言档位词表 SSOT 与提示词成员的**成员关系**
  ⇒ 本文件**不重复**，只断言 v6 新增的两项**结构**约定（小节 / 行首）；
- `test_m4_eval_pace_reference.py`：断言 M4 评委端参照系
  ⇒ 本文件不动评委端，只保证**供给端**产出可被 M4 消费；
- `test_plan_gate_pace_tier_supply.py`：断言采样闸行为
  ⇒ 本文件断言该闸的**小节表**与真源一致（它若漏了新小节，闸会静默看不见）。
"""

from __future__ import annotations

import re
from pathlib import Path

import agent
import pytest

from agent.core.story.chapter_contract import (
    HOOKS_SECTION,
    PACE_TIER_NAMES,
    POINTS_SECTION,
    TIERS_SECTION,
    pace_tier_coverage,
    pace_tier_of,
    pace_tiers_of_window,
    parse_pace_tier,
)

_PROMPTS = Path(agent.__file__).parent / "prompts"
_TEMPLATE = Path(agent.__file__).parent / "templates" / "subline.md.j2"


def _prompt_text(name: str = "m3.outline") -> str:
    from agent.core.infra.prompt_manager import pm

    pd = pm.get(name)
    chunks = [
        v
        for attr in ("system", "user", "raw", "body", "content", "text", "source")
        if isinstance(v := getattr(pd, attr, None), str)
    ]
    assert chunks, f"无法从 PromptDef 读取文本：{pd!r}"
    return "\n".join(chunks)


# 样本：v6 标准形态（独立小节 + 行首档位）
_V6_SUBLINE = """# 支线设定 · 测试

## 章节钩子设计

第1章：档位=日常｜章首钩子=开局｜章尾钩子=发现疑点｜验收=读者知道主角处境
第2章：档位=推进｜章首钩子=被扣配额｜章尾钩子=灵力入体｜验收=读者知道赌约

## 章节强度档位

第1章：日常
第2章：推进
第3章：推进
第4章：高潮
第5章：垫片

## 情节点序列

第1章：林凡被分派清田；发现根系相冲
"""

# 样本：v5 历史形态（仅行内字段，无独立小节）
_V5_SUBLINE = """# 支线设定 · 测试

## 章节钩子设计

第1章：章首钩子=开局｜验收=读者知道处境｜档位=日常
第2章：章首钩子=被扣配额｜验收=读者知道赌约｜档位=推进
"""


# ============================================================
# 一、独立小节：能与长钩子文本解耦（主修复）
# ============================================================
class TestIndependentTiersSection:
    def test_reads_from_independent_section(self) -> None:
        """① 独立小节必须可被解析端读回（主修复的正面断言）。"""
        for n, expect in ((1, "日常"), (2, "推进"), (3, "推进"), (4, "高潮"), (5, "垫片")):
            assert pace_tier_of(_V6_SUBLINE, n) == expect, f"第{n}章档位读取失败"

    def test_section_survives_when_hooks_lack_tiers(self) -> None:
        """★★ 核心：**钩子行里完全没有档位**时，独立小节仍须供给成功。

        这正是真实事故的形态（S01 的 15 条行没有档位）——
        若修复只在"钩子行写了档位"时生效，就等于没修。
        """
        md = """# 支线设定

## 章节钩子设计

第1章：章首钩子=开局｜验收=读者知道处境
第2章：章首钩子=被扣配额｜验收=读者知道赌约

## 章节强度档位

第1章：日常
第2章：推进
"""
        assert pace_tier_of(md, 1) == "日常"
        assert pace_tier_of(md, 2) == "推进"

    def test_section_is_the_authority_when_both_present(self) -> None:
        """独立小节与行内不一致时，以**独立小节**为准（明确优先级，避免歧义）。"""
        md = """# 支线设定

## 章节钩子设计

第1章：章首钩子=开局｜档位=高潮

## 章节强度档位

第1章：日常
"""
        assert pace_tier_of(md, 1) == "日常", "独立小节应优先于行内字段"

    def test_tolerates_bare_and_annotated_forms(self) -> None:
        """容忍 `第N章：推进` / `第N章：档位=推进` / 带括注三种写法。"""
        for line in ("第3章：推进", "第3章：档位=推进", "第3章：推进（张力 6-8）", "第3章：推进。"):
            assert pace_tier_of(f"## {TIERS_SECTION}\n\n{line}\n", 3) == "推进", line

    def test_unknown_value_in_section_is_empty(self) -> None:
        """独立小节里写了未登记的值 ⇒ 空串（不得猜）。"""
        md = f"## {TIERS_SECTION}\n\n第1章：缓冲\n"
        assert pace_tier_of(md, 1) == ""

    def test_window_covers_all_marked_chapters(self) -> None:
        """窗口函数必须逐章覆盖（M4 消费契约不受本次改动破坏）。"""
        w = pace_tiers_of_window(_V6_SUBLINE, (1, 5))
        assert [n for n, _ in w] == [1, 2, 3, 4, 5]
        assert [t.name for _, t in w] == ["日常", "推进", "推进", "高潮", "垫片"]


# ============================================================
# 二、解析式必须吃下 v6 行首形态（2026-09-19 真实 LLM 取证修复）
# ============================================================
class TestLineHeadTierIsParsed:
    """★★ P0 回归：``第N章：档位=X｜…``（行首、前导是**冒号**）必须被解析。

    事故（真实 LLM 取证，非构造）
    -----------------------------
    提示词 v6（``prompts/m3/outline.md:33``）刻意把 ``档位`` 挪到**行首**
    （紧跟 ``第N章：``），理由是 v5 放行尾会被长 ``验收=`` 挤掉。
    但解析式 ``_PACE_TIER_RE`` 当时写成 ``[｜|]\\s*档位…`` —— **要求前导是竖线**。

    真实 LLM 实测（``.workbuddy/diag/i_verify/m3_outline_raw.txt``，43997 字符）：

        逐章行 239 条｜``档位`` 字样 239 处｜**解析命中 0 条**
        值分布 = 推进82 / 日常53 / 高潮53 / 垫片51  ← 产出本身完全合格

    ⇒ 档位供给链（M2 装配 / M3 写手分叉 / M4 评委参照系）在真实链路 **100% 空转**，
      而当时 164 条档位红线**全绿**（样本用 v5 行中形态）。

    ★ 这是纪律 #3「语言锚与解析式是同一件事的两半」+ #20「单点单测绿 ≠ 链路口子通」
      的双料案例：**改提示词位置时没改解析式**，且**样本与真形态不符**。

    本类断言的重点不是"某个字符串能解析"，而是**四种前导分隔符都吃得下**：
    全角 ``：``（真形态）/ 半角 ``:`` / 全角 ``｜``（历史）/ 半角 ``|``。
    """

    @pytest.mark.parametrize(
        "sep",
        ["：", ":", "： ", "：\t"],
        ids=["fullwidth-colon", "halfwidth-colon", "colon-space", "colon-tab"],
    )
    def test_line_head_after_colon(self, sep: str) -> None:
        """★ 真形态：``第N章`` + 分隔符 + ``档位=`` —— 四种变体都必须命中。"""
        line = f"第1章{sep}档位=日常｜章首钩子=开局｜章尾钩子=发现疑点｜验收=读者知道处境"
        assert parse_pace_tier(line) == "日常", (
            f"行首档位（分隔符 {sep!r}）解析失败 —— v6 提示词就是这种形态，"
            "解析式若要求前导竖线则整条档位链路空转"
        )

    def test_line_head_via_pace_tier_of(self) -> None:
        """★ 端到端：行首形态经 ``pace_tier_of`` 也要拿得到（不只 parse 层）。"""
        md = (
            "# 支线设定\n\n"
            f"## {HOOKS_SECTION}\n\n"
            "第1章：档位=日常｜章首钩子=开局｜章尾钩子=发现疑点\n"
            "第2章：档位=推进｜章首钩子=被扣配额｜章尾钩子=灵力入体\n"
            "第3章：档位=高潮｜章首钩子=摊牌｜章尾钩子=胜负定\n"
        )
        assert pace_tier_of(md, 1) == "日常"
        assert pace_tier_of(md, 2) == "推进"
        assert pace_tier_of(md, 3) == "高潮"
        # 覆盖度读数必须同步（否则采样闸看不见供给）
        assert pace_tier_coverage(md, (1, 3)) == (3, 3, [])

    def test_line_head_without_independent_section(self) -> None:
        """★★ 关键：**没有独立小节**时行首形态仍须可读。

        这正是真实事故形态 —— 真实 LLM 产出里 ``chapter_tiers`` 字段
        与 ``## 章节强度档位`` 小节**都不存在**（实测 count=0），
        唯一可用供给就是 ``chapter_hooks`` 的行首档位。
        若修复只依赖独立小节，等于**没修**。
        """
        md = (
            f"## {HOOKS_SECTION}\n\n"
            "第1章：档位=垫片｜章首钩子=暗流｜章尾钩子=只露疑点\n"
        )
        assert pace_tier_of(md, 1) == "垫片"

    def test_lead_anchor_does_not_swallow_later_positions(self) -> None:
        """★★ 防回归：前导约束**不得**因 ``^`` 分支而拒掉行中位置。

        修复过程中踩到的坑：把前导写成 ``(?:^|[｜|【\\s])`` 时，
        ``^`` 作为**零宽分支**会在位置 0 成功然后整体失败，引擎前移到
        ``：`` 之后时 ``^`` 不成立、字符集又不含 ``：`` ⇒ **整体 None**。
        实测该写法下 ``第1章：档位=日常`` 命中 0，而 ``｜档位=日常`` 命中
        ⇒ **同一模式对不同位置敏感**（正是最隐蔽的一类缺陷）。

        本断言用**同一行的两种前导**对照，锁死"位置不改变可解析性"。
        """
        head = "第1章：档位=推进｜章首钩子=开局"
        mid = "第1章：章首钩子=开局｜档位=推进"
        assert parse_pace_tier(head) == "推进", "行首形态失效（前导约束把 ^ 分支写成消费式）"
        assert parse_pace_tier(mid) == "推进", "行中形态失效（历史数据能力倒退，违反纪律 #4）"


class TestTierFieldBoundary:
    """前导约束的**反面**：同后缀词不得被误命中（放宽前导的代价必须守住）。"""

    @pytest.mark.parametrize(
        "bad",
        ["我方档位=日常", "副档位=推进", "验收档位=高潮", "设定档位=垫片"],
        ids=["woward", "sub-tier", "acceptance-tier", "setting-tier"],
    )
    def test_rejects_prefixed_words(self, bad: str) -> None:
        """左侧紧邻**汉字**的 ``XX档位=`` 不是档位字段 ⇒ 必须拒绝。

        放宽前导（允许行首 / ``：``）时最容易误伤这里：若直接删掉前导约束，
        ``我方档位=日常`` 会被当成档位供给 ⇒ **静默失真**（纪律 #21 同型）。
        """
        assert parse_pace_tier(bad) == "", f"{bad!r} 不是档位字段，不得被解析"

    @pytest.mark.parametrize(
        "line",
        ["第1章：档位=缓冲｜章首钩子=开局", "第1章：档位=｜章首钩子=开局"],
        ids=["unregistered", "empty-value"],
    )
    def test_unregistered_or_empty_value_is_empty(self, line: str) -> None:
        """未登记值 / 空值 ⇒ 空串（下游按"未标档位"处理，不猜）。"""
        assert parse_pace_tier(line) == ""


class TestRealLlmShapeFixture:
    """★ 用**真实 LLM 产物**做样本，杜绝"构造样本与真形态不符"再次发生。

    纪律 #23：构造单测通过 ≠ 真实项目验证。本类直接从取证产物里取行，
    若产物不在本机（CI）则跳过 —— 但**只要在就一定跑**。
    """

    _RAW = (
        Path(agent.__file__).parent.parent.parent.parent
        / ".workbuddy" / "diag" / "i_verify" / "m3_outline_raw.txt"
    )

    def test_real_llm_line_head_parses(self) -> None:
        """真实 LLM 的 239 条逐章行必须全部可解析（修复前 = 0）。"""
        if not self._RAW.exists():
            pytest.skip("真实 LLM 取证产物不在本机（CI 环境）")
        raw = self._RAW.read_text(encoding="utf-8", errors="replace")
        unescaped = raw.replace("\\n", "\n").replace('\\"', '"')
        from agent.core.story.chapter_contract import PACE_TIER_NAMES

        lines = [
            ln.strip()
            for ln in unescaped.splitlines()
            if re.match(r"^第\s*\d+\s*章\s*[：:]\s*档位\s*[=:：]", ln.strip())
        ]
        assert lines, "取证产物里没有行首档位行 —— 样本或产物已变，需重新取证"
        parsed = [(ln, parse_pace_tier(ln)) for ln in lines]
        failed = [(ln[:60], t) for ln, t in parsed if t not in PACE_TIER_NAMES]
        assert not failed, (
            f"真实 LLM 行首档位解析失败 {len(failed)}/{len(parsed)} 条：{failed[:3]}"
        )

    def test_real_llm_produced_no_independent_field(self) -> None:
        """★ 记录事实：真实 LLM **未产出** ``chapter_tiers`` 独立字段与小节。

        这条断言的价值是**锁住缺陷证据**：它证明"独立小节"这条路在真实
        LLM 上没走通 ⇒ 修复**不能只依赖**独立小节（见
        ``test_line_head_without_independent_section``）。
        若将来提示词强化后 LLM 开始产出，本断言会失败 —— 那时应改为
        "两者都要求"，而不是删掉它。
        """
        if not self._RAW.exists():
            pytest.skip("真实 LLM 取证产物不在本机（CI 环境）")
        raw = self._RAW.read_text(encoding="utf-8", errors="replace")
        assert "chapter_tiers" not in raw, "LLM 已开始产出 chapter_tiers ⇒ 需更新本断言口径"
        assert "## 章节强度档位" not in raw, "LLM 已开始产出独立小节 ⇒ 需更新本断言口径"


# ============================================================
# 三、向后兼容（纪律 #4：口子只挡历史）
# ============================================================
class TestLegacyStillWorks:
    def test_v5_inline_form_still_readable(self) -> None:
        """v5 行内形态必须仍可读 —— 否则历史数据能力倒退。"""
        assert pace_tier_of(_V5_SUBLINE, 1) == "日常"
        assert pace_tier_of(_V5_SUBLINE, 2) == "推进"

    def test_unmarked_chapter_returns_empty(self) -> None:
        """未标档位的章 ⇒ 空串（判据回到通用口径，不放松也不收紧）。"""
        assert pace_tier_of(_V6_SUBLINE, 9) == ""
        assert pace_tier_of(_V5_SUBLINE, 3) == ""

    def test_never_invents_from_pressure_curve(self) -> None:
        """★★ **绝不编造**：有 pressure_curve 但无档位 ⇒ 仍为空串。

        按阶段反推档位 = 系统替作者定意图（纪律 #15 消费者编造的老路），
        比缺档更糟 —— 它会让"缺供给"这一缺陷**被掩盖**。
        """
        md = """# 支线设定

## 剧集压力曲线

| 阶段 | 章节 | 张力等级 |
|---|---|---|
| 铺垫 | 1-3 | 低 |
| 冲突 | 4-6 | 中 |
| 高潮 | 7-8 | 高 |
| 舒缓 | 9 | 低 |

## 章节钩子设计

第1章：章首钩子=开局｜验收=读者知道处境
"""
        for n in range(1, 10):
            assert pace_tier_of(md, n) == "", f"第{n}章不应从压力曲线反推档位"

    def test_real_project_still_zero(self) -> None:
        """真实项目（v5 产出、无档位）必须仍解析为 0 —— 不造假。"""
        p = (
            Path(agent.__file__).parent.parent.parent.parent
            / "novels" / "灵荒薪传-重启" / "sublines" / "S01_工坊崛起" / "subline.md"
        )
        if not p.exists():
            pytest.skip("真实项目样本不在本机（CI 环境）")
        content = p.read_text(encoding="utf-8")
        got = [(n, pace_tier_of(content, n)) for n in range(1, 21)]
        assert not [1 for _, t in got if t], "真实无档位数据不得凭空产生档位"

    def test_no_current_line_borrowed(self) -> None:
        """不得借用"最近前文"的档位（参照系错位的既有防线必须保持）。"""
        md = """# 支线设定

## 章节强度档位

第5章：高潮
"""
        assert pace_tier_of(md, 5) == "高潮"
        assert pace_tier_of(md, 9) == "", "第9章不得借用第5章的档位"


# ============================================================
# 三、供给完整度可观测（纪律 #21：缺口不得静默）
# ============================================================
class TestCoverageIsObservable:
    def test_coverage_reports_gaps(self) -> None:
        md = f"## {TIERS_SECTION}\n\n第1章：日常\n第2章：推进\n第5章：高潮\n"
        marked, total, missing = pace_tier_coverage(md, (1, 5))
        assert (marked, total, missing) == (3, 5, [3, 4])

    def test_coverage_full_is_zero_missing(self) -> None:
        marked, total, missing = pace_tier_coverage(_V6_SUBLINE, (1, 5))
        assert (marked, total, missing) == (5, 5, [])

    def test_coverage_empty_window(self) -> None:
        assert pace_tier_coverage(_V6_SUBLINE, (9, 8)) == (0, 0, [])

    def test_coverage_does_not_judge_or_fill(self) -> None:
        """只报数、不判罚、不补值：缺档不抛异常，也不凭空填档。"""
        marked, total, missing = pace_tier_coverage(_V5_SUBLINE, (1, 10))
        assert missing == list(range(3, 11))
        assert pace_tier_of(_V5_SUBLINE, 5) == "", "不得因报数而补值"


# ============================================================
# 四、提示词 / 模板 / 解析三端一致（纪律 #3 / #19）
# ============================================================
class TestThreeEndsAligned:
    def test_prompt_declares_independent_field(self) -> None:
        """提示词必须声明独立字段 `chapter_tiers`（否则规划端不会产出）。"""
        text = _prompt_text()
        assert "chapter_tiers" in text, "提示词未声明独立档位字段"

    def test_prompt_requires_tier_at_line_head(self) -> None:
        """★ v6 约定：`档位=` 必须写在行首（紧跟 `第N章：`）。"""
        text = _prompt_text()
        # 取第 7 条的格式模板行（形如 `第N章：档位=<四档之一>｜章首钩子=…`）
        m = re.search(r"第N章：档位=", text)
        assert m, (
            "提示词格式模板里 `档位=` 未写在 `第N章：` 之后的行首 —— "
            "v5 把它放在行尾，实测被长 `验收=` 文本挤掉（真实项目档位 0 个）"
        )

    def test_prompt_format_template_has_no_trailing_tier(self) -> None:
        """反面断言：模板行不得再把 `档位=` 放在末尾（防止改回 v5 形态）。"""
        text = _prompt_text()
        assert "验收=…｜档位=" not in text, (
            "提示词格式模板仍是 v5 行尾形态 —— 会被长验收文本挤掉"
        )

    def test_template_renders_tiers_section(self) -> None:
        """落盘模板必须渲染 `## 章节强度档位` 小节（否则解析端读不到）。"""
        tpl = _TEMPLATE.read_text(encoding="utf-8")
        assert f"## {TIERS_SECTION}" in tpl, "subline.md.j2 未渲染独立档位小节"
        assert "chapter_tiers" in tpl, "模板未接收 chapter_tiers 变量"

    def test_pipeline_reads_tiers_section(self) -> None:
        """★ 采样闸的"供给小节表"必须包含档位小节，且与真源同源派生。

        若漏了它 ⇒ "只有档位行"的章被算作 no_chapter_line
        ⇒ 闸看不见自己最该观测的供给 ⇒ 纪律 #21 同型的静默失真。
        """
        from agent.workflows.pipeline import plan_consistency as pc

        assert TIERS_SECTION in pc._PLOT_SOURCE_SECTIONS, (
            "采样闸的供给小节表漏了档位小节 ⇒ 供给会被误判为缺位"
        )
        assert set(pc._PLOT_SOURCE_SECTIONS) == {
            HOOKS_SECTION, POINTS_SECTION, TIERS_SECTION
        }, "供给小节表必须与 chapter_contract 的真源完全一致"

    def test_workflow_passes_chapter_tiers_to_template(self) -> None:
        """M3 落盘工作流必须把 chapter_tiers 传给模板（端到端可达）。"""
        src = (
            Path(agent.__file__).parent / "workflows" / "planning" / "m3_outline.py"
        ).read_text(encoding="utf-8")
        assert "chapter_tiers=" in src, "m3_outline 未把 chapter_tiers 传给模板"
        assert 's.get("chapter_tiers"' in src, "未从 LLM 产出里取 chapter_tiers"

    def test_tier_names_are_ssot_members(self) -> None:
        """成员关系（纪律 #19）：解析出的档位名必须都在 SSOT 内。"""
        for n in range(1, 6):
            t = pace_tier_of(_V6_SUBLINE, n)
            assert t in PACE_TIER_NAMES, t
