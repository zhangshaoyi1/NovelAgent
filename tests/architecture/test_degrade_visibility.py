"""F-1 降级可见化红线（架构红线测试）

规则（与《两天提交分析与框架根因反思》F-1 对应）：
- 每个 ``except`` 块要么在 body 内调用 ``degrade(...)`` / ``logger.*`` / ``logging.*``，
  要么在 except 行标注 ``# noqa: SILENT_DEGRADE`` 显式豁免（存量豁免，增量拦截）；
- body 以 ``raise`` / ``return`` 结束视为主动处理（重新抛/提前返回），不算静默降级；
- 新增静默降级（无 degrade、无豁免、非 raise/return）→ FAIL。

目的：杜绝"悄悄不工作"（deslop/预算/legacy 路径等静默失效的复发）。

────────────────────────────────────────────────────────────────
判据升级（2026-09-15，监管回溯缺口 G2 的处置）
────────────────────────────────────────────────────────────────
**旧判据** ``text.count("SILENT_DEGRADE")`` 数**任意位置的字符串出现次数**，
包括 docstring / 日志文案。实测该线本来就基本准确（390 次里仅 1 处噪声，
来自 ``core/infra/degrade.py`` 自己的文档串），但**判据与语义不匹配**这一点
与 ``test_state_ownership`` 同源（同一类缺陷，两个显影）。

**新判据**：``tokenize`` 逐 token 扫描，**只数 COMMENT token** ——
豁免的本质就是「except 上的注释标记」，语义对齐后不再可能被文案污染。

★ 换判据时**挖出了一个更严重的存量问题**：旧写法
``except (SyntaxError, UnicodeDecodeError, OSError): continue`` 在遇到
**BOM 文件**时 ``ast.parse`` 抛 ``invalid non-printable character U+FEFF``
→ **整个文件被静默跳过**。``src/`` 下正好有 **5 个 BOM 文件**，
其中 4 个藏了 **9 处从未被审过的静默降级**：
``feedback_rewriter.py`` / ``m12_audit.py``(×3) / ``m20_analyze.py``(×3) /
``budget_planner.py``(×2)。**红线对它们失明了整整一段历史。**

处置（2026-09-15）：6 处真实失败接入 ``degrade()``；3 处属「预期跳过 / 重试循环
（最终由 for-else raise 上报）」转正为显式豁免并写明理由。

基线随之重定：390（旧判据，看不全）→ 399（新判据，含 BOM 文件）
→ **402**（+3 处转正豁免）。⚠ 这是**判据纠正后的一次性重定基，不是放宽**：
6 处真实静默降级已接 ``degrade()``（不计豁免）。此后棘轮纪律不变：只减不增。

⚠ BOM 同样要处理：未按 ``utf-8-sig`` 读取会让 ``tokenize``/``ast`` 失败 →
若静默 ``continue`` 则该文件对红线失明。故解析失败**必须 FAIL 并列出文件名**
（见 ``test_all_sources_parseable``）。
"""

from __future__ import annotations

import ast
import io
import tokenize
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


def comment_lines(text: str) -> set[int]:
    """返回含 ``SILENT_DEGRADE`` 的 **COMMENT token** 所在行号集合。

    只认注释 —— docstring / 字符串字面量里出现该字样**不算**豁免
    （那是「对齐判据而非对齐语义」的旧病，见模块 docstring）。
    """
    out: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text.removeprefix("\ufeff")).readline):
            if tok.type == tokenize.COMMENT and _EXEMPT_MARK in tok.string:
                out.add(tok.start[0])
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError):
        raise
    return out


def _read_sources() -> tuple[dict[str, str], list[str]]:
    """读取全部源文件（utf-8-sig），返回 (相对路径->文本, 读取失败清单)。"""
    texts: dict[str, str] = {}
    failed: list[str] = []
    for py in sorted(SRC.rglob("*.py")):
        rel = py.relative_to(SRC).as_posix()
        try:
            texts[rel] = py.read_text(encoding="utf-8-sig")
        except (UnicodeDecodeError, OSError) as exc:
            failed.append(f"{rel}: 读取失败 {type(exc).__name__}")
    return texts, failed


def count_exemptions() -> dict[str, int]:
    """统计每个源文件中**注释形式**的 SILENT_DEGRADE 豁免标记数。

    解析失败的文件不计入（由 test_all_sources_parseable 显性失败兜底）。
    """
    counts: dict[str, int] = {}
    texts, _ = _read_sources()
    for rel, text in texts.items():
        try:
            n = len(comment_lines(text))
        except Exception:
            continue
        if n:
            counts[rel] = n
    return counts


def find_violations() -> list[tuple[str, int, str]]:
    """返回违规清单 [(文件相对路径, 行号, except 行文本)]。"""
    out: list[tuple[str, int, str]] = []
    texts, _ = _read_sources()
    for rel, text in texts.items():
        try:
            tree = ast.parse(text, filename=rel)
        except (SyntaxError, ValueError):
            continue  # 由 test_all_sources_parseable 显性失败
        try:
            exempt_ln = comment_lines(text)
        except Exception:
            exempt_ln = set()
        lines = text.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            if not node.body:
                continue
            if _body_has_visible_handling(node.body):
                continue
            # 检查 except 语句区间（lineno..end_lineno）内**任何注释行**含豁免标记：
            # ExceptHandler.end_lineno 是块尾（body 末行），标记通常加在 except 头行；
            # 多行 except 标记可能加在最后物理行——区间扫描兜底两者。
            head = max(1, node.lineno)
            tail = min(len(lines), node.end_lineno or node.lineno)
            if any(ln in exempt_ln for ln in range(head, tail + 1)):
                continue
            out.append((rel, node.lineno, lines[node.lineno - 1].strip()[:90]))
    return out


class TestJudgementIsSemantic:
    """判据本身的行为（防退化回字符串计数）。"""

    def test_mark_in_comment_is_exemption(self) -> None:
        src = 'try:\n    pass\nexcept Exception:  # noqa: SILENT_DEGRADE - 存量\n    pass\n'
        assert comment_lines(src) == {3}

    def test_mark_in_docstring_is_not_exemption(self) -> None:
        """docstring 里提到 SILENT_DEGRADE 不算豁免（旧判据会误计）。"""
        src = '"""说明：本模块的 except 需标注 # noqa: SILENT_DEGRADE。"""\n'
        assert comment_lines(src) == set()

    def test_mark_in_message_is_not_exemption(self) -> None:
        src = 'x = "SILENT_DEGRADE 是豁免标记"\n'
        assert comment_lines(src) == set()

    def test_comment_count_matches_expectation(self) -> None:
        src = (
            "# SILENT_DEGRADE\n"
            'y = 1  # SILENT_DEGRADE - 理由\n'
            'z = "SILENT_DEGRADE"  # 不是标记\n'
        )
        assert comment_lines(src) == {1, 2}

    def test_bom_is_tolerated(self) -> None:
        src = '\ufeffx = 1  # SILENT_DEGRADE\n'
        assert comment_lines(src) == {1}


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


def test_all_sources_parseable() -> None:
    """任何源码不可解析都必须显性失败（不得静默跳过 → 红线失明）。"""
    texts, failed = _read_sources()
    unparseable = list(failed)
    for rel, text in texts.items():
        try:
            ast.parse(text, filename=rel)
            comment_lines(text)
        except (SyntaxError, ValueError, tokenize.TokenError, IndentationError) as exc:
            unparseable.append(f"{rel}: 解析失败 {type(exc).__name__}")
    assert not unparseable, (
        f"{len(unparseable)} 个源文件无法解析 —— 红线对它们会永久失明，必须先修"
        "（常见原因：BOM 未按 utf-8-sig 读取 / 语法错误）：\n"
        + "\n".join(f"  {u}" for u in unparseable[:20])
    )


# ── F-1 豁免棘轮（2026-09-12 建立）────────────────────────────────
# 背景：存量 405 处豁免此前「只增不减」——没有任何机制推动清偿，于是同一份
# 架构文档里出现双标：R6 分层矩阵零豁免（真红线），降级可见化 405 豁免（装饰性红线）。
# 这里冻结基线并强制单调递减：清偿一批就下调一批，新增降级点必须接 degrade()。
#
# 2026-09-15 判据由「文本出现次数」改为「COMMENT token 数」，并首次真正扫到 5 个 BOM 文件
# （旧写法 `except SyntaxError: continue` 把它们永久静默跳过 → 红线对这些文件失明）：
#     390（旧判据，看不见 BOM 文件）→ 399（新判据，含 5 个 BOM 文件）
#     → 402（其中 3 处「预期跳过 / 重试循环」转正为有理由的显式豁免）
# ⚠ 本次上调是**判据纠正后的一次性重定基**，不是放宽：另 6 处真实静默降级已接入
#   degrade()（不计入豁免）。此后棘轮纪律不变 —— 只减不增。
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
