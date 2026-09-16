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

    @pytest.mark.parametrize(
        "rel",
        ["workflows/writing/agentic_write.py", "workflows/writing/m5_write_chapter.py"],
    )
    def test_both_write_paths_append_design_block(self, rel: str) -> None:
        """两条写章路径都要注入——只接一条 = 「一条路径能看到、另一条看不到」。

        本项目历史上正栽在这类接线漂移上（模板无该字段且从未渲染 ⇒
        autowrite 主路径 Writer 长期看不到）。
        """
        src = _read(rel)
        assert 'ctx.get("design_brief")' in src, f"{rel} 未注入设计产出"
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
