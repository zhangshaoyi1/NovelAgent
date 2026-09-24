"""红线：凡设计，三端可达（2026-09-16）——「只设计不通知」防复发。

命题（作者 2026-09-16）
--------------------
> 「除了性格之外，别的都不是一成不变的，设计好的内容全部都要传递给写手评委
> 最终落盘，不要只设计不通知，这样设计没有任何意义。」

事故链条（登记单 ``20260916_角色弧光规格未建立与设计内转变被误判``）
-----------------------------------------------------------------
``plan.json.route.nodes[].main_branch.growth`` 早已登记「心性：隐忍→果敢」，
写手链拿到了（``m5_context`` 渲染「成长预期」），但**批末体检零引用** ⇒
写手每轮被要求写「兼济」、评委每轮被要求抓「前后矛盾」且看不到这是设计 ⇒
窗口落在弧线转变区时失败是**确定性**的 ⇒ 无限回退、净增 0 章。

本文件把三端到达性钉死，防止任何一端再次"漏接"：

===================  ==========================================================
端                   断言
===================  ==========================================================
写手                 ``m5_context`` 装配设计产出并放进 ctx；两条写章路径都注入
评委                 ``reader_appeal`` **对所有维度**注入设计产出（不再只给 3 维）
落盘                 ``m5_persist`` 覆盖设计维度事实（境界/关系/心性），不止「性格」
判据                 ``dimension_registry`` 的三条一致性维判据写明「按设计轨判定」
===================  ==========================================================
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "src"
_AGENT = _SRC / "agent"


def _read(rel: str) -> str:
    return (_AGENT / rel).read_text(encoding="utf-8")


def _func_source(rel: str, name: str) -> str:
    """取指定文件里某个函数的源码片段（找不到则 FAIL）。"""
    src = _read(rel)
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    pytest.fail(f"{rel} 中找不到函数 {name}（红线失效，请更新本测试）")


# ============================================================
# 评委端：对所有维度注入（事故真缺口）
# ============================================================
class TestJudgeGetsDesignIntent:
    def test_gather_for_eval_injects_design_for_every_dimension(self) -> None:
        """``_gather_for_eval`` 必须**无条件**注入设计产出——不得再按维度白名单分流。

        旧实现是 ``if dimension in _CANON_DIMS: <注入贫瘠版设定真源>``：
        评委读的是「状态/时间线/基础」，**不含弧光/关系/章级意图/判据」。
        一旦恢复成条件注入，本红线必须失败。

        注意：``_CANON_DIMS`` 本身仍可出现——但**只能**用在"真源缺失就显性降级"
        的告警分支里（判定不了 ≠ 没问题），绝不能再当注入开关。
        """
        src = _read("core/quality/scoring/reader_appeal.py")
        tree = ast.parse(src)
        func = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_gather_for_eval"
        )

        # 1) 必须存在对 _design_facts 的调用
        design_calls = [
            n
            for n in ast.walk(func)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "_design_facts"
        ]
        assert design_calls, (
            "_gather_for_eval 必须注入设计产出（_design_facts）——"
            "缺了它评委看不到设计轨，回退误判会复发"
        )

        # 2) 该调用不得被任何以 _CANON_DIMS 为条件的 If 包住
        def _mentions_canon_dims(node: ast.AST) -> bool:
            return any(
                isinstance(x, ast.Name) and x.id == "_CANON_DIMS"
                for x in ast.walk(node)
            )

        for call in design_calls:
            for node in ast.walk(func):
                if not isinstance(node, (ast.If, ast.IfExp)):
                    continue
                guarded = _mentions_canon_dims(node.test)
                inside = any(
                    stmt is call or call in ast.walk(stmt)
                    for stmt in ast.walk(node)
                    if isinstance(stmt, ast.stmt)
                )
                assert not (guarded and inside), (
                    "设计产出不得按 _CANON_DIMS 白名单分流——"
                    "它对所有维度都是判定前提，不是某几维的特供"
                )

    def test_design_facts_uses_shared_assembly(self) -> None:
        """评委端的设计产出必须来自共同装配器，禁止自行抽取（否则三端会漂移）。"""
        src = _read("core/quality/scoring/reader_appeal.py")
        assert "from agent.core.story.design_brief import build_design_brief" in src
        snippet = _func_source("core/quality/scoring/reader_appeal.py", "_design_facts")
        assert "build_design_brief" in snippet

    def test_appeal_path_also_gets_design(self) -> None:
        """迷爱看 6 维的批末路径同样要拿到设计轨（character_arc 依赖成长意图）。"""
        snippet = _func_source(
            "core/quality/scoring/reader_appeal.py", "_load_eval_appeal_kwargs"
        )
        assert "build_design_brief" in snippet

    def test_judge_prompt_carries_exemption(self) -> None:
        """三条一致性维的判据必须写明「按设计轨判定」，否则仍会误报。"""
        from agent.core.quality.dimension_registry import DIMENSIONS

        for name in (
            "character_stability_high",
            "setting_consistency_high",
        ):
            label = DIMENSIONS[name].prompt_label
            assert "设计" in label, (
                f"{name} 的判据未提「设计轨」——评委会把设计内转变数成崩坏"
            )
        assert "弧光" in DIMENSIONS["character_stability_high"].prompt_label
        assert "台账" in DIMENSIONS["setting_consistency_high"].prompt_label


# ============================================================
# 写手端
# ============================================================
class TestWriterGetsDesignIntent:
    def test_ctx_carries_design_brief(self) -> None:
        src = _read("workflows/writing/m5_context.py")
        assert '"design_brief": design_brief_text' in src, (
            "m5_context 必须把设计产出放进 ctx（写手侧唯一供给点）"
        )
        assert "build_design_brief(" in src

    def test_write_path_appends_design_block(self) -> None:
        """唯一写章入口必须注入设计产出。

        2026-09-16：废弃 M5 写章入口（``m5_write_chapter.py`` 的 ``_generate_chapter``）
        已随 ``run()`` 删除（登记单 ``20260916_闸门信号可达性普查`` §三.C2），
        故由「两条路径都要注入」收敛为「唯一入口必须注入」。断言口径不变
        （仍是行为级：必须真的 ``+=`` 追加到提示词，不是取了就算）。
        """
        src = _read("workflows/writing/agentic_write.py")
        assert 'ctx.get("design_brief")' in src, "生产写章入口未注入设计产出"
        # 必须是"追加到提示词"，不是取了就算
        assert "+= " in src


# ============================================================
# 落盘端
# ============================================================
class TestPersistCoversDesignDimensions:
    def test_extractor_emits_design_facts(self) -> None:
        """事实抽取器必须覆盖设计维度（境界/关系/心性），不止五类基础事实。

        只落盘 presence/state/location/holder/count ⇒ 「境界提升、关系演变、
        心性转变」永不结账 ⇒ 后续章节永远拿初始档案说话。
        """
        src = _read("workflows/writing/m5_persist.py")
        for field_name in ("realm", "relation", "disposition"):
            assert f'"{field_name}"' in src, (
                f"事实抽取器未覆盖设计维度 {field_name}——"
                f"该维度的推进会丢失，后续章节按旧状态写"
            )

    def test_design_brief_exposes_persist_renderer(self) -> None:
        from agent.core.story.design_brief import DesignBrief

        assert hasattr(DesignBrief, "render_for_persist")


# ============================================================
# 供给单源本身
# ============================================================
class TestSingleSourceOfDesignSupply:
    def test_module_exists_and_exports_three_renderers(self) -> None:
        from agent.core.story import design_brief

        for fn in ("render_for_writer", "render_for_judge", "render_for_persist"):
            assert hasattr(design_brief.DesignBrief, fn), f"缺 {fn}"

    def test_no_consumer_reimplements_extraction(self) -> None:
        """禁止消费端各自实现"抽设计产出"（写手/评委必须共用同一装配）。

        例外：``m5_context`` 自身的历史抽取函数（角色硬约束/设定台账）服务于
        **硬约束措辞**，与设计轨装配是两件事，不在本红线范围内。
        """
        import re as _re

        judge = _read("core/quality/scoring/reader_appeal.py")
        # 评委端不得自建 plan.json 解析（route/quality_targets 的抽取归 design_brief）
        assert not _re.search(r'plan\.json"\)\.read_text|"plan\.json"', judge), (
            "评委端自行解析 plan.json = 又一条抽取通路，必将与 design_brief 漂移"
        )


# ============================================================
# 承接=（章首状态结转）三端供给（2026-09-21）
# ============================================================
class TestCarriedStateThreeEndSupply:
    def test_design_brief_carries_opening_state_field(self) -> None:
        """DesignBrief 必须携带承接=块（裁判一致性对齐的权威起点）。"""
        from agent.core.story.design_brief import DesignBrief

        assert "opening_state" in DesignBrief.__dataclass_fields__, (
            "承接=（opening_state）未成为设计产出的一部分——评委拿不到权威章首状态"
        )

    def test_judge_render_injects_carried_state(self) -> None:
        """评委端渲染必须把承接=置于对齐起点（CARRIED_TO_JUDGE），不得只靠旧台账。"""
        src = _read("core/story/design_brief.py")
        judge = _func_source("core/story/design_brief.py", "render_for_judge")
        assert "CARRIED_TO_JUDGE" in judge, (
            "render_for_judge 未注入承接=判定前提——评委会拿旧台账当标尺"
        )
        # 承接块必须出现在档位块之前（对齐起点最先可见）
        assert judge.index("CARRIED_TO_JUDGE") < judge.index("window_pace_tiers"), (
            "承接=应置于评委端判定参照系**之前**（作为对齐起点，最不易被预算挤出）"
        )

    def test_persist_render_injects_carried_state(self) -> None:
        """落盘端渲染必须携带承接=结转核对基线（CARRIED_TO_PERSIST）。"""
        persist = _func_source("core/story/design_brief.py", "render_for_persist")
        assert "CARRIED_TO_PERSIST" in persist, (
            "render_for_persist 未注入承接=结转核对——落盘端只写不读"
        )

    def test_opening_state_assembled_from_continuity_singleton(self) -> None:
        """承接=必须与写手端 continuity_projection **同源**（core/continuity 投影）。
        禁止另起抽取通路，否则评委与写手又漂移。
        """
        snippet = _func_source("core/story/design_brief.py", "_render_opening_state")
        assert "core.continuity" in snippet, "承接=未从 core/continuity 账本投影装配"
        assert "project_to_text" in snippet, "承接=必须复用 project_to_text（与写手端同源）"

    def test_writer_render_injects_carried_anchor(self) -> None:
        """写手端必须注入承接=的**硬约束锚**（CARRIED_TO_WRITER），且置于块首。

        2026-09-21：ch25 实证 ``continuity_projection`` 只是**说明性**投影，给不到
        写手"这是权威起点、只能连续演进"的硬约束力 ⇒ 引灵中期被连跳两级到淳真期初期。
        故 ``render_for_writer`` 必须在最前注入写手专用锚。CARRIED_TO_JUDGE/PERSIST
        系评委/落盘专用，不得混入写手端；写手锚必须优先于设计意图（连续起点最先可见）。
        """
        src = _read("core/story/design_brief.py")
        writer = _func_source("core/story/design_brief.py", "render_for_writer")
        assert "CARRIED_TO_WRITER" in writer, (
            "render_for_writer 未注入写手端承接=硬约束锚——写手看不到权威章首境界，越级会复发"
        )
        assert "CARRIED_TO_JUDGE" not in writer and "CARRIED_TO_PERSIST" not in writer, (
            "写手端不得混入评委/落盘专用锚（防止语义错位）"
        )
        assert writer.index("CARRIED_TO_WRITER") < writer.index("chapter_intent"), (
            "承接=写手锚应置于设计意图**之前**（连续起点最先可见、最不易被预算挤出）"
        )
        assert "CARRIED_TO_WRITER" in src, (
            "CARRIED_TO_WRITER 常量必须存在于 design_brief——否则写手锚无正文"
        )


# ============================================================
# 阶段级 vs 章级粒度标注 + 优先级仲裁（2026-09-24）
# ============================================================
class TestStageDirectionGranularity:
    """事故：ch41 一章内 引灵→淳真 连跳两境。

    根因是 ``_render_chapter_intent`` 把**阶段级**（跨多章）的「支线目标/主线方向」
    与本章级内容混排，而写手端标题声称整块「本章须落实」——两者冲突且无仲裁，
    写手服从"须落实" ⇒ 把阶段目标挤进一章。本组断言把粒度标注钉死。
    """

    def test_stage_level_lines_are_labeled_cross_chapter(self) -> None:
        from agent.core.story.design_brief import _render_chapter_intent

        md = "## 支线目标\n用三条支线把工坊做成全州最大的灵材供应方\n"
        out = _render_chapter_intent(
            md, chapter_num=41, route_title="规模化深化", route_result="占据散修市场"
        )
        staged = [
            ln for ln in out.splitlines()
            if ln.startswith("- ") and ("支线目标" in ln or "主线方向" in ln)
        ]
        assert len(staged) == 2, f"支线目标/主线方向两行应都在，实际：{out!r}"
        for ln in staged:
            assert "跨多章" in ln and "本章不必完成" in ln, (
                "阶段级（跨多章）目标必须自带粒度标注——否则写手会把它当"
                "「本章须落实」，在一章内做完整条阶段目标（ch41 越级跳变复发）"
            )

    def test_stage_direction_label_does_not_reuse_non_current_token(self) -> None:
        """粒度标注**不得**复用「非本章」三字。

        「非本章」是 ``chapter_contract.NON_CURRENT_LABEL_SUFFIX`` 的既有专义
        （"回退到最近前文、证据弱"）。阶段方向复用同词 ⇒ 提示词里出现两个同词异义的
        参照系（写手/评委都要猜是"证据弱"还是"别在本章做完"）——这正是纪律 #20
        警告的参照系错位。此断言在 2026-09-24 首版措辞（"非本章须完成"）上曾失败。
        """
        from agent.core.story.chapter_contract import NON_CURRENT_LABEL_SUFFIX
        from agent.core.story.design_brief import _render_chapter_intent

        md = "## 支线目标\n在青石镇站住脚\n"
        out = _render_chapter_intent(
            md, chapter_num=3, route_title="立足", route_result="第一笔口碑"
        )
        assert NON_CURRENT_LABEL_SUFFIX not in out, "阶段方向不得携带弱证据标注"
        assert "非本章" not in out, (
            "「非本章」是弱证据（回退到最近前文）的专义，阶段方向复用会造成同词异义"
        )

    def test_chapter_level_lines_not_labeled_cross_chapter(self) -> None:
        """粒度标注不得泛化到章级行（钩子/情节点），否则章级目标被写手忽略。"""
        snippet = _func_source("core/story/design_brief.py", "_render_chapter_intent")
        assert snippet.count("阶段方向·跨多章") == 2, (
            "「跨多章」标注只应出现在阶段级两行（支线目标/主线方向）——"
            "出现次数不符说明标注被误加到了章级行（钩子/情节点）"
        )

    def test_writer_intent_heading_is_neutral(self) -> None:
        writer = _func_source("core/story/design_brief.py", "render_for_writer")
        assert "【本章设计意图（规划端已登记，本章须落实；不得自行改道）】" not in writer, (
            "写手端设计意图标题不得再声称整块都「本章须落实」——"
            "块内含阶段级信息，该措辞与承接锚的「禁止越级跳变」直接冲突且无仲裁"
        )
        assert "跨多章" in writer, (
            "写手端标题必须显式声明「标注跨多章的禁止在单章内完成」"
        )


class TestCarriedPriorityArbitration:
    """承接=（CARRIED_TO_WRITER）必须给出**冲突仲裁**，否则并列信息仍靠模型自行取舍。"""

    def test_arbitration_clause_present(self) -> None:
        from agent.core.story.design_brief import CARRIED_TO_WRITER

        assert "优先级仲裁" in CARRIED_TO_WRITER, (
            "承接锚缺【优先级仲裁】段——设计意图与承接状态冲突时无判定口径"
        )
        for kw in ("至多从承接值推进一境", "不得在单章内完成"):
            assert kw in CARRIED_TO_WRITER, f"仲裁段缺少关键约束：{kw}"
