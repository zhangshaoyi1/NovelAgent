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

注意（2026-09-11 升级为「真机制」）：原实现是**硬编码白名单**——只对账
``_archive_chapter`` 一个 hook，其余断链（14:30 排查报告列出的 P1×4）一个都发现不了。
现改为**差集对账**：自动提取 ``M5WriteChapterWorkflow.run()`` 链路可达的全部私有能力
（``self._xxx(...)``），与生产入口 ``AgenticWriteWorkflow.run()`` 链路内出现的调用名
做差集；差集必须 ⊆ :data:`_PARITY_EXEMPT` 豁免登记表（每项须写明去向）。
任何只接线到旧入口、未随迁的新能力都会立刻出现在差集里并被拦下。

``_pre_validation``（E3 前置门禁）在生产入口显式关闭（``pre_validate=False``，带设计
注释），属**有意取舍**——登记在豁免表内，而非接线。
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


# ============================================================
# 能力对账（真机制，2026-09-11）
# ============================================================
def _collect_run_chain(tree: ast.Module, class_name: str) -> tuple[set[str], set[str]]:
    """返回 ``(self 私有方法调用集, 链路内所有属性调用名集)``。

    从 ``run`` 出发沿同类 ``self.<name>(...)`` 自调用下钻，收集整条链路：

    - 第一个集合 = 该类拥有的**私有能力**（``self._xxx``）——「旧入口挂了什么」；
    - 第二个集合 = 链路内**任意**属性调用名（含 ``m5._validate_evidence(...)``
      这类外部实例调用）——「新入口调用了什么」。
    """
    methods = _class_methods(tree, class_name)
    if "run" not in methods:
        raise AssertionError(f"{class_name}.run not found")
    seen: set[str] = set()
    stack = ["run"]
    self_priv: set[str] = set()
    all_attrs: set[str] = set()
    while stack:
        name = stack.pop()
        if name in seen or name not in methods:
            continue
        seen.add(name)
        for node in ast.walk(methods[name]):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                all_attrs.add(node.func.attr)
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "self":
                    self_priv.add(node.func.attr)
                    stack.append(node.func.attr)
    return {n for n in self_priv if n.startswith("_")}, all_attrs


#: 「废弃入口有、生产入口无」能力的**豁免登记表**：每项必须写明去向与理由。
#: 新增能力若未在生产入口接线、又未登记在此 → 差集对账红线 FAIL（防「收敛丢能力」复发）。
_PARITY_EXEMPT: dict[str, str] = {
    "_generate_chapter": "生成收敛到 WriterAgent（agentic_write.py 内 writer.run）",
    "_quality_check_and_revise": "章内质检+修订收敛到 WriterAgent（quality_gate 注入）",
    "_format_chapter_body": "正文规范化改由 _finalize_chapter_text 承担（同链幂等）",
    "_maybe_deslop": "去 AI 味改由 AgenticWriteWorkflow._run_deslop 承担",
    "_load_published_titles": "标题查重改由 m5._ensure_unique_title 内部加载",
    "_present": "呈现层上移至 CLI/上层，生产入口只返回 AgenticWriteResult",
    "_pre_validation": (
        "E3 前置门禁为有意取舍（agentic 显式 pre_validate=False），由生成后 M21 审稿兜底"
    ),
}


def test_production_entry_covers_m5_side_effects() -> None:
    """能力对账（真机制）：旧入口 run 链路的私有能力，生产入口必须覆盖或显式豁免。

    机制：``M5.run()`` 可达的 ``self._xxx(...)`` 能力集 − ``AgenticWriteWorkflow.run()``
    链路内出现的调用名 = 差集；差集必须 ⊆ ``_PARITY_EXEMPT``。
    → 取代「硬编码只对账一个 hook」的白名单实现，能自动发现下一个「哑火」。
    """
    m5_caps, _ = _collect_run_chain(_parse("m5_write_chapter.py"), "M5WriteChapterWorkflow")
    _, ag_attrs = _collect_run_chain(_parse("agentic_write.py"), "AgenticWriteWorkflow")
    gap = sorted(m5_caps - ag_attrs)
    unexplained = [g for g in gap if g not in _PARITY_EXEMPT]
    assert not unexplained, (
        "以下能力在废弃入口 M5.run() 可达、但生产入口 AgenticWriteWorkflow 未接线"
        "且未登记豁免 —— 能力对账失败（「收敛丢能力」复发风险）：\n  "
        + "\n  ".join(unexplained)
        + "\n修法：接线到生产入口，或在 _PARITY_EXEMPT 登记去向与理由。"
    )


def test_parity_mechanism_preconditions() -> None:
    """自检：能力对账机制本身有效（防判据写错 → 永远绿灯 / 豁免表腐化）。"""
    m5_caps, _ = _collect_run_chain(_parse("m5_write_chapter.py"), "M5WriteChapterWorkflow")
    _, ag_attrs = _collect_run_chain(_parse("agentic_write.py"), "AgenticWriteWorkflow")
    gap = m5_caps - ag_attrs
    assert len(m5_caps) >= 10, (
        f"仅提取到 {len(m5_caps)} 个 M5 能力 —— AST 判据可能失效（类名/结构已变？）"
    )
    stale = sorted(set(_PARITY_EXEMPT) - gap)
    assert not stale, (
        f"豁免表存在**失效条目**（已不在差集中，说明已接线或能力已删）：{stale} —— "
        "请从 _PARITY_EXEMPT 清理，避免豁免表腐化后失去对账意义"
    )
