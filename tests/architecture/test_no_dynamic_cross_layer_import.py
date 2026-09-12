"""R6 动态 import 红线：core 不得经 importlib / __import__ 反向依赖上层

编译期 import 由 test_redlines_agent.R6 静态扫描覆盖；本文件补**动态缺口**——
``importlib.import_module(X)`` / ``__import__(X)`` / ``importlib.__import__(X)``
的字符串字面量实参同样受分层依赖矩阵约束（hook_dispatcher 曾借 LEGACY_MAP /
点分 hook 规格从 core 运行期 import agent.workflows / agent.agents，2026-09-12 清零）。

- 静态：AST 扫描 src/agent/core 全部动态 import 调用点，字面量实参不得指向上层包；
- 运行时：hook_dispatcher 对指向上层的点分 hook 规格必须拒绝且不产生 import
  （见 tests/test_hook_dispatcher.py::test_dispatch_refuses_upper_layer_spec）。
"""
from __future__ import annotations

import ast
from pathlib import Path

AGENT_SRC = Path(__file__).resolve().parents[2] / "src" / "agent"

# R6：core 的动态 import 不得指向这些上层/接入层包（client 也在列——core 只经 base 抽象用 LLM）
FORBIDDEN_PREFIXES = (
    "agent.workflows",
    "agent.agents",
    "agent.cli",
    "agent.web",
    "agent.service",
    "agent.tasks",
    "agent.client",
)


def _dynamic_import_targets(path: Path) -> list[tuple[int, str]]:
    """AST 解析一个文件中所有动态 import 调用的字符串字面量实参，返回 (行号, 模块名)。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_import_module = (
            isinstance(func, ast.Attribute)
            and func.attr == "import_module"
            and isinstance(func.value, ast.Name)
            and func.value.id == "importlib"
        )
        is_builtin_import = isinstance(func, ast.Name) and func.id == "__import__"
        is_direct_import_module = (
            isinstance(func, ast.Attribute)
            and func.attr == "__import__"
            and isinstance(func.value, ast.Name)
            and func.value.id == "importlib"
        )
        if not (is_import_module or is_builtin_import or is_direct_import_module):
            continue
        if not node.args:
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            hits.append((node.lineno, arg.value))
    return hits


def test_core_dynamic_imports_never_target_upper_layers() -> None:
    """core/ 下任何动态 import 的字面量实参不得指向 agent 上层包"""
    violations: list[str] = []
    for p in (AGENT_SRC / "core").rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        for lineno, mod in _dynamic_import_targets(p):
            if mod.startswith(FORBIDDEN_PREFIXES) or mod in (
                "agent.workflows",
                "agent.agents",
                "agent.cli",
                "agent.web",
                "agent.service",
                "agent.tasks",
                "agent.client",
            ):
                rel = p.relative_to(AGENT_SRC).as_posix()
                violations.append(f"{rel}:{lineno} import_module({mod!r})")
    assert not violations, (
        "core 存在指向上层包的动态 import（R6 运行期违规，"
        "改由上层 register_genre_hook / 回调注入）：\n" + "\n".join(violations)
    )


def test_core_dynamic_import_call_sites_are_registered() -> None:
    """core/ 下动态 import 调用点清单冻结（新增调用点须显式登记并说明为何不违反 R6）"""
    ALLOWED = {
        # 点分 hook 规格解析：运行时已拒绝指向上层的 agent.* 规格（_FORBIDDEN_TOPS）
        "core/infra/hook_dispatcher.py",
        # `__import__("time")` 一次性时间戳取用（stdlib，与分层无关）
        "core/llmops/usage_reporter.py",
    }
    call_sites: set[str] = set()
    for p in (AGENT_SRC / "core").rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        if _has_dynamic_import_call(p):
            call_sites.add(p.relative_to(AGENT_SRC).as_posix())
    unexpected = call_sites - ALLOWED
    assert not unexpected, (
        f"core/ 出现新的动态 import 调用点（须登记进 ALLOWED 并论证不违反 R6）：{sorted(unexpected)}"
    )


def _has_dynamic_import_call(path: Path) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr in ("import_module", "__import__")
                and isinstance(func.value, ast.Name)
                and func.value.id == "importlib"
            ) or (isinstance(func, ast.Name) and func.id == "__import__"):
                return True
    return False
