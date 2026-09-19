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
# 二、向后兼容（纪律 #4：口子只挡历史）
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
