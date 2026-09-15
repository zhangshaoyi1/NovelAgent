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
import re
import tokenize
import warnings
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
#
# ⚠ 2026-09-15 二次变更（G2 件 D）：**总数/单文件预算从「闸门」降为「看板」**。
# 原因：件 B 落地后，**带 reason=/ref= 的合规新豁免**与「总数 ≤ 402」直接冲突 ——
# 一个合格的豁免会被配额误杀。约束的正确形态是**契约**（可追溯性），不是配额
# （数量）。故：
#   - 闸门 → `BARE_EXEMPTION_BUDGET`（未引用 reason 的豁免数，只减不增，
#     见 test_new_exemptions_must_cite_reason）；
#   - 看板 → 本节的 TOTAL / BY_FILE 预算，超出只 warnings.warn，**不 FAIL**。
# 与件 C（state 所有权计数降为看板）同一决策：**契约取代配额**。
DEGRADE_EXEMPTION_BUDGET = 402  # 看板软上限（不参与判定）

# 主链路重点盯盘对象（写作 / 评估 / 流水线）：本地告警，防"总量在降、关键路径在涨"。
DEGRADE_EXEMPTION_BUDGET_BY_FILE = {
    "agent/workflows/writing/agentic_write.py": 19,
    "agent/workflows/pipeline/agentic_pipeline.py": 15,
    "agent/agents/planner.py": 12,
    "agent/agents/evaluator_dims.py": 9,
    "agent/cli/commands/autowrite.py": 11,
}


def test_exemption_count_dashboard() -> None:
    """**看板**：豁免总数超软上限只告警（不 FAIL）。

    实际闸门是 BARE_EXEMPTION_BUDGET（件 B）：合规的新豁免（带 reason=/ref=）
    允许让总数上升，配额不再是约束形态。
    """
    total = sum(count_exemptions().values())
    if total > DEGRADE_EXEMPTION_BUDGET:
        warnings.warn(
            f"[看板] 降级豁免总数 {total} 超过软上限 {DEGRADE_EXEMPTION_BUDGET}"
            "（不 FAIL，仅供清偿盯盘）",
            stacklevel=1,
        )


def test_main_path_exemption_dashboard() -> None:
    """**看板**：主链路单文件豁免超预算只告警（不 FAIL）。"""
    counts = count_exemptions()
    over = [
        f"{f}: {counts.get(f, 0)} > {budget}"
        for f, budget in DEGRADE_EXEMPTION_BUDGET_BY_FILE.items()
        if counts.get(f, 0) > budget
    ]
    if over:
        warnings.warn(
            "[看板] 主链路降级豁免超预算（写作/评估路径优先清偿，不 FAIL）：\n  "
            + "\n  ".join(over),
            stacklevel=1,
        )


# ══════════════════════════════════════════════════════════════════
# 豁免契约（G2 契约化 · 件 B，2026-09-15）
# ══════════════════════════════════════════════════════════════════
# 上面的预算棘轮管的是**数量**（配额）；本节管的是**可追溯性**（契约）。
#
# 旧状态：``# noqa: SILENT_DEGRADE`` 是**零成本**标记 —— 加完即过，
# 理由靠自觉、不可机检。这与 AGENTS #11「别把过扫描当修复」同病：
# 约束没有指向任何责任人 / 文档，等于把"我确认这里安全"写成了匿名便条。
#
# 新契约（**增量收紧**，存量 402 条走棘轮 grandfather）：
#   新豁免必须写成 ``# noqa: SILENT_DEGRADE reason=<枚举> [ref=<登记单>]``
#   - ``reason`` 必填，取值须在 ``EXEMPTION_REASONS`` 内（可机检的语义分类）；
#   - ``ref`` 在 reason ∈ ``REASONS_REQUIRING_REF`` 时必填 —— 这些理由
#     **可能掩盖真 bug**（尽力而为 / 可选特性），必须指向一份有人签字的登记单；
#   - 若给了 ``ref``，其指向的文件必须存在于 ``项目文档/优化/``。
#
# 为什么用棘轮而非一次性全量改造：存量 402 条是历史债，一次性重写理由
# 只会产出 402 条敷衍文本（那正是"对齐判据不对齐语义"）。让**新增**先合规，
# 存量随清偿自然下降，与 DEGRADE_EXEMPTION_BUDGET 同节奏。

# reason 语义分类（闭集，可机检）
EXEMPTION_REASONS = {
    "expected-skip",  # 预期内的跳过（文件不存在 / 进程已退出 / 空输入）
    "retry-loop",  # 重试循环体内，最终失败由外层 raise 上报
    "best-effort",  # 尽力而为的旁路，失败不影响主流程（★ 需 ref）
    "logging-only",  # 仅日志 / 事件 / 落盘留痕失败
    "optional-feature",  # 可选特性 / 增强 / 优化失败（★ 需 ref）
    "process-gone",  # 目标进程已退出 / 并发取消
}

# 这些 reason 可能掩盖真 bug → 必须引用登记单
REASONS_REQUIRING_REF = {"best-effort", "optional-feature"}

# 登记单目录（相对 agent/；AGENTS.md 规定结构性改动须先在 项目文档/优化/ 登记）
_OPTIMIZATION_DIR = Path(__file__).resolve().parents[3] / "项目文档" / "优化"

_REASON_RE = re.compile(r"\breason\s*=\s*([A-Za-z][A-Za-z0-9_-]*)")
_REF_RE = re.compile(r"\bref\s*=\s*([^\s,)]+)")

# 存量「未引用 reason」的豁免数（棘轮：只减不增）。
# 2026-09-15 建线时实测 = 402（= DEGRADE_EXEMPTION_BUDGET，二者同批清偿）。
BARE_EXEMPTION_BUDGET = 402


def exemption_comments() -> list[tuple[str, int, str]]:
    """返回全部豁免注释 ``[(相对路径, 行号, 注释原文), ...]``（COMMENT token）。"""
    out: list[tuple[str, int, str]] = []
    texts, _ = _read_sources()
    for rel, text in texts.items():
        try:
            toks = tokenize.generate_tokens(
                io.StringIO(text.removeprefix("\ufeff")).readline
            )
        except (tokenize.TokenError, IndentationError, SyntaxError, ValueError):
            continue  # 由 test_all_sources_parseable 显性失败
        for tok in toks:
            if tok.type == tokenize.COMMENT and _EXEMPT_MARK in tok.string:
                out.append((rel, tok.start[0], tok.string))
    return out


def parse_exemption(comment: str) -> tuple[str | None, str | None]:
    """解析豁免注释里的 ``reason=`` / ``ref=``（返回 ``(reason, ref)``）。"""
    m_r = _REASON_RE.search(comment)
    m_f = _REF_RE.search(comment)
    return (m_r.group(1) if m_r else None, m_f.group(1) if m_f else None)


class TestExemptionContractJudgement:
    """契约判据本身的行为。"""

    def test_parse_plain(self) -> None:
        assert parse_exemption("# noqa: SILENT_DEGRADE - 存量") == (None, None)

    def test_parse_full(self) -> None:
        c = "# noqa: SILENT_DEGRADE reason=best-effort ref=20260915_x.md"
        assert parse_exemption(c) == ("best-effort", "20260915_x.md")

    def test_parse_reason_only(self) -> None:
        c = "# noqa: SILENT_DEGRADE reason=expected-skip"
        assert parse_exemption(c) == ("expected-skip", None)


def test_exemption_reason_values_are_known() -> None:
    """``reason=`` 若给出，取值必须在闭集内（防自由发挥）。"""
    bad = [
        f"  {rel}:{ln}  reason={reason}"
        for rel, ln, c in exemption_comments()
        for reason, _ in [parse_exemption(c)]
        if reason is not None and reason not in EXEMPTION_REASONS
    ]
    assert not bad, (
        "豁免 reason 取值不在 EXEMPTION_REASONS 闭集内：\n" + "\n".join(bad)
    )


def test_ambiguous_reasons_must_cite_registration() -> None:
    """可能掩盖真 bug 的 reason 必须带 ``ref=``，且指向存在的登记单。"""
    missing = [
        f"  {rel}:{ln}  reason={reason}（缺 ref=）"
        for rel, ln, c in exemption_comments()
        for reason, ref in [parse_exemption(c)]
        if reason in REASONS_REQUIRING_REF and not ref
    ]
    assert not missing, (
        "以下豁免使用了「可能掩盖真 bug」的 reason 却未引用登记单 ——\n"
        "此类豁免必须指向 项目文档/优化/ 下的一份登记单（含同类点位清单）：\n"
        + "\n".join(missing)
    )


def test_cited_registration_exists() -> None:
    """``ref=`` 指向的登记单必须真实存在（悬空引用 = FAIL）。"""
    dangling = [
        f"  {rel}:{ln}  ref={ref}"
        for rel, ln, c in exemption_comments()
        for _, ref in [parse_exemption(c)]
        if ref and not (_OPTIMIZATION_DIR / ref).exists()
    ]
    assert not dangling, (
        f"豁免引用了不存在的登记单（对照目录 {_OPTIMIZATION_DIR}）——\n"
        "请先在 项目文档/优化/ 建立登记单再引用：\n" + "\n".join(dangling)
    )


def test_new_exemptions_must_cite_reason() -> None:
    """棘轮：未引用 ``reason=`` 的豁免总数只减不增（新增必须带 reason）。"""
    bare = sum(1 for _, _, c in exemption_comments() if parse_exemption(c)[0] is None)
    assert bare <= BARE_EXEMPTION_BUDGET, (
        f"未引用 reason 的降级豁免 {bare} 超过预算 {BARE_EXEMPTION_BUDGET}（棘轮：只减不增）。\n"
        "新增豁免请写成：\n"
        f"    # noqa: {_EXEMPT_MARK} reason=<{'|'.join(sorted(EXEMPTION_REASONS))}>"
        " [ref=<登记单>]\n"
        "（reason ∈ {best-effort, optional-feature} 时 ref 必填）"
    )

