"""能力对账红线（架构红线测试）

规则沿革
--------
**2026-09-11**（五灵破 ch181/182 章间矛盾复盘后新增）：写章入口从
``M5WriteChapterWorkflow.run()`` 收敛到 ``AgenticWriteWorkflow.run()`` 时，
G15 ``_archive_chapter`` hook 只留在废弃入口内，生产入口从未调用 ⇒
``ledger.json`` 永不落盘 ⇒ 上一章动态状态（物品/人数/动作/约定）对下一章不可见 ⇒
writer 自创细节 ⇒ 硬指标失败熔断。

**原实现**以「废弃入口 ``M5.run()`` 链路」为**基准**做差集对账
（差集必须 ⊆ ``_PARITY_EXEMPT``）。

**2026-09-16**（登记单 ``20260916_闸门信号可达性普查`` §三.C2）：废弃入口连同
``run()`` 一并删除 ⇒ 基准消失，故改为**契约清单**对账（方向反转、价值保留）：

    契约（``_REQUIRED_AGENTIC_CAPABILITIES``）⊆ 生产入口 run 链路实际调用名

—— 少接一项即在清单里裸奔（FAIL）。这同时修掉旧实现的一个盲区：旧的「整方法
豁免」粒度会把**方法内部的子能力缺口**当成"已接线"（实例：``_maybe_deslop``
整体登记为「已由 ``_run_deslop`` 承担」，而其内部 **L1 禁词硬拦截** 从未迁移，
生产路径从未跑过 —— 见 ``tests/test_l1_hard_block_reachability.py``）。

另两条独立红线不变：
1. 生产入口 run 链路必须调用 ``_archive_chapter``（防「收敛丢能力」复发）；
2. ``_archive_chapter`` 内 ``ledger.commit(...)`` 的 ``facts`` 与
   ``ContinuityHandoff(must_carry=...)`` 不得传字面量空列表（防占位实现回潮）。
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


def _collect_run_chain(tree: ast.Module, class_name: str) -> tuple[set[str], set[str]]:
    """返回 ``(self 私有方法调用集, 链路内所有属性调用名集)``。

    从 ``run`` 出发沿同类 ``self.<name>(...)`` 自调用下钻，收集整条链路。
    第二个集合含 ``m5._validate_evidence(...)`` 这类外部实例调用名 ——
    契约对账消费的就是它。
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


#: 生产入口**必备能力契约**：每项必须在 ``AgenticWriteWorkflow.run()`` 链路内被调用。
#: 少接一项 = 「收敛丢能力」复发；新增生产入口必备能力请同时登记在此（契约只增不减）。
_REQUIRED_AGENTIC_CAPABILITIES: dict[str, str] = {
    "_load_context": "7 步上下文装配（写前输入）",
    "_archive_chapter": "G15 连续性账本归档（2026-09-11 事故：曾只接废弃入口）",
    "_record_book_ledger": "书级台账（与 M5 同位）",
    "_save_chapter": "章节落盘",
    "_update_progress": "进度更新",
    "_build_evidence_chain": "E4 结构化依据链",
    "_validate_evidence": "F-E4.3 落盘前证据源校验",
    "_finalize_chapter_text": "canonical body 成文管线（落盘/门禁/指纹同产物）",
    "_extract_title": "章节标题提取",
    "_ensure_unique_title": "标题唯一性保障（占位/撞名就地重生）",
    "_run_deslop": "去 AI 味 + L1 禁词硬拦截（L1 段 2026-09-16 迁移）",
}


def test_production_entry_wires_archive_hook() -> None:
    """生产入口 AgenticWriteWorkflow.run() 链路必须调用 G15 归档 hook。"""
    tree = _parse("agentic_write.py")
    assert _reachable_from_run(tree, "AgenticWriteWorkflow", "_archive_chapter"), (
        "AgenticWriteWorkflow.run() 未调用 _archive_chapter —— 写章入口收敛时再次"
        "丢失连续性归档能力（能力对账红线），上一章动态状态将对下一章断供"
    )


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
# 能力契约对账（2026-09-16 取代「废弃入口基准」差集对账）
# ============================================================
def test_run_reaches_required_capabilities() -> None:
    """能力契约：生产入口 run 链路必须覆盖全部必备能力。

    机制：``_REQUIRED_AGENTIC_CAPABILITIES`` − ``AgenticWriteWorkflow.run()``
    链路内出现的调用名 = 缺口；缺口非空即 FAIL。
    → 取代「以废弃入口为基准」的差集对账（基准已随废弃物删除），
      且对账粒度细到**方法内部能力**，不再被整方法豁免掩盖。
    """
    _, ag_attrs = _collect_run_chain(_parse("agentic_write.py"), "AgenticWriteWorkflow")
    missing = sorted(set(_REQUIRED_AGENTIC_CAPABILITIES) - ag_attrs)
    assert not missing, (
        "以下能力已登记为生产入口必备、但 AgenticWriteWorkflow.run() 链路未接线 —— "
        "能力对账失败（「收敛丢能力」复发风险）：\n  "
        + "\n  ".join(f"{m}（{_REQUIRED_AGENTIC_CAPABILITIES[m]}）" for m in missing)
        + "\n修法：接线到生产入口；确属有意取舍请从契约中删除并写明理由与去向。"
    )


def test_contract_mechanism_preconditions() -> None:
    """自检：对账机制本身有效（防判据写错 → 永远绿灯）。"""
    _, ag_attrs = _collect_run_chain(_parse("agentic_write.py"), "AgenticWriteWorkflow")
    assert len(ag_attrs) >= 15, (
        f"仅提取到 {len(ag_attrs)} 个调用名 —— AST 判据可能失效（类名/结构已变？）"
    )
    assert len(_REQUIRED_AGENTIC_CAPABILITIES) >= 8, (
        "契约清单过短，对账意义不足（能力被移除时未同步清理清单？）"
    )
