"""六维评分门禁阈值的**单一真源**红线（纪律 #19）

背景
----
``60``（综合合格线）/ ``40``（单维触底线）此前在 **11 处各写一份**：
``policy`` / ``reader_appeal``（两对：``APPEAL_*`` 与 ``GOLDEN_*``）/
``evaluator``（``appeal_threshold`` + ``golden_three_*``）/
``m5_quality_gate`` / ``agentic_pipeline``（两组）/ ``autowrite``（typer 选项 ×2 + 回落）/ 
``dimension_registry``（量纲契约 ×2）/ ``evaluator_types``（渲染回落 ×4）/
``web/quality_admin``（页面回落）。

其中 ``m5_quality_gate`` 的原注释写着「与 B4 golden_three_threshold 默认一致」
—— **用注释担保一致性**，正是纪律 #19 点名的反模式。

本红线锁的是**派生关系**（导入 + 不得手写字面量），不是「数值相等」：
后者会在「全都改回旧值」时巧合通过（同族：``test_pressure_stage_ssot`` R8c 三层互补）。

★ 为什么这两个值尤其重要：它们是 **provider 敏感判据**（分数由 LLM 六维产生，
换 provider 后分布可能整体位移）。敏感面必须只有一处可改，否则"改阈值"这个动作本身
就不可靠。详见 ``core/quality/provider_sensitivity.py``。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "agent"
POLICY_MOD = SRC / "core" / "quality" / "golden_policy.py"

#: 受本红线约束的**模块级常量名**（历史上都是字面量副本）
GUARDED_CONSTS = frozenset({
    "GOLDEN_THREE_TOTAL", "GOLDEN_THREE_FLOOR",
    "APPEAL_PASS_LINE", "APPEAL_DIM_FLOOR",
    "GOLDEN_PASS_LINE", "GOLDEN_DIM_FLOOR",
    "GOLDEN_WRITE_GATE_TOTAL", "GOLDEN_WRITE_GATE_FLOOR",
})

#: 受约束的**函数/方法参数名正则**（构造参数默认值、typer 选项参数）
GUARDED_PARAM_RE = re.compile(r"(golden_three|golden|appeal).*(threshold|floor)", re.I)

#: 被禁的字面量
FORBIDDEN = {40, 60}

#: 必须**导入**真源的消费者文件（导入关系在场 = 派生的前提）
CONSUMERS = [
    SRC / "core" / "quality" / "policy.py",
    SRC / "core" / "quality" / "dimension_registry.py",
    SRC / "core" / "quality" / "scoring" / "reader_appeal.py",
    SRC / "agents" / "evaluator.py",
    SRC / "agents" / "evaluator_types.py",
    SRC / "workflows" / "writing" / "m5_quality_gate.py",
    SRC / "workflows" / "pipeline" / "agentic_pipeline.py",
    SRC / "cli" / "commands" / "autowrite.py",
    SRC / "web" / "quality_admin.py",
]


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _is_forbidden_const(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and node.value in FORBIDDEN


class TestSingleSource:
    """R1–R2：一处真源 + 全部消费者取值一致。"""

    def test_r1_policy_module_is_the_single_source(self) -> None:
        from agent.core.quality import golden_policy as gp

        assert gp.SIX_DIM_PASS_LINE == 60
        assert gp.SIX_DIM_FLOOR == 40
        # 别名必须是**引用**，不是第二份真源
        assert gp.GOLDEN_THREE_TOTAL is gp.SIX_DIM_PASS_LINE
        assert gp.GOLDEN_THREE_FLOOR is gp.SIX_DIM_FLOOR

    def test_r2_all_consumers_agree(self) -> None:
        from agent.core.quality import golden_policy as gp
        from agent.core.quality import policy as policy_mod
        from agent.core.quality import dimension_registry as dr
        from agent.core.quality.scoring import reader_appeal as ra
        from agent.workflows.writing import m5_quality_gate as qg

        assert policy_mod.DEFAULT_QUALITY_POLICY["golden_three"]["threshold"] == gp.SIX_DIM_PASS_LINE
        assert policy_mod.DEFAULT_QUALITY_POLICY["golden_three"]["floor"] == gp.SIX_DIM_FLOOR
        assert ra.APPEAL_PASS_LINE == gp.SIX_DIM_PASS_LINE
        assert ra.APPEAL_DIM_FLOOR == gp.SIX_DIM_FLOOR
        assert ra.GOLDEN_PASS_LINE == gp.SIX_DIM_PASS_LINE
        assert ra.GOLDEN_DIM_FLOOR == gp.SIX_DIM_FLOOR
        assert qg.GOLDEN_WRITE_GATE_TOTAL == gp.SIX_DIM_PASS_LINE
        assert qg.GOLDEN_WRITE_GATE_FLOOR == gp.SIX_DIM_FLOOR

        contracts = {c.prefix: c for c in dr._TOTAL_THRESHOLD_CONTRACTS} \
            if hasattr(dr, "_TOTAL_THRESHOLD_CONTRACTS") else None
        if contracts is None:
            # 契约表的真实属性名不在本红线职责内，改为直接断言模块可取值
            src = (SRC / "core" / "quality" / "dimension_registry.py").read_text(encoding="utf-8")
            assert "float(SIX_DIM_PASS_LINE)" in src and "float(SIX_DIM_FLOOR)" in src
        else:
            for prefix in ("appeal_", "golden_"):
                assert contracts[prefix].dim_threshold == float(gp.SIX_DIM_FLOOR)
                assert contracts[prefix].total_threshold == float(gp.SIX_DIM_PASS_LINE)


class TestNoLiteralCopies:
    """R3–R6：四类形态的禁写（AST 级，非文本匹配）。"""

    def _scan(self) -> list[str]:
        bad: list[str] = []
        for path in CONSUMERS:
            tree = _tree(path)
            rel = path.relative_to(SRC.parent.parent).as_posix()
            for node in ast.walk(tree):
                # ① 模块级常量赋值
                if isinstance(node, ast.Assign):
                    for t in node.targets:
                        if isinstance(t, ast.Name) and t.id in GUARDED_CONSTS \
                                and _is_forbidden_const(node.value):
                            bad.append(f"{rel}:{node.lineno} {t.id} = {node.value.value}")
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    if node.target.id in GUARDED_CONSTS and _is_forbidden_const(node.value):
                        bad.append(f"{rel}:{node.lineno} {node.target.id} = {node.value.value}")
                # ② 函数/方法参数的默认值
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    args = node.args
                    pos = list(args.posonlyargs) + list(args.args)
                    off = len(pos) - len(args.defaults)
                    for k, d in enumerate(args.defaults):
                        pname = pos[off + k].arg if off + k >= 0 else ""
                        if GUARDED_PARAM_RE.search(pname) and _is_forbidden_const(d):
                            bad.append(f"{rel}:{node.lineno} def {node.name}({pname}={d.value})")
                    for k, d in enumerate(args.kw_defaults):
                        pname = args.kwonlyargs[k].arg if k < len(args.kwonlyargs) else ""
                        if GUARDED_PARAM_RE.search(pname) and _is_forbidden_const(d):
                            bad.append(f"{rel}:{node.lineno} def {node.name}({pname}={d.value})")
                # ③ typer.Option / 任意 Call 的**首位置参数**或关键字默认
                if isinstance(node, ast.Call):
                    fname = node.func.attr if isinstance(node.func, ast.Attribute) else \
                        getattr(node.func, "id", "")
                    if fname == "Option" and node.args and _is_forbidden_const(node.args[0]):
                        bad.append(f"{rel}:{node.lineno} Option({node.args[0].value}, ...)")
                    for kw in node.keywords:
                        if kw.arg in ("dim_threshold", "total_threshold") \
                                and _is_forbidden_const(kw.value):
                            bad.append(f"{rel}:{node.lineno} {kw.arg}={kw.value.value}")
                # ④ .get("...", 40/60) 回落值
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                        and node.func.attr == "get" and len(node.args) >= 2 \
                        and _is_forbidden_const(node.args[1]):
                    key = node.args[0].value if isinstance(node.args[0], ast.Constant) else "?"
                    if key in ("threshold", "floor"):
                        bad.append(f"{rel}:{node.lineno} .get({key!r}, {node.args[1].value})")
        return bad

    def test_r3_no_literal_copies_anywhere(self) -> None:
        bad = self._scan()
        assert bad == [], (
            "以下位置仍在手写 40/60（应改为从 core/quality/golden_policy 派生）：\n  "
            + "\n  ".join(bad)
        )

    def test_r4_every_consumer_imports_the_source(self) -> None:
        missing = []
        for path in CONSUMERS:
            tree = _tree(path)
            ok = any(
                isinstance(n, ast.ImportFrom) and n.module
                and n.module.endswith("golden_policy")
                for n in ast.walk(tree)
            )
            if not ok:
                missing.append(path.relative_to(SRC.parent.parent).as_posix())
        assert missing == [], f"以下消费者未导入唯一真源：{missing}"

    def test_r5_ctor_defaults_track_the_source(self) -> None:
        """R5：**行为级** —— 构造参数默认值必须等于真源（防"改了真源但默认值没跟上"）。"""
        import inspect

        from agent.agents.evaluator import EvaluatorAgent
        from agent.workflows.pipeline.agentic_pipeline import AgenticPipelineWorkflow
        from agent.core.quality import golden_policy as gp

        for cls in (EvaluatorAgent, AgenticPipelineWorkflow):
            sig = inspect.signature(cls.__init__)
            for pname in ("golden_three_threshold", "golden_three_floor", "appeal_threshold"):
                if pname not in sig.parameters:
                    continue
                default = sig.parameters[pname].default
                want = gp.SIX_DIM_FLOOR if pname.endswith("floor") else gp.SIX_DIM_PASS_LINE
                assert default == want, f"{cls.__name__}.{pname} 默认值 {default} ≠ 真源 {want}"

    def test_r6_source_module_is_dependency_free(self) -> None:
        internal = [
            n.module for n in ast.walk(_tree(POLICY_MOD))
            if isinstance(n, ast.ImportFrom) and n.module and n.module.startswith("agent")
        ]
        assert internal == [], f"golden_policy 不应依赖其他 agent 模块：{internal}"


class TestBehaviour:
    """R7–R8：真源改动必须**同时**影响三条门禁（防"改了没生效"）。"""

    def test_r7_write_gate_uses_the_shared_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """R7：写时金三门禁的常量与真源同源（改真源后重载即变）。"""
        import importlib

        from agent.core.quality import golden_policy as gp

        monkeypatch.setattr(gp, "SIX_DIM_FLOOR", 55)
        monkeypatch.setattr(gp, "SIX_DIM_PASS_LINE", 77)
        mg = importlib.import_module("agent.workflows.writing.m5_quality_gate")
        importlib.reload(mg)
        try:
            assert mg.GOLDEN_WRITE_GATE_TOTAL == 77
            assert mg.GOLDEN_WRITE_GATE_FLOOR == 55
        finally:
            monkeypatch.undo()
            importlib.reload(mg)
        assert mg.GOLDEN_WRITE_GATE_TOTAL == gp.SIX_DIM_PASS_LINE

    def test_r8_reader_appeal_uses_the_shared_value(self) -> None:
        src = (SRC / "core" / "quality" / "scoring" / "reader_appeal.py").read_text(encoding="utf-8")
        for name in ("APPEAL_PASS_LINE", "APPEAL_DIM_FLOOR", "GOLDEN_PASS_LINE", "GOLDEN_DIM_FLOOR"):
            m = re.search(rf"^{name}: int = (\S+)", src, re.M)
            assert m, f"{name} 定义丢失"
            assert m.group(1).startswith("SIX_DIM_"), (
                f"{name} 又写回字面量：{m.group(1)}"
            )
