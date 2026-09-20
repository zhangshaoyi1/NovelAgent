"""章节字数下限的**单一真源**红线（纪律 #19：跨模块共享常量须机器交叉核对）

背景
----
``1500`` 此前在**两处各写一份**且代码互不相识：
- ``core/quality/scoring/quality_checker.py``（写时门禁硬下限）
- ``core/quality/book_checkup.py``（全书体检「超短章」阈值）

⇒ 待爆形态：一次改名即**双向破裂**（改一边、另一边静默沿用），而既有红线只锁
各自的存在、**不锁两者的派生关系**。

本文件锁的是**派生关系**，不是「数值相等」——后者会在「两边都改回旧值」时
巧合通过（同族：``test_pressure_stage_ssot`` 的 R8a/R8c 三层互补）。
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "agent"
POLICY = SRC / "core" / "quality" / "length_policy.py"
CHECKUP = SRC / "core" / "quality" / "book_checkup.py"
CHECKER = SRC / "core" / "quality" / "scoring" / "quality_checker.py"

#: 被收口的字面量：任何消费者都不得再手写这个数
FORBIDDEN_LITERAL = 1500


def _module_level_assigns(path: Path) -> dict[str, ast.AST]:
    """取模块顶层（类外/函数外）的 ``NAME = value`` 赋值节点。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.value is not None:
                out[node.target.id] = node.value
    return out


class TestSingleSource:
    """R1–R4：三处同源 + 派生形态 + 消费者真的在用。"""

    def test_r1_three_consumers_agree(self) -> None:
        """R1：唯一真源、门禁、体检三处取值一致。"""
        from agent.core.quality import length_policy
        from agent.core.quality.scoring import quality_checker
        from agent.core.quality import book_checkup

        assert length_policy.ABSOLUTE_MIN_CJK_WORDS == FORBIDDEN_LITERAL
        assert quality_checker.ABSOLUTE_MIN_CJK_WORDS == length_policy.ABSOLUTE_MIN_CJK_WORDS
        assert book_checkup.DEFAULT_MIN_CHAPTER_CHARS == length_policy.ABSOLUTE_MIN_CJK_WORDS

    def test_r2_no_consumer_rewrites_the_literal(self) -> None:
        """R2：两个消费者都**不得**再手写字面量（禁写清单）。

        只看**字面量**赋值（``ast.Constant``）—— 从共享常量派生（``ast.Name``）
        是正确形态，不算违规。这是本红线的主判据，防「两边都改回旧值」的巧合相等。
        """
        checks: list[str] = []
        for path, name in ((CHECKUP, "DEFAULT_MIN_CHAPTER_CHARS"),
                           (CHECKER, "ABSOLUTE_MIN_CJK_WORDS")):
            assigns = _module_level_assigns(path)
            val = assigns.get(name)
            if val is None:
                continue  # 已不再本地赋值（= 纯导入），更好
            if isinstance(val, ast.Constant):
                checks.append(f"{path.name}::{name} = {val.value!r}")
        assert checks == [], (
            "以下位置仍在手写字面量（应改为从 length_policy 派生）：\n  "
            + "\n  ".join(checks)
        )

    def test_r3_derivation_is_a_reference_not_a_copy(self) -> None:
        """R3：体检的阈值必须是**引用**（Name 节点），不是复制过来的常量。

        断言 AST 形态 ⇒ 即使将来有人「用 `1500 - 0` 之类的表达式规避 R2」，
        只要不是引用共享常量就会被抓。
        """
        assigns = _module_level_assigns(CHECKUP)
        node = assigns.get("DEFAULT_MIN_CHAPTER_CHARS")
        assert node is not None, "book_checkup 应保留 DEFAULT_MIN_CHAPTER_CHARS 名称"
        assert isinstance(node, ast.Name), (
            f"DEFAULT_MIN_CHAPTER_CHARS 的值应为共享常量引用（Name），实为 {type(node).__name__}"
        )
        assert node.id == "ABSOLUTE_MIN_CJK_WORDS", f"引用名不符：{node.id}"

    def test_r4_checker_imports_the_shared_constant(self) -> None:
        """R4：写时门禁必须**导入**共享常量（导入关系在场）。"""
        tree = ast.parse(CHECKER.read_text(encoding="utf-8"))
        ok = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and \
                    node.module.endswith("length_policy"):
                if any(a.name == "ABSOLUTE_MIN_CJK_WORDS" for a in node.names):
                    ok = True
        assert ok, "quality_checker 未从 core/quality/length_policy 导入 ABSOLUTE_MIN_CJK_WORDS"

    def test_r5_threshold_is_actually_used_in_behaviour(self) -> None:
        """R5：行为试金石 —— 体检的「超短章」判定确实用该阈值（1499 报 / 1500 不报）。"""
        from agent.core.quality import book_checkup as bc

        limit = bc.DEFAULT_MIN_CHAPTER_CHARS
        short = [{"chapter": 1, "body": "字" * (limit - 1)}]
        exact = [{"chapter": 1, "body": "字" * limit}]
        assert bc.check_word_count(short, limit)["undersized"], "1499 字应被判超短"
        assert not bc.check_word_count(exact, limit)["undersized"], "1500 字不应被判超短"

    def test_r6_policy_module_is_dependency_free(self) -> None:
        """R6：真源模块必须零内部依赖（否则会造出导入环）。"""
        tree = ast.parse(POLICY.read_text(encoding="utf-8"))
        internal = [
            n.module for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom) and n.module and n.module.startswith("agent")
        ]
        assert internal == [], f"length_policy 不应依赖其他 agent 模块：{internal}"


def test_import_order_does_not_matter() -> None:
    """R7：任何导入顺序下取值都一致（防「先导入者冻结旧值」）。"""
    import sys

    for mod in ("agent.core.quality.book_checkup",
                "agent.core.quality.scoring.quality_checker",
                "agent.core.quality.length_policy"):
        sys.modules.pop(mod, None)
    policy = importlib.import_module("agent.core.quality.length_policy")
    checker = importlib.import_module("agent.core.quality.scoring.quality_checker")
    checkup = importlib.import_module("agent.core.quality.book_checkup")
    assert policy.ABSOLUTE_MIN_CJK_WORDS == checker.ABSOLUTE_MIN_CJK_WORDS \
        == checkup.DEFAULT_MIN_CHAPTER_CHARS
    assert pytest is not None
