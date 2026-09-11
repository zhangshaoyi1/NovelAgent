"""能力对账红线（架构红线测试）

规则（2026-09-11 五灵破 ch181/182 章间矛盾复盘后新增）：

背景：写章入口从 ``M5WriteChapterWorkflow.run()`` 收敛到 ``AgenticWriteWorkflow.run()``
时，G15 ``_archive_chapter``（连续性账本归档）hook 只留在废弃入口内，生产入口从未
调用 → ``ledger.json`` 永不落盘 → 上一章动态状态（物品/人数/动作/约定）对下一章
不可见 → writer 自创细节 → 人设/设定/连贯性硬指标失败熔断。

既有红线 ``test_m5_run_deprecated`` 只禁止跑废弃入口，**不对账能力是否被新入口
继承** —— 本测试补上这一层：

1. **入口对等**：生产入口 ``AgenticWriteWorkflow`` 与废弃入口 ``M5WriteChapterWorkflow``
   的 ``run`` 链路都必须调用 ``_archive_chapter``（防「收敛丢能力」复发）；
2. **零事实生产者红线**：``_archive_chapter`` 内 ``ledger.commit(...)`` 的
   ``facts`` 与 ``ContinuityHandoff(must_carry=...)`` 不得传字面量空列表
   （防占位实现回潮——历史上曾硬编码 ``must_carry=[]`` 存续数月无人发现）。

注意：E3 ``_pre_validation`` 在生产入口显式关闭（``pre_validate=False``，带设计
注释），属**有意取舍**，不纳入本红线。
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "agent"
WRITING = SRC / "workflows" / "writing"


def _parse(rel: str) -> ast.Module:
    return ast.parse((WRITING / rel).read_text(encoding="utf-8"), filename=str(WRITING / rel))


def _class_methods(tree: ast.Module, class_name: str) -> dict[str, ast.FunctionDef]:
    """返回指定类的全部方法 {方法名: 节点}。"""
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                n.name: n
                for n in node.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
    raise AssertionError(f"class {class_name} not found")


def _calls_attribute(fn: ast.AST, attr: str) -> bool:
    """函数体内是否存在 ``< anything >._archive_chapter(...)`` 形态调用。"""
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == attr
        ):
            return True
    return False


def _reachable_from_run(mod: ast.Module, class_name: str, hook: str) -> bool:
    """hook 是否在类的 run 主链路可达：run 自身调用，或 run 调用的同类其他方法内调用。"""
    methods = _class_methods(mod, class_name)
    if "run" not in methods:
        raise AssertionError(f"{class_name}.run not found")
    seen: set[str] = set()
    stack = ["run"]
    while stack:
        name = stack.pop()
        if name in seen or name not in methods:
            continue
        seen.add(name)
        fn = methods[name]
        if _calls_attribute(fn, hook):
            return True
        # 收集同类方法的自调用（self._xxx(...)），沿链下钻
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "self"
            ):
                stack.append(node.func.attr)
    return False


def test_production_entry_wires_archive_hook() -> None:
    """生产入口 AgenticWriteWorkflow.run() 链路必须调用 G15 归档 hook。"""
    tree = _parse("agentic_write.py")
    assert _reachable_from_run(tree, "AgenticWriteWorkflow", "_archive_chapter"), (
        "AgenticWriteWorkflow.run() 未调用 _archive_chapter —— 写章入口收敛时再次"
        "丢失连续性归档能力（能力对账红线），上一章动态状态将对下一章断供"
    )


def test_m5_entry_wires_archive_hook() -> None:
    """废弃入口 M5WriteChapterWorkflow.run() 同样保留归档 hook（入口对等）。"""
    tree = _parse("m5_write_chapter.py")
    assert _reachable_from_run(tree, "M5WriteChapterWorkflow", "_archive_chapter")


def test_archive_hook_has_fact_producer() -> None:
    """_archive_chapter 的 ledger.commit 不得传字面量空列表（零事实生产者红线）。

    facts / must_carry / next_chapter_constraints 必须来自变量或非空表达式；
    历史教训：硬编码 ``must_carry=[]`` 的占位实现存续数月，账本永远只写标题壳。
    """
    tree = _parse("m5_persist.py")
    fn = _class_methods(tree, "M5PersistMixin")["_archive_chapter"]
    checked: list[str] = []

    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute):
            name = node.func.attr  # ledger.commit(...)
        elif isinstance(node.func, ast.Name):
            name = node.func.id  # ContinuityHandoff(...)
        else:
            continue
        if name not in ("commit", "ContinuityHandoff"):
            continue
        for kw in node.keywords:
            if kw.arg in ("facts", "must_carry", "next_chapter_constraints"):
                empty_literal = isinstance(kw.value, ast.List) and not kw.value.elts
                assert not empty_literal, (
                    f"_archive_chapter 内 {name}({kw.arg}=[]) 为字面量空列表 —— "
                    "零事实生产者红线：账本只会写空壳，必须接事实抽取器"
                )
                checked.append(f"{name}.{kw.arg}")

    assert set(checked) >= {"commit.facts", "ContinuityHandoff.must_carry"}, (
        f"未找到待检关键字（找到 {checked}）—— _archive_chapter 结构可能已变化，"
        "请同步更新本测试"
    )
