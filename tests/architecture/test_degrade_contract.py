"""降级命名空间契约红线（G2 契约化 · 件 A）

────────────────────────────────────────────────────────────────
与 test_degrade_visibility.py 的分工
────────────────────────────────────────────────────────────────
- ``test_degrade_visibility.py``：**"有没有降级"** —— 每个 except 块要么可见
  处理（degrade/logger），要么显式豁免。管的是**存在性**（配额棘轮）。
- 本文件：**"降级有没有身份"** —— 每个 ``degrade(where, ...)`` 的 ``where``
  必须是注册表里登记过的命名空间。管的是**契约性**（成员资格）。

两者合起来才把「降级可见化」从**配额**升级为**契约**：
配额说"静默点少于 N 个即可"，契约说"每个降级点归属于谁、叫什么，有账可查"。

────────────────────────────────────────────────────────────────
判据（AST，双向差集）
────────────────────────────────────────────────────────────────
扫描全仓 ``degrade("...")`` 调用（**只认裸名 `degrade`**，不认 ``ev.degrade``
这类对象方法）：
- 字面量首参 → 取该字符串为命名空间；
- f-string 首参 → 取**静态前缀**（首个占位符之前的部分，去尾点）；
- 两者皆非（纯变量）→ 计入不可版本化清单 → FAIL。

双向差集：
    调用了但未登记  → FAIL（新增降级点必须登记进 DEGRADE_NAMESPACES）
    登记了但无调用  → FAIL（僵尸条目必须删除，否则契约表腐化）

为什么"僵尸条目"也要拦：注册表的价值在于**可枚举**。留下永不触发的条目，
等于让"系统里有哪些降级点"这个问题的答案重新失真 —— 与旧判据（自由字符串）
殊途同归。契约必须是**活的**。
"""

from __future__ import annotations

import ast
from pathlib import Path

from agent.core.infra.degrade_registry import (
    DEGRADE_NAMESPACE_PREFIXES,
    DEGRADE_NAMESPACES,
    is_registered,
    is_valid_namespace,
)

SRC = Path(__file__).resolve().parents[2] / "src" / "agent"


def _static_prefix(joined: ast.JoinedStr) -> str:
    """取 f-string 首个占位符之前的静态片段，去掉尾部点号。

    ``f"evaluator.score_fn.{name}"`` → ``"evaluator.score_fn"``
    ``f"{var}.x"``（无静态前缀）→ ``""``

    ⚠ 必须在**首个 FormattedValue 处截断**：``ast.JoinedStr.values`` 会把
    占位符之后的静态片段也列出来，若全量拼接则 ``f"a.{x}.b"`` 会得到
    ``"a..b"``（假命名空间）。
    """
    parts: list[str] = []
    for seg in joined.values:
        if isinstance(seg, ast.Constant) and isinstance(seg.value, str):
            parts.append(seg.value)
        else:
            break
    return "".join(parts).rstrip(".")


def collect_from_tree(
    tree: ast.AST, rel: str = "x.py"
) -> tuple[dict[str, list[str]], list[str]]:
    """从已解析的 AST 采集降级命名空间（**唯一判据来源**）。

    返回 ``(命名空间 -> [位置...], 不可静态提取的位置)``。
    ``collect_calls`` 与单元测试都用它，避免两套判据分叉。
    """
    namespaces: dict[str, list[str]] = {}
    dynamic: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # 只认裸 degrade(...)；ev.degrade(...) 是对象方法，不属本契约
        if not (isinstance(func, ast.Name) and func.id == "degrade"):
            continue
        if not node.args:
            dynamic.append(f"{rel}:{node.lineno}（无参数）")
            continue
        a0 = node.args[0]
        if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
            namespaces.setdefault(a0.value, []).append(f"{rel}:{node.lineno}")
        elif isinstance(a0, ast.JoinedStr):
            prefix = _static_prefix(a0)
            if prefix:
                namespaces.setdefault(prefix, []).append(f"{rel}:{node.lineno}")
            else:
                dynamic.append(f"{rel}:{node.lineno}（f-string 无静态前缀）")
        else:
            dynamic.append(f"{rel}:{node.lineno}（非字面量）")
    return namespaces, dynamic


def collect_calls() -> tuple[dict[str, list[str]], list[str], list[str]]:
    """扫描全仓 degrade() 调用。

    返回 ``(命名空间 -> [位置...], 不可静态提取的位置, 解析失败的文件名)``。
    """
    namespaces: dict[str, list[str]] = {}
    dynamic: list[str] = []
    failed: list[str] = []
    for py in sorted(SRC.rglob("*.py")):
        rel = py.relative_to(SRC).as_posix()
        try:
            text = py.read_text(encoding="utf-8-sig")
        except (UnicodeDecodeError, OSError) as exc:
            failed.append(f"{rel}: 读取失败 {type(exc).__name__}")
            continue
        try:
            tree = ast.parse(text.removeprefix("\ufeff"), filename=rel)
        except (SyntaxError, ValueError) as exc:
            failed.append(f"{rel}: 解析失败 {type(exc).__name__}: {exc}")
            continue
        ns, dyn = collect_from_tree(tree, rel)
        for k, v in ns.items():
            namespaces.setdefault(k, []).extend(v)
        dynamic.extend(dyn)
    return namespaces, dynamic, failed


class TestJudgementIsSemantic:
    """判据本身的行为（防退化）。用 collect_from_tree 同源验证。"""

    def _collect(self, source: str) -> tuple[dict[str, list[str]], list[str]]:
        tree = ast.parse(source.removeprefix("\ufeff"), filename="x.py")
        return collect_from_tree(tree, "x.py")

    def test_literal_is_collected(self) -> None:
        ns, dyn = self._collect("degrade('a.b.c', 'r')\n")
        assert ns == {"a.b.c": ["x.py:1"]} and not dyn

    def test_fstring_static_prefix_is_collected(self) -> None:
        ns, _ = self._collect("degrade(f'evaluator.score_fn.{name}', 'r')\n")
        assert ns == {"evaluator.score_fn": ["x.py:1"]}

    def test_object_method_is_not_collected(self) -> None:
        """``ev.degrade(...)`` 是证据对象的方法，不是统一降级出口。"""
        ns, dyn = self._collect("ev.degrade(reason)\n")
        assert ns == {} and dyn == []

    def test_pure_variable_is_dynamic(self) -> None:
        ns, dyn = self._collect("degrade(where, 'r')\n")
        assert ns == {} and len(dyn) == 1

    def test_bom_is_tolerated(self) -> None:
        ns, _ = self._collect("\ufeffdegrade('a.b', 'r')\n")
        assert ns == {"a.b": ["x.py:1"]}


def test_all_sources_parseable() -> None:
    """任何源码不可解析都必须显性失败（否则契约表对该文件永久失明）。"""
    _, _, failed = collect_calls()
    assert not failed, (
        f"{len(failed)} 个源文件无法解析 —— 降级契约红线对它们会永久失明：\n"
        + "\n".join(f"  {f}" for f in failed[:20])
    )


def test_every_degrade_call_is_registered() -> None:
    """调用了但未登记 → FAIL（新增降级点必须登记）。"""
    namespaces, _, _ = collect_calls()
    unregistered = [
        f"  {ns}  ← {', '.join(locs[:3])}"
        for ns, locs in sorted(namespaces.items())
        if not is_registered(ns)
    ]
    assert not unregistered, (
        "发现未登记的降级命名空间（G2 契约：每个 degrade() 的 where 必须登记）——\n"
        "请在 src/agent/core/infra/degrade_registry.py 的 DEGRADE_NAMESPACES "
        "补登记（含归属模块）：\n" + "\n".join(unregistered)
    )


def test_no_dynamic_namespaces() -> None:
    """命名空间必须可静态提取（字面量或 f-string 静态前缀）。"""
    _, dynamic, _ = collect_calls()
    assert not dynamic, (
        "发现无法静态提取的降级命名空间（纯变量）—— 契约表无法覆盖，"
        "请改为字面量或带静态前缀的 f-string：\n" + "\n".join(f"  {d}" for d in dynamic[:20])
    )


def test_namespace_syntax() -> None:
    """命名空间必须符合 ``<域>.<子域>...`` 语法（小写/数字/下划线）。"""
    bad = [ns for ns in DEGRADE_NAMESPACES if not is_valid_namespace(ns)]
    bad += [ns for ns in DEGRADE_NAMESPACE_PREFIXES if not is_valid_namespace(ns)]
    assert not bad, "命名空间语法不合规（应为小写点分标识符）：\n" + "\n".join(
        f"  {b}" for b in sorted(bad)
    )


def test_no_zombie_entries() -> None:
    """登记了但无调用 → FAIL（僵尸条目使契约表失真）。"""
    namespaces, _, _ = collect_calls()
    used = set(namespaces)
    zombies = sorted(
        ns
        for ns in DEGRADE_NAMESPACES
        if not any(u == ns or u.startswith(ns + ".") for u in used)
    )
    assert not zombies, (
        "注册表存在僵尸条目（已登记但全仓无对应 degrade() 调用）——\n"
        "若降级点已删除，请从 DEGRADE_NAMESPACES 移除；若改名，请同步更新：\n"
        + "\n".join(f"  {z}" for z in zombies)
    )


def test_prefix_entries_are_used() -> None:
    """前缀条目必须确实被某个动态命名空间使用。"""
    namespaces, _, _ = collect_calls()
    used = set(namespaces)
    unused = sorted(
        p
        for p in DEGRADE_NAMESPACE_PREFIXES
        if not any(u == p or u.startswith(p + ".") for u in used)
    )
    assert not unused, (
        "前缀注册条目无任何 f-string 命名空间使用（僵尸前缀）：\n"
        + "\n".join(f"  {u}" for u in unused)
    )


def test_registry_is_not_empty() -> None:
    """注册表非空（防误清空导致契约空心化）。"""
    assert DEGRADE_NAMESPACES, "DEGRADE_NAMESPACES 为空 —— 契约表被误清空"
