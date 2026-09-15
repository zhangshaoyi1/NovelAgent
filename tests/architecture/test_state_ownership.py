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

────────────────────────────────────────────────────────────────
契约升级（2026-09-15，G2 契约化 · 件 C）
────────────────────────────────────────────────────────────────
判据语义化只解决「数得准」，没解决「管什么」。旧的 `CONVERGE_BASELINE`
是**每文件引用次数**的配额（"每文件 ≤ 1 次"）—— 它拦住的是**数量**，
拦不住**资格**：把 7 处写入集中到一个无权模块，计数不变而所有权崩塌。

新契约把「计数」换成「成员资格」：

    STATE_OWNERSHIP: 状态路径 → 允许写入该路径的模块集合（业主）
    STATE_OWNERSHIP_TOLERATED: 尚未收敛的旁路 —— 每条必须**具名**
        （理由 + 收敛目标），而不是一个数字

闸门 = 成员资格 + 容忍条目必备理由 + 无僵尸条目 + 容忍**条目数**棘轮。
引用**次数**降为看板（`reference_counts()`，只作信息上报，不再单独致 FAIL）。

为什么容忍清单要"具名"：数字（"7 个文件 ≤ 1 次"）不告诉你**为什么允许**、
**谁来收**。具名条目把这两件事写死在契约里 —— 与 `# noqa` 的
`reason=`/`ref=` 是同一手法（G2 件 B）。
"""

from __future__ import annotations

import ast
import warnings
from pathlib import Path

AGENT_SRC = Path(__file__).resolve().parents[2] / "src" / "agent"

MARK = "state.json"

# ── 契约（2026-09-15 件 C）：状态路径 → 允许写入的模块集合 ──────────
# 注意：这里的"路径"是**逻辑路径**（判据命中用的子串），不是文件系统路径。
STATE_PATH = ".state/state.json"

STATE_OWNERSHIP: dict[str, set[str]] = {
    STATE_PATH: {
        "core/engine/state_machine.py",  # 状态机：state.json 的唯一所有权者
        "core/infra/atomic.py",  # 原子写工具（被所有权者调用）
        "core/infra/doctor.py",  # 健康诊断（只读 + 修复建议）
        "core/infra/dashboard_aggregator.py",  # 仪表盘聚合（只读投影）
        "core/engine/events.py",  # 进度事件落盘
        "core/story/injected_trope_store.py",  # 注入梗独立存储
        "web/state.py",  # 接入层适配（§3.7 项目状态读写门）
    },
}

# ── 容忍清单（尚未收敛的旁路写入）────────────────────────────────
# 契约要求：每条必须**具名**（reason=为什么暂时允许 / target=收敛到哪），
# 不接受"数字配额"。棘轮对象是**条目数**（= 待收敛文件的个数），
# 而不是"引用次数"——后者已降为看板（reference_counts）。
STATE_OWNERSHIP_TOLERATED: dict[str, dict[str, str]] = {
    "cli/commands/cmd_list.py": {
        "path": STATE_PATH,
        "reason": "列表命令直读 state.json 展示项目清单，尚未改走 StateMachine 只读接口",
        "target": "core/engine/state_machine.py 只读门（read_snapshot）",
    },
    "cli/commands/reset_state.py": {
        "path": STATE_PATH,
        "reason": "重置命令直接覆写初始状态，属 CLI 特权路径",
        "target": "core/engine/state_machine.py::reset()",
    },
    "cli/commands/resume.py": {
        "path": STATE_PATH,
        "reason": "续写命令读取断点状态，尚未改走统一只读接口",
        "target": "core/engine/state_machine.py 只读门（read_snapshot）",
    },
    "cli/commands/rollback.py": {
        "path": STATE_PATH,
        "reason": "回滚命令需读取并改写章节游标，属 CLI 特权路径",
        "target": "core/engine/state_machine.py::rollback()",
    },
    "cli/commands/status.py": {
        "path": STATE_PATH,
        "reason": "状态展示命令直读活状态，尚未改走只读投影",
        "target": "core/infra/dashboard_aggregator.py 投影",
    },
    "workflows/pipeline/budget_planner.py": {
        "path": STATE_PATH,
        "reason": "预算规划需在规划阶段写入派生字段（route/curve 锚点）",
        "target": "core/engine/state_machine.py 派生状态写接口",
    },
    "workflows/pipeline/plan_consistency.py": {
        "path": STATE_PATH,
        "reason": "规划一致性核对直接对照活状态，尚未改走只读接口",
        "target": "core/engine/state_machine.py 只读门（read_snapshot）",
    },
}

# 棘轮：容忍**条目数**（待收敛文件个数）只减不增。建线时实测 = 7。
TOLERATED_ENTRIES_BUDGET = 7

# 看板软上限：旁路引用**次数**（建线时实测 = 7）。超出只告警，不 FAIL。
TOLERATED_REFS_SOFT_CEILING = 7

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


# ── 契约查询 ────────────────────────────────────────────────────────


def owners_for(path: str) -> set[str]:
    """该状态路径的合法写入者（业主）集合。"""
    return STATE_OWNERSHIP.get(path, set())


def is_owner(path: str, module: str) -> bool:
    return module in owners_for(path)


def tolerated_refs_soft_ceiling() -> int:
    """旁路引用的软上限（看板用，不参与判定）。"""
    return TOLERATED_REFS_SOFT_CEILING


def reference_counts() -> dict[str, int]:
    """**看板指标**：每文件的 state.json 路径字面量引用次数。

    ⚠ 仅作信息上报，**不再单独致 FAIL**（旧 `CONVERGE_BASELINE` 计数闸门已移除）。
    契约的闸门是**成员资格**（见 TestStateOwnershipContract）。
    """
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


class TestOwnershipContractJudgement:
    """契约判据本身的行为。"""

    def test_owner_lookup(self) -> None:
        assert is_owner(STATE_PATH, "core/engine/state_machine.py")
        assert not is_owner(STATE_PATH, "cli/commands/status.py")

    def test_all_tolerated_entries_declare_their_path(self) -> None:
        """容忍条目必须声明它碰的是哪个受监管路径。"""
        assert all(e.get("path") for e in STATE_OWNERSHIP_TOLERATED.values())

    def test_tolerated_paths_are_tracked(self) -> None:
        """容忍条目声明的路径必须在 STATE_OWNERSHIP 中有业主。"""
        unknown = sorted(
            f"  {f}: path={e.get('path')}"
            for f, e in STATE_OWNERSHIP_TOLERATED.items()
            if e.get("path") not in STATE_OWNERSHIP
        )
        assert not unknown, "容忍条目引用了未登记的受监管路径：\n" + "\n".join(unknown)


class TestStateOwnershipContract:
    """成员资格契约（取代旧的「每文件计数」配额）。"""

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

    def test_writers_must_be_owners_or_tolerated(self) -> None:
        """**契约闸门**：触碰受监管状态的模块，必须是业主或被具名容忍。"""
        counts, _ = scan_sources()
        violations = [
            f"  {f}: {n} 处（既非 {STATE_PATH} 的业主，也不在容忍清单内）"
            for f in sorted(counts)
            if not is_owner(STATE_PATH, f) and f not in STATE_OWNERSHIP_TOLERATED
        ]
        assert not violations, (
            f"新增了触碰 {STATE_PATH} 的文件——状态应经 core/engine/state_machine.py 访问；\n"
            "确需临时旁路，请在 STATE_OWNERSHIP_TOLERATED 具名登记（reason + target）：\n"
            + "\n".join(violations)
        )

    def test_tolerated_entries_are_justified(self) -> None:
        """容忍条目必须写明理由与收敛目标（不接受匿名容忍）。"""
        bad = [
            f"  {f}: reason={'有' if e.get('reason') else '缺'} "
            f"target={'有' if e.get('target') else '缺'}"
            for f, e in sorted(STATE_OWNERSHIP_TOLERATED.items())
            if not (e.get("reason") and e.get("target"))
        ]
        assert not bad, (
            "容忍条目缺少 reason/target —— 容忍是**有意为之**，必须能回答"
            "「为什么暂时允许」与「收敛到哪」：\n" + "\n".join(bad)
        )

    def test_no_zombie_tolerance(self) -> None:
        """已清偿的容忍条目必须删除（僵尸条目使契约失真）。"""
        counts, _ = scan_sources()
        stale = [
            f"  {f}（已不再引用 {STATE_PATH} → 请从 STATE_OWNERSHIP_TOLERATED 删除）"
            for f in STATE_OWNERSHIP_TOLERATED
            if counts.get(f, 0) == 0
        ]
        assert not stale, "容忍清单存在僵尸条目：\n" + "\n".join(stale)

    def test_tolerated_entry_count_never_grows(self) -> None:
        """棘轮：容忍**条目数**（待收敛文件个数）只减不增。"""
        n = len(STATE_OWNERSHIP_TOLERATED)
        assert n <= TOLERATED_ENTRIES_BUDGET, (
            f"待收敛文件数 {n} 超过预算 {TOLERATED_ENTRIES_BUDGET}（棘轮：只减不增）。\n"
            "清偿一个就把对应条目从 STATE_OWNERSHIP_TOLERATED 删除并下调本预算。"
        )

    def test_reference_counts_dashboard(self) -> None:
        """**看板**：引用次数不参与判定，超软上限只告警（不 FAIL）。

        旧实现把「每文件引用次数」当闸门，属于配额型监管：把一个无权模块的
        多处写入集中起来，计数不变而所有权已崩塌。计数降级为看板后，
        真正的闸门是成员资格。
        """
        counts = reference_counts()
        bypass_refs = sum(n for f, n in counts.items() if f in STATE_OWNERSHIP_TOLERATED)
        if bypass_refs > TOLERATED_REFS_SOFT_CEILING:
            warnings.warn(
                f"[看板] 旁路引用次数 {bypass_refs} 超过软上限 "
                f"{TOLERATED_REFS_SOFT_CEILING}（不 FAIL，仅供收敛盯盘）",
                stacklevel=1,
            )
