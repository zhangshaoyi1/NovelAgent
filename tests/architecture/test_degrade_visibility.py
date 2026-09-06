"""F-1 降级可见化红线（架构红线测试）

规则（与《两天提交分析与框架根因反思》F-1 对应）：
- 每个 ``except`` 块要么在 body 内调用 ``degrade(...)`` / ``logger.*`` / ``logging.*``，
  要么在 except 行标注 ``# noqa: SILENT_DEGRADE`` 显式豁免（存量豁免，增量拦截）；
- body 以 ``raise`` / ``return`` 结束视为主动处理（重新抛/提前返回），不算静默降级；
- 新增静默降级（无 degrade、无豁免、非 raise/return）→ FAIL。

目的：杜绝"悄悄不工作"（deslop/预算/legacy 路径等静默失效的复发）。
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"

# 允许的降级签名调用：degrade(...) 或 logger./logging. 开头
_LOG_PREFIXES = ("logger", "logging")
_DEGRADE_NAMES = {"degrade"}
_EXEMPT_MARK = "SILENT_DEGRADE"


def _call_full_name(func: ast.AST) -> str:
    """把 ast.Call 的 func 解析成完整名字（如 logger.warning / self.logger.info）。"""
    parts: list[str] = []
    node = func
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _body_has_visible_handling(body: list[ast.stmt]) -> bool:
    """body 内是否有 degrade/logging 调用，或以 raise/return 主动结束。"""
    for stmt in body:
        if isinstance(stmt, ast.Raise) or isinstance(stmt, ast.Return):
            return True
        # 遍历该语句内的所有调用（含赋值右侧、表达式）
        for node in ast.walk(stmt):
            if isinstance(node, ast.Call):
                name = _call_full_name(node.func)
                first = name.split(".")[0]
                if name in _DEGRADE_NAMES or first in _LOG_PREFIXES:
                    return True
    return False


def find_violations() -> list[tuple[str, int, str]]:
    """返回违规清单 [(文件相对路径, 行号, except 行文本)]。"""
    out: list[tuple[str, int, str]] = []
    for py in sorted(SRC.rglob("*.py")):
        try:
            text = py.read_text(encoding="utf-8")
            tree = ast.parse(text, filename=str(py))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue  # 语法/编码问题由其他测试负责
        lines = text.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            if not node.body:
                continue
            if _body_has_visible_handling(node.body):
                continue
            # 取 except 语句最后物理行（支持多行 except）检查豁免标记
            end_line = node.end_lineno or node.lineno
            line_text = lines[end_line - 1] if end_line <= len(lines) else ""
            if _EXEMPT_MARK in line_text:
                continue
            out.append((str(py.relative_to(SRC)), node.lineno, lines[node.lineno - 1].strip()[:90]))
    return out


def test_no_silent_degrades() -> None:
    """存量已豁免、增量被拦截：违规清单必须为空。"""
    violations = find_violations()
    assert not violations, (
        f"发现 {len(violations)} 处无声降级（except 块未调用 degrade/logging，"
        f"且 except 行未标注 # noqa: {_EXEMPT_MARK}）——请接入 degrade() 或显式豁免：\n"
        + "\n".join(f"  {f}:{ln}  {txt}" for f, ln, txt in violations[:20])
    )


def test_degrade_tool_exists() -> None:
    """降级工具必须存在（统一出口）。"""
    tool = SRC / "agent" / "core" / "infra" / "degrade.py"
    assert tool.exists(), "agent/core/infra/degrade.py 缺失（F-1 统一降级出口）"
