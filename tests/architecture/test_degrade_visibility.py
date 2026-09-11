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
            # 检查 except 语句区间（lineno..end_lineno）内**任何一行**含豁免标记：
            # ExceptHandler.end_lineno 是块尾（body 末行），标记通常加在 except 头行；
            # 多行 except 标记可能加在最后物理行——区间扫描兜底两者。
            head = max(1, node.lineno)
            tail = min(len(lines), node.end_lineno or node.lineno)
            if any(_EXEMPT_MARK in lines[i - 1] for i in range(head, tail + 1)):
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


# ── F-1 豁免棘轮（2026-09-12 建立）────────────────────────────────
# 背景：存量 405 处豁免此前「只增不减」——没有任何机制推动清偿，于是同一份
# 架构文档里出现双标：R6 分层矩阵零豁免（真红线），降级可见化 405 豁免（装饰性红线）。
# 这里冻结基线并强制单调递减：清偿一批就下调一批，新增降级点必须接 degrade()。
DEGRADE_EXEMPTION_BUDGET = 402

# 主链路重点清偿对象（写作 / 评估 / 流水线）：单独设上限，
# 防止「总量在降、关键路径却在涨」被总数掩盖。
DEGRADE_EXEMPTION_BUDGET_BY_FILE = {
    "agent/workflows/writing/agentic_write.py": 19,
    "agent/workflows/pipeline/agentic_pipeline.py": 15,
    "agent/agents/planner.py": 12,
    "agent/agents/evaluator_dims.py": 9,
    "agent/cli/commands/autowrite.py": 11,
}


def count_exemptions() -> dict[str, int]:
    """统计每个源文件中的 SILENT_DEGRADE 豁免标记数。"""
    counts: dict[str, int] = {}
    for py in sorted(SRC.rglob("*.py")):
        try:
            text = py.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        n = text.count(_EXEMPT_MARK)
        if n:
            counts[py.relative_to(SRC).as_posix()] = n
    return counts


def test_exemption_budget_never_grows() -> None:
    """棘轮：豁免总数只减不增。"""
    counts = count_exemptions()
    total = sum(counts.values())
    assert total <= DEGRADE_EXEMPTION_BUDGET, (
        f"降级豁免总数 {total} 超过预算 {DEGRADE_EXEMPTION_BUDGET}（F-1 棘轮：只减不增）。\n"
        f"新增降级点请接入 degrade()，不要新增 # noqa: {_EXEMPT_MARK}；"
        f"清偿后请同步下调 DEGRADE_EXEMPTION_BUDGET。"
    )


def test_exemption_budget_by_file() -> None:
    """主链路豁免不得在总量掩护下局部恶化。"""
    counts = count_exemptions()
    violations = [
        f"  {f}: 当前 {counts.get(f, 0)} > 预算 {budget}"
        for f, budget in DEGRADE_EXEMPTION_BUDGET_BY_FILE.items()
        if counts.get(f, 0) > budget
    ]
    assert not violations, (
        "主链路降级豁免超预算（写作/评估路径优先清偿）：\n" + "\n".join(violations)
    )
