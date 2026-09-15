"""状态所有权红线（架构 §3.7「项目状态的唯一读写门」）

现状（2026-09-12 实测）：``state.json`` 被 cli / core / workflows / web **四层
24 个文件**直接引用。架构文档称 ``web/state.py`` 是「项目状态唯一读写门」，
但它实际只是**接入层的适配**——cli 与 workflows 各自直写，所谓「唯一」名不副实。

为什么不一次性迁移：state.json 是运行中的小说项目的活状态，全量改写入口
风险过高（涉及正在续写的长篇任务）。故采用**棘轮**策略：

1. 先显式声明状态所有权者（合法直写）；
2. 其余层的直写点冻结在当前基线，只减不增；
3. 逐批把 cli / workflows 的直写收敛到 ``core/engine/state_machine.py``，
   每清偿一个文件就下调对应预算。

目标终态：``core/engine/state_machine.py`` 是 state.json 的唯一所有权者，
``web/state.py`` 是其在接入层的适配，其余层一律经所有权者访问。

────────────────────────────────────────────────────────────────
判据升级（2026-09-15，监管回溯缺口 G2 的处置）
────────────────────────────────────────────────────────────────
**旧判据** ``text.count("state.json")`` 把注释、docstring、日志/异常文案
**一并计数** → 实测 45 次命中里 **32 次是噪声（71%）**。

代价实证：``092e55f`` 给 ``payoff_script.py`` 补了正确的 ``degrade()``，
文案含 "state.json" → **22 分钟后** ``4bd2ec7`` 只能改文案
（commit message 自述「语义不变，文案改为…表达同一语义」）。
即：修复者被迫「对齐判据」而不是「对齐语义」——这是 churn 的主引擎。

**新判据**：AST 扫描，只数**路径字面量**（``_is_path_literal``），
文案/散文/文档串不再计分。

基线随之重定（同一份代码、两种判据）：
    非所有权者 17 文件 / 22 次  →  7 文件 / 7 次
（差额全部是注释与文档串，非直写点。）

⚠ **踩坑（必须保留的教训）**：``src/`` 下有 **5 个文件带 BOM**，
其中 ``workflows/pipeline/budget_planner.py`` 是**真实的 state.json 写入者**
（``self.project_dir / ".state" / "state.json"``）。``ast.parse`` 对 BOM 会抛
``invalid non-printable character U+FEFF`` —— 若照旧写法 ``except: continue``
**静默跳过整个文件**，红线就对它永久失明。故：
1. 一律用 ``encoding="utf-8-sig"`` 读取；
2. 解析失败**必须 FAIL 并列出文件名**，不许静默跳过
   （「失败被当成通过」是本项目最忌讳的缺陷族，见 AGENTS.md 第 9/15 条）。
"""

from __future__ import annotations

import ast
from pathlib import Path

AGENT_SRC = Path(__file__).resolve().parents[2] / "src" / "agent"

MARK = "state.json"

# 状态所有权者与必要工具：允许直接触碰 state.json
STATE_OWNERS = {
    "core/engine/state_machine.py",  # 状态机：state.json 的唯一所有权者
    "core/infra/atomic.py",  # 原子写工具（被所有权者调用）
    "core/infra/doctor.py",  # 健康诊断（只读 + 修复建议）
    "core/infra/dashboard_aggregator.py",  # 仪表盘聚合（只读投影）
    "core/engine/events.py",  # 进度事件落盘
    "core/story/injected_trope_store.py",  # 注入梗独立存储
    "web/state.py",  # 接入层适配（§3.7 项目状态读写门）
}

# 待收敛层基线（文件 -> state.json **路径引用**次数）：冻结，只减不增。
# 2026-09-15 判据由「文本出现次数」改为「AST 路径字面量」后重定：
# 旧基线 17 文件 / 22 次 → 新基线 7 文件 / 7 次（差额是注释与文档串）。
CONVERGE_BASELINE = {
    "cli/commands/cmd_list.py": 1,
    "cli/commands/reset_state.py": 1,
    "cli/commands/resume.py": 1,
    "cli/commands/rollback.py": 1,
    "cli/commands/status.py": 1,
    "workflows/pipeline/budget_planner.py": 1,
    "workflows/pipeline/plan_consistency.py": 1,
}

_MAX_PATH_LITERAL = 80


def _is_path_literal(value: str) -> bool:
    """判定一个字符串常量是否为「路径字面量」。

    判据（2026-09-15）：包含 ``state.json``、**不含任何空白**、长度可控。
    - ``"state.json"`` / ``".state/state.json"`` → 命中（路径）
    - ``"state.json 读取失败，回退下一级缺省"`` → 不命中（散文有空白）
    - 多行 docstring → 不命中（有空白/换行）

    这是「语义判据」的核心：区分**在构造路径**和**在写文案**。
    """
    return MARK in value and not any(c.isspace() for c in value) and len(value) <= _MAX_PATH_LITERAL


def count_state_refs(text: str) -> int:
    """纯函数：统计一段源码里 state.json **路径字面量**的出现次数。

    自带 BOM 容错（``removeprefix("\\ufeff")``）——纵深防御：即使调用方用
    ``utf-8`` 而非 ``utf-8-sig`` 读取，本函数也不会因 BOM 而抛异常，
    从而不会把「解析失败」误判成「没有写入点」（那会让红线失明）。
    """
    tree = ast.parse(text.removeprefix("\ufeff"))
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and _is_path_literal(node.value)
    )


def scan_sources() -> tuple[dict[str, int], list[str]]:
    """扫描全部源码。

    返回 ``(计数, 解析失败文件清单)``。
    **不吞解析异常** —— 失败清单交回调用方，由红线断言为空（显性化）。
    """
    counts: dict[str, int] = {}
    unparseable: list[str] = []
    for py in sorted(AGENT_SRC.rglob("*.py")):
        rel = py.relative_to(AGENT_SRC).as_posix()
        try:
            text = py.read_text(encoding="utf-8-sig")
        except (UnicodeDecodeError, OSError) as exc:
            unparseable.append(f"{rel}: 读取失败 {type(exc).__name__}")
            continue
        try:
            n = count_state_refs(text)
        except (SyntaxError, ValueError) as exc:
            unparseable.append(f"{rel}: 解析失败 {type(exc).__name__}: {exc}")
            continue
        if n:
            counts[rel] = n
    return counts, unparseable


def _state_json_path_refs() -> dict[str, int]:
    """兼容旧调用点：只返回计数（解析失败见 test_all_sources_parseable）。"""
    counts, _ = scan_sources()
    return counts


class TestJudgementIsSemantic:
    """判据本身的行为（防退化回字符串计数）。"""

    def test_prose_is_not_counted(self) -> None:
        """文案/日志/异常消息里提到 state.json **不得**计数。

        这是 092e55f → 4bd2ec7 那个假阳性的回归测试。
        """
        src = (
            "def f():\n"
            "    try:\n"
            "        pass\n"
            "    except Exception as e:\n"
            '        degrade("payoff.resolve.state", "state.json 读取失败，回退缺省", e)\n'
        )
        assert count_state_refs(src) == 0

    def test_comment_is_not_counted(self) -> None:
        src = "# 本模块不直写 state.json，只经 StateMachine 读\nx = 1\n"
        assert count_state_refs(src) == 0

    def test_docstring_is_not_counted(self) -> None:
        src = '"""模块说明：state.json 是项目活状态，唯一所有权者在 core。"""\n'
        assert count_state_refs(src) == 0

    def test_path_literal_is_counted(self) -> None:
        src = 'p = self.project_dir / ".state" / "state.json"\n'
        assert count_state_refs(src) == 1

    def test_open_call_is_counted(self) -> None:
        src = 'with open(d / "state.json", "w") as f:\n    pass\n'
        assert count_state_refs(src) == 1

    def test_bom_is_tolerated(self) -> None:
        """BOM 文件必须能解析（否则整个文件对红线失明）。

        实证：src/ 下 5 个文件带 BOM，其中 budget_planner.py 是真实写入者。
        """
        src = '\ufeffp = d / "state.json"\n'
        assert count_state_refs(src) == 1


class TestStateOwnership:
    def test_all_sources_parseable(self) -> None:
        """任何源码不可解析都必须显性失败（不得静默跳过 → 红线失明）。"""
        _, unparseable = scan_sources()
        assert not unparseable, (
            f"{len(unparseable)} 个源文件无法解析 —— 红线对它们会永久失明，"
            "必须先修（常见原因：BOM 未按 utf-8-sig 读取 / 语法错误）：\n"
            + "\n".join(f"  {u}" for u in unparseable[:20])
        )

    def test_bom_writer_is_visible(self) -> None:
        """带 BOM 的真实写入者必须被计到（否则等于红线漏看）。"""
        counts, _ = scan_sources()
        assert counts.get("workflows/pipeline/budget_planner.py", 0) >= 1, (
            "workflows/pipeline/budget_planner.py 含 state.json 路径字面量却未被计入 —— "
            "判据很可能退回了「静默跳过解析失败」的写法。"
        )

    def test_no_new_direct_state_writers(self) -> None:
        """禁止新增直写 state.json 的文件（所有权者除外）。"""
        counts, _ = scan_sources()
        violations = [
            f"  {f}: {n} 处（既非所有权者，也不在收敛基线内）"
            for f, n in sorted(counts.items())
            if f not in STATE_OWNERS and f not in CONVERGE_BASELINE
        ]
        assert not violations, (
            "新增了直写 state.json 的文件——状态应经 core/engine/state_machine.py 访问，"
            "不要另开门户：\n" + "\n".join(violations)
        )

    def test_converge_budget_never_grows(self) -> None:
        """棘轮：待收敛层的 state.json 路径引用次数只减不增。"""
        counts, _ = scan_sources()
        violations = [
            f"  {f}: 当前 {counts.get(f, 0)} > 基线 {budget}"
            for f, budget in CONVERGE_BASELINE.items()
            if counts.get(f, 0) > budget
        ]
        assert not violations, (
            "待收敛层的 state.json 直写增加（应逐步收敛到状态所有权者）：\n"
            + "\n".join(violations)
        )

    def test_baseline_not_stale(self) -> None:
        """清偿后必须同步下调基线（棘轮只减不增，且不许留僵尸条目）。"""
        counts, _ = scan_sources()
        stale = [
            f"  {f}: 基线 {b} 但当前 0（已清偿 → 请从 CONVERGE_BASELINE 删除）"
            for f, b in CONVERGE_BASELINE.items()
            if counts.get(f, 0) == 0
        ]
        assert not stale, "收敛基线存在僵尸条目：\n" + "\n".join(stale)
