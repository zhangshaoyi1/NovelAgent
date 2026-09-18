"""章级契约粒度「三端可达」红线（2026-09-18）。

命题（用户 2026-09-18）：
> 「我理解自动写很简单啊，规划者给足规划的信息，写手根据规划者的规划去写，
> 评委根据规划者和写手的内容去校验，哪个环节出问题就找谁——是给的信息不够，
> 还是什么原因。」

取证结论（《灵荒薪传》实证）：**是给的信息不够，而且不够在粒度上**。
`prompts/m3/outline.md` 此前要求规划者「**按压力阶段**」给 chapter_hooks /
plot_points（"每阶段一行" / "每阶段 3-6 个"）⇒ 单条支线 180 章只有 4 行阶段模板
⇒ 写手拿到的"本章意图"可套用到任意一章、章级内容只能自行编造 ⇒ 评委按通用叙事
尺判不合格 ⇒ 回退重写输入一字不变 ⇒ 整窗销毁-重写死循环。

本文件钉住修复后的**两条因果链**（纪律 #11：验收写成"某条因果链被切断"）：
1. 规划者按章供给 ⇒ 写手/评委**只拿到本章**契约（不再拿到别章/阶段模板）；
2. 提示词里的**书写格式**与消费者的**解析式**是同一件事的两半
   （纪律 #3）——改其一必须同时改另一半，故本文件把它钉成一条红线。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.core.infra.prompt_manager import pm
from agent.core.story.chapter_contract import (
    HOOKS_SECTION,
    POINTS_SECTION,
    chapter_contract,
    chapter_line_pattern,
    has_chapter_level_lines,
    select_chapter_lines,
)

# ============================================================
# 章级供给的细纲（每章一行，格式 = 提示词要求的格式）
# ============================================================
_HOOKS_TITLE = "章节钩子设计"
_POINTS_TITLE = "情节点序列"

_SUBLINE_MD = """---
subline_id: "S01_工坊崛起"
subline_name: "工坊崛起"
---

# 支线设定 · 工坊崛起

## 支线目标

从灵植杂役晋升外门弟子，建立第一条人工作坊流水线。

## 章节钩子设计

第1章：章首钩子=杂役清晨分派｜章尾钩子=灵田土壤冲突被当众归咎｜爽点=无｜目标情绪=压抑
第2章：章首钩子=管事克扣灵肥｜章尾钩子=吞噬诀自发吸走一缕火灵力｜爽点=能力初现｜目标情绪=惊疑
第3章：章首钩子=账簿与实收对不上｜章尾钩子=废弃药圃里的旧傀儡残骸｜爽点=发现传承线索｜目标情绪=好奇
第7章：章首钩子=执事堂传唤｜章尾钩子=同门诬陷的赃物被塞进他铺位｜爽点=用账目反将一军｜目标情绪=紧张
第8章：章首钩子=对决前夜清点符箓｜章尾钩子=挑衅者的剑被一招收走｜爽点=越阶压场｜目标情绪=热血
20章之后：按压力阶段给基调

## 情节点序列

第1章：林凡被分派清理废弃药圃时发现两畦灵植根系互相枯萎；账簿与实收对不上，他第一次怀疑配额被动过
第2章：他按属性相冲推演出土壤冲突规律并记成简表；夜里一缕火灵力被吞噬诀吸走
第3章：在药圃残骸里翻出半具旧傀儡；用神识解析出第一条传承残篇
第7章：执事堂对质，林凡出示三本账册的时间差；被诬陷的赃物当众翻案
第8章：与挑衅者比试，一招夺剑；执事改口承认他是灵植区的"可用之人"
"""

#: 多章挤在同一行（LLM 常用 `；` 而非换行分隔）——解析器必须能切分
_SUBLINE_MD_ONE_LINE = """# 支线设定

## 章节钩子设计

第1章：章首钩子=分派｜章尾钩子=冲突；第2章：章首钩子=克扣｜章尾钩子=觉醒；第3章：章首钩子=查账｜章尾钩子=残骸

## 情节点序列

第1章：发现土壤冲突；第2章：记成简表；第3章：翻出旧傀儡
"""

#: 纯阶段级（历史数据，兼容路径）
_SUBLINE_MD_PHASE = """# 支线设定

## 章节钩子设计

铺垫章：章首=日常铺垫，章尾=危机触发

## 情节点序列

铺垫阶段：发现土壤冲突；首次触发五行吞噬诀
"""

_WORLD_MD = """# 灵荒薪传

## 故事简介

林凡穿越到灵荒大陆，以生产力革命冲击修仙界的资源掠夺模式。
"""

_PLAN = {
    "total_chapters": 1200,
    "quality_targets": {"coherence": 85.0, "readability": 80.0},
    "route": {
        "nodes": [
            {
                "id": "N01",
                "chapter_range": "1-150",
                "milestone": "杂役觉醒",
                "main_branch": {
                    "title": "五行吞噬诀解封",
                    "result": "灵田产量翻倍",
                    "growth": "境界：杂役→引灵；心性：隐忍→果敢",
                },
            }
        ]
    },
}

_CHARACTER_MD = """---
name: "林凡"
role: "protagonist"
realm: "杂役→归一期"
---

# 角色档案 · 林凡

## 内核

- **核心动机**：改变修仙界资源掠夺模式
"""


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    (tmp_path / "world.md").write_text(_WORLD_MD, encoding="utf-8")
    (tmp_path / "characters").mkdir()
    (tmp_path / "characters" / "林凡.md").write_text(_CHARACTER_MD, encoding="utf-8")
    (tmp_path / "sublines" / "S01_工坊崛起").mkdir(parents=True)
    (tmp_path / "sublines" / "S01_工坊崛起" / "subline.md").write_text(
        _SUBLINE_MD, encoding="utf-8"
    )
    (tmp_path / ".state").mkdir()
    (tmp_path / ".state" / "plan.json").write_text(
        json.dumps(_PLAN, ensure_ascii=False), encoding="utf-8"
    )
    (tmp_path / "chapters").mkdir()
    return tmp_path


# ============================================================
# 一、切分实现（唯一真源）
# ============================================================
class TestSelectChapterLines:
    def test_only_current_chapter_line(self) -> None:
        """逐章供给 ⇒ 只取本章行，不含别章。"""
        got = select_chapter_lines(_SUBLINE_MD, HOOKS_SECTION, chapter_num=7)
        assert "第7章" in got
        assert "执事堂传唤" in got
        assert "第8章" not in got
        assert "第2章" not in got

    def test_points_same_rule(self) -> None:
        got = select_chapter_lines(_SUBLINE_MD, POINTS_SECTION, chapter_num=7)
        assert "三本账册的时间差" in got
        assert "一招夺剑" not in got

    def test_multi_chapter_single_line_is_split(self) -> None:
        """多章挤在同一行（`；` 分隔）也必须切出本章——否则本章契约退化为整窗。"""
        got = select_chapter_lines(_SUBLINE_MD_ONE_LINE, HOOKS_SECTION, chapter_num=2)
        assert "克扣" in got
        assert "分派" not in got
        assert "查账" not in got

    def test_cross_reference_to_other_chapter_is_not_a_contract(self) -> None:
        """章级契约里对**别章的交叉引用**（如"（留至第5章）"）不得被误判成别章契约。

        2026-09-18 自查实证：本项目 subline.md 第4章契约含"不得由外门执事或长老出面
        裁决（留至第5章）"，旧匹配式（允许无冒号的裸章号）会把第4章尾部整段当成
        第5章契约 ⇒ 本章契约被污染成"上一章的禁止项"。故章标记必须**带冒号**。
        """
        md = (
            "## 章节钩子设计\n\n"
            "第4章：章首钩子=拼傀儡｜禁=不得由外门执事出面裁决（留至第5章）；不得使用宿命｜验收=读者能说出产量变化\n"
            "第5章：章首钩子=押去执事堂｜禁=不得当场获得外门身份｜验收=读者能说出三本账\n"
        )
        got4 = select_chapter_lines(md, HOOKS_SECTION, chapter_num=4)
        got5 = select_chapter_lines(md, HOOKS_SECTION, chapter_num=5)
        assert "拼傀儡" in got4 and "留至第5章" in got4
        assert got5.startswith("第5章：") and "押去执事堂" in got5
        assert "拼傀儡" not in got5, "第5章契约混入了第4章尾部"
        assert "留至第5章" not in got5

    def test_phase_fallback_for_legacy_data(self) -> None:
        """无逐章行时回退压力阶段行（兼容阶段级历史数据）。"""
        got = select_chapter_lines(
            _SUBLINE_MD_PHASE, HOOKS_SECTION, chapter_num=3, pressure_stage="铺垫"
        )
        assert "危机触发" in got

    def test_section_fallback_when_no_annotations(self) -> None:
        got = select_chapter_lines(_SUBLINE_MD_PHASE, POINTS_SECTION, chapter_num=3)
        assert "五行吞噬诀" in got

    def test_has_chapter_level_lines(self) -> None:
        assert has_chapter_level_lines(_SUBLINE_MD) is True
        assert has_chapter_level_lines(_SUBLINE_MD_PHASE) is False

    def test_chapter_contract_returns_both(self) -> None:
        hooks, pts = chapter_contract(_SUBLINE_MD, chapter_num=8)
        assert "一招收走" in hooks
        assert "一招夺剑" in pts


# ============================================================
# 二、写手端（m5_context）
# ============================================================
class TestWriterSide:
    def test_hooks_only_current_chapter(self) -> None:
        from agent.workflows.writing.m5_context import M5ContextMixin

        got = M5ContextMixin._extract_chapter_hooks(_SUBLINE_MD, 7)
        assert "执事堂传唤" in got
        assert "对决前夜" not in got  # 第8章
        assert "克扣" not in got  # 第2章

    def test_plot_points_only_current_chapter(self) -> None:
        """写手侧情节点此前只按压力阶段取 ⇒ 逐章供给时会整段注入（别章全进）。"""
        from agent.workflows.writing.m5_context import M5ContextMixin

        got = M5ContextMixin._extract_plot_points(_SUBLINE_MD, "铺垫", 7)
        assert "三本账册的时间差" in got
        assert "一招夺剑" not in got
        assert "记成简表" not in got  # 第2章

    def test_plot_points_backward_compatible(self) -> None:
        """旧签名（无章号）仍按压力阶段工作——不得因改粒度而回归。"""
        from agent.workflows.writing.m5_context import M5ContextMixin

        got = M5ContextMixin._extract_plot_points(_SUBLINE_MD_PHASE, "铺垫")
        assert "首次触发五行吞噬诀" in got


# ============================================================
# 三、评委端（design_brief 设计简报 + reader_appeal 批末评估）
# ============================================================
class TestJudgeSide:
    def test_design_brief_chapter_intent_scoped_to_chapter(self) -> None:
        from agent.core.story.design_brief import _render_chapter_intent

        text = _render_chapter_intent(_SUBLINE_MD, chapter_num=7)
        assert "本章钩子设计" in text and "本章情节点" in text
        assert "三本账册的时间差" in text
        assert "一招夺剑" not in text  # 第8章契约不得混入
        assert "按压力阶段给基调" not in text  # 窗口外的阶段标注不得被当作本章契约

    def test_design_brief_legacy_no_chapter(self) -> None:
        from agent.core.story.design_brief import _render_chapter_intent

        text = _render_chapter_intent(_SUBLINE_MD_PHASE)
        assert "钩子设计（按压力阶段）" in text
        assert "危机触发" in text

    def test_reader_appeal_chapter_intent_scoped(self, project: Path) -> None:
        from agent.core.quality.scoring.reader_appeal import _load_eval_appeal_kwargs

        kw = _load_eval_appeal_kwargs(project, chapter_start=7)
        intent = kw.get("chapter_intent", "")
        assert "第7章细纲契约" in intent
        assert "三本账册的时间差" in intent
        assert "一招夺剑" not in intent

    def test_three_ends_share_one_source(self, project: Path) -> None:
        """三端拿到的是**同一份**本章契约（单一真源，禁止各端各写正则）。"""
        from agent.core.quality.scoring.reader_appeal import _load_eval_appeal_kwargs
        from agent.core.story.design_brief import _render_chapter_intent
        from agent.workflows.writing.m5_context import M5ContextMixin

        hooks, pts = chapter_contract(_SUBLINE_MD, chapter_num=7)
        writer_hooks = M5ContextMixin._extract_chapter_hooks(_SUBLINE_MD, 7)
        judge_brief = _render_chapter_intent(_SUBLINE_MD, chapter_num=7)
        judge_eval = _load_eval_appeal_kwargs(project, chapter_start=7)["chapter_intent"]

        assert writer_hooks == hooks
        assert hooks in judge_brief and pts in judge_brief
        assert hooks in judge_eval and pts in judge_eval


# ============================================================
# 四、提示词语言锚 ↔ 消费者解析式（纪律 #3：同一件事的两半）
# ============================================================
def _prompt_text(name: str) -> str:
    pd = pm.get(name)
    chunks = [
        v
        for attr in ("system", "user", "raw", "body", "content", "text", "source")
        if isinstance(v := getattr(pd, attr, None), str)
    ]
    assert chunks, f"无法从 PromptDef 读取文本：{pd!r}"
    return "\n".join(chunks)


class TestPromptAnchorBinding:
    def test_prompt_requires_chapter_level_hooks(self) -> None:
        """规划提示词必须要求**逐章**（`第N章：`）供给，而不是"每阶段一行"。"""
        text = _prompt_text("m3.outline")
        assert "第N章：" in text
        assert "逐章" in text
        # 旧契约（按阶段）不得再作为**唯一**要求出现在章级字段上
        assert "每阶段一行" not in text
        assert "每阶段 3-6 个" not in text

    def test_prompt_documents_cast_ban_and_acceptance(self) -> None:
        """章级契约必须含「在场 / 禁 / 验收」——只给钩子不给边界，写手照样越界。"""
        text = _prompt_text("m3.outline")
        for field in ("在场=", "禁=", "验收="):
            assert field in text, f"提示词未要求章级契约字段 {field}"

    def test_documented_format_is_parseable(self) -> None:
        """提示词里写明的书写格式，必须能被消费者的解析式命中（两半对齐）。"""
        text = _prompt_text("m3.outline")
        assert "第N章：" in text, "提示词未声明逐章书写格式"
        # 按提示词声明的格式手写一行（`第N章：` 前缀 + 全角冒号 + ｜ 分隔字段）
        documented = (
            "第7章：章首钩子=执事堂传唤（中）｜章尾钩子=赃物被塞进铺位（强）"
            "｜爽点=用账目反将一军｜目标情绪=紧张｜在场=林凡，周管事｜禁=不得出现外门长老"
            "｜验收=读者能说出三本账对不上"
        )
        assert chapter_line_pattern(7).search(documented), "解析式与提示词声明的格式不一致"
        # 逐章行必须落在「章节钩子设计」小节内才被提取（小节标题即语言锚）
        md = f"## 章节钩子设计\n\n{documented}\n"
        assert select_chapter_lines(md, HOOKS_SECTION, chapter_num=7) == documented

    def test_window_outside_still_phase_annotated(self) -> None:
        """窗口之外仍允许阶段级标注（不得把长尾也要求逐章，避免规划不可行）。"""
        text = _prompt_text("m3.outline")
        assert "阶段：" in text
