"""红线：接线齐备 ≠ 能力已生效（D 项，2026-09-19）。

## 这个文件守什么

立项判断是「**规划层三处评审闸门全缺**」（全仓搜 ``design_review|plan_review``
= 0 命中）。取证推翻了它：能力叫 ``plan_critic``，**已实现 631 行、两处接线
齐备且可达**；真实缺陷是——**16 个项目零台账（从未在生产执行过一次）**。

⇒ 三条红线把「**接线存在**」与「**能力已生效**」强行区分开：

====================  ==========================================================
D1 无数据 ≠ 通过      台账缺失/空 ⇒ 消费端必须拿到 ``no_data``/``empty``，
                      **不得**被解读为「评审通过、规划无问题」（纪律 #1）
D2 接线不得孤儿化      ``review_plan`` 的**两处**调用点必须同时在位；
                      摘掉任一 ⇒ FAIL（防「接线被无意删除 ⇒ 静默退化为零评审」）
D3 零调用点须豁免      新增**模块级 public 生产函数**若全仓零调用点、
                      且不在豁免表内 ⇒ FAIL（把纪律 #7 机器化；
                      覆盖 ``l1_block`` / ``LearningImitationMiner`` /
                      ``SupervisorEngine`` 同族）
====================  ==========================================================

## ★ 本文件自身的方法论纪律（记录一次真实翻车）

取证 ``plan_critic`` 时我**两次**用「推断」代替「取证」，都写下了**假结论**：

1. 把「关键词 0 命中」当成「能力不存在」（实际叫 ``plan_critic``）；
2. 用**推断的函数名** ``replan_batch_with_audit`` 做 AST 扫描查询键，
   得到 0 调用点 ⇒ 写下「接线 B 是死码」；复核发现该名字**根本不存在**，
   真实入口是 ``maybe_replan``（``autowrite.py:502`` 调用）⇒ 接线 B 是活的。

⇒ 故本文件所有 AST 扫描**一律以「实际定义点」为锚**（``_iter_module_functions``
先枚举真实 ``def`` 节点，再反查引用），**绝不接受手写字面量名作为查询键**。

## 误报防线（D3 的关键，实测证明必要）

朴素「零调用点」扫描会产出**大量误报**——本仓实测 146 个候选里**只有 19 个**
是真候选，其中 11 个真孤儿。误报来源：

- **typer / FastAPI 路由**（``@app.get``/``@cli.command``）⇒ 按**字符串**注册，
  永不出现函数名调用点；
- **工具注册表**（``@registry.register(name="count_words")``）⇒ 同上，
  且调用形态是 ``registry.call("quality_check", ...)``（**字符串**）；
- **hook 分发**（``register_genre_rules``）⇒ 字符串派发。

⇒ D3 的判据必须**识别装饰器注册**，否则红线会被误报淹没而被人关掉
（纪律 #18：缺**豁免**＝挡住合法历史；缺**作用域**＝误伤）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]        # <repo>/agent
_SRC = _REPO / "src" / "agent"
_TESTS = _REPO / "tests"

#: D2 盯住的接线：``review_plan`` 必须在**这两处**都被调用。
#: ``(相对 _SRC 的路径, 调用点所属函数名或 None=模块级/任意)``
_PLAN_CRITIC_CALLSITES = (
    ("workflows/pipeline/agentic_pipeline_planning.py", "review_plan"),
    ("workflows/pipeline/batch_replan.py", "review_plan"),
)

#: D3 的豁免表：**模块级 public 生产函数**，全仓零调用点但**有正当理由**。
#: ⚠ 每一项都必须写清「为什么它不是孤儿」——否则红线退化成橡皮图章。
#:
#: 组成（2026-09-19 全仓取证，逐项人工核实）：
#:   * **外部消费者型**：接口/Observability 读数，供体检报告、看板、
#:     CLI 脚本或下游定档流程消费，不在 Python 调用图里；
#:   * **公共 API 面型**：库式 API（如 ``retry_*`` 家族、``specs_by_*`` 家族），
#:     为一致性而保留的兄弟函数，当前无内部调用点；
#:   * ⚠ 这类是本仓的**高风险区**（纪律 #7）：docstring 写了「供 XX 消费」
#:     却查不出消费者 ＝ 该注释是缺陷的伪装。列入豁免时必须写明**消费者是谁**；
#:     写不出来的，不要豁免 —— 上报为「未收口隐患」。
_ORPHAN_EXEMPT: dict[str, str] = {
    # ---- 公共 API 面：契约完整性保留（同族兄弟均在使用，这几个是当前未用的入口）----
    "all_names": "维度注册表公共 API：返回全部维度名（兄弟 get_spec/specs_by_* 在用），供 CLI/脚本消费",
    "iter_specs": "维度注册表公共 API：按名批量取声明；当前无内部调用点，属查询面完整性保留",
    "spec_for": "维度注册表公共 API：**强约束**取声明（未登记即抛），供 L4 处置层新增维度时消费",
    "specs_by_repairability": "维度注册表公共 API：按可修复性过滤（兄弟 specs_by_timing 在用），供处置层消费",
    "get_roster": "agents/registry.py 公共 API：返回完整阵容；供 Web/CLI 或外部脚本消费",
    "owner_of": "degrade_registry 公共 API：命名空间→归属模块；供体检/文档生成或人工排查消费",
    "profile_to_llm_kwargs": "model_profiles 公共 API：档案→LLMConfig 关键字；供构造 LLM 的调用方消费",
    "has_explicit_max_time": "daemon/process_manager 公共 API：判 argv 是否显式给 --max-time，供墙钟缩放判定消费",
    "retry_business": "core/base/retry 公共 API：retry_* 装饰器家族之一（兄弟 retry_io/retry_parse 在用）",
    # ---- 观测面读数（Python 调用图外）----
    # ⚠ 注：``read_plan_critic`` / ``aggregate_readings`` 经复核**不算孤儿**
    #   （``aggregate_readings`` 内部调用 ``read_plan_critic``），故不列入本表。
    #   它们的**外部消费者缺失**问题属 D1（无数据 ≠ 通过）+ D5（M8 前置不可满足），
    #   由那条线索跟踪，不要在此重复登记（避免两个闸报同一件事）。
}


def _iter_py(base: Path):
    for p in sorted(base.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        yield p


def _parse(p: Path) -> ast.Module | None:
    try:
        return ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
    except (SyntaxError, UnicodeDecodeError):
        return None


def _is_decorator_registered(node: ast.FunctionDef) -> bool:
    """函数是否由**装饰器**注册（typer/FastAPI/工具表/hook）⇒ 不计孤儿。

    ★ 这是 D3 的**误报防线**：装饰器注册的符号按字符串派发，
      朴素调用点扫描必然看不到它们。
    """
    for dec in node.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        # @app.get / @cli.command / @registry.register / @router.post ...
        if isinstance(target, ast.Attribute):
            return True
        if isinstance(target, ast.Name):
            return True
    return False


def _module_scoped_public_defs(p: Path) -> list[ast.FunctionDef]:
    """文件**模块级**（非类方法）的 public 函数定义——以**实际 def 节点**为锚。"""
    tree = _parse(p)
    if tree is None:
        return []
    out: list[ast.FunctionDef] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("_"):
                continue
            if _is_decorator_registered(node):
                continue
            out.append(node)
    return out


def _all_referenced_names() -> set[str]:
    """全仓（src + tests）出现过的**标识符**集合。

    ⚠ 四类必须都算「引用」，否则会产出**假孤儿**（实测踩过两次）：

    1. ``Name`` / ``Attribute`` —— 直接调用；
    2. **``ImportFrom`` 的 ``alias.name``** —— ``from m import f as _f`` 的
       **原名**（★ 坑：只看 ``asname``/``Name`` 会把 ``usage_snapshot``、
       ``strip_leading_headings`` 这类**别名导入**的函数误判成孤儿，
       而它们其实活着）；
    3. **字符串字面量 + 点分路径末段** —— 本仓大量能力经
       ``registry.call("<name>")`` / hook 字符串键 / ``"a.b.c.func"`` 点分路径
       派发（★ 坑：只匹配整串会漏掉点分路径里的函数名）；
    4. ``keyword.arg`` —— ``@register(name="x")`` 这类关键字参数。

    ★ **必须排除本文件自身**：``_ORPHAN_EXEMPT`` 的键是字符串字面量，
      若把自己算进"引用"，则**每个**豁免条目都会看起来"已有引用"
      ⇒ 豁免表被自己的登记行为清空（实测：11 条全报 stale，真孤儿数为 0）。
      —— 同族教训：观测工具污染观测对象。
    """
    me = Path(__file__).resolve()
    seen: set[str] = set()
    for base in (_SRC, _TESTS):
        for p in _iter_py(base):
            if p.resolve() == me:
                continue
            tree = _parse(p)
            if tree is None:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Name):
                    seen.add(node.id)
                elif isinstance(node, ast.Attribute):
                    seen.add(node.attr)
                elif isinstance(node, ast.ImportFrom):
                    # ★ 别名导入：登记**原名**（asname 会被 Name 节点覆盖）
                    for a in node.names:
                        seen.add(a.name)
                        if a.asname:
                            seen.add(a.asname)
                elif isinstance(node, ast.Import):
                    for a in node.names:
                        if a.asname:
                            seen.add(a.asname)
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    seen.add(node.value)
                    # ★ 点分路径（``"a.b.Cls.method"``）末段也算引用
                    for seg in node.value.split("."):
                        if seg:
                            seen.add(seg)
                elif isinstance(node, ast.keyword) and node.arg:
                    seen.add(node.arg)
    return seen


# ============================================================
# D1：无数据 ≠ 通过
# ============================================================
class TestNoDataIsNotPass:
    """★ 台账缺失 ⇒ 消费端必须能区分「没审」与「没问题」（纪律 #1）。

    这是本仓一号病的评审层复现点：``plan_critic`` **从未在生产跑过**，
    若 ``read_plan_critic`` 在无台账时返回一个「干净空表」，
    任何消费端都会把它读成「规划无问题、可以升 blocking」。
    """

    def test_missing_ledger_reports_no_data(self, tmp_path: Path) -> None:
        from agent.core.story.plan_critic import read_plan_critic

        data = read_plan_critic(tmp_path)
        assert data["status"] == "no_data", (
            "台账不存在时必须报 no_data —— 不得静默返回一个看起来『干净』的空表"
        )
        assert data["total"] == 0

    def test_empty_ledger_is_not_ok(self, tmp_path: Path) -> None:
        from agent.core.story.plan_critic import CRITIC_LEDGER, read_plan_critic

        (tmp_path / ".state").mkdir(parents=True, exist_ok=True)
        (tmp_path / CRITIC_LEDGER).write_text("", encoding="utf-8")
        assert read_plan_critic(tmp_path)["status"] == "empty"

    def test_all_broken_lines_is_not_ok(self, tmp_path: Path) -> None:
        """全坏行 ⇒ 无有效记录 ⇒ 等同无数据（**不得**当成 ok）。"""
        from agent.core.story.plan_critic import CRITIC_LEDGER, read_plan_critic

        (tmp_path / ".state").mkdir(parents=True, exist_ok=True)
        (tmp_path / CRITIC_LEDGER).write_text("{坏\n{也坏\n", encoding="utf-8")
        assert read_plan_critic(tmp_path)["status"] == "empty"

    def test_real_ledger_is_ok(self, tmp_path: Path) -> None:
        from agent.core.story.plan_critic import (
            CRITIC_LEDGER,
            PlanCriticReport,
            read_plan_critic,
            record_plan_critic,
        )

        assert record_plan_critic(tmp_path, PlanCriticReport(current_chapter=1))
        assert read_plan_critic(tmp_path)["status"] == "ok"

    def test_aggregate_without_ledger_is_not_empty_pass(self, tmp_path: Path) -> None:
        """★★ ``aggregate_readings`` 无样本时**不得**返回 `{}`。

        `{}` 的天然读法是「没有任何判据有问题」＝全绿 ⇒ 若 M8 据此定档，
        会把「从未采样」读成「所有判据都可达」，正是纪律 #13 最怕的
        「阈值变摧毁扳机」的前置条件。⇒ 必须返回自曝其缺的哨兵。
        """
        from agent.core.story.plan_critic import aggregate_readings

        agg = aggregate_readings(tmp_path)
        assert "__status__" in agg, (
            "无数据时必须以哨兵自曝 —— 返回 `{}` 会被读成『全判据可达』"
        )
        assert agg["__status__"] == "no_data"
        assert agg["total"] == 0

    def test_aggregate_status_is_not_a_judge_key(self, tmp_path: Path) -> None:
        """哨兵键名不得与真实判据 id 撞名（否则污染历史达成率统计）。"""
        from agent.core.story.plan_critic import (
            PlanCriticReport,
            Finding,
            LEVEL_WARN,
            aggregate_readings,
            record_plan_critic,
        )

        rep = PlanCriticReport(current_chapter=1)
        rep.findings.append(Finding(LEVEL_WARN, "G1.real", "granularity", "x"))
        record_plan_critic(tmp_path, rep)
        agg = aggregate_readings(tmp_path)
        assert "__status__" not in agg
        assert set(agg) == {"G1.real"}


# ============================================================
# D2：接线不得孤儿化
# ============================================================
class TestPlanCriticReachability:
    """★ ``review_plan`` 的两处调用点必须同时在位（防静默退化为零评审）。

    ⚠ 与旧 ``test_gate_signal_reachability`` 的区别：那条判「**置位是否短路**」，
      本条判「**接线是否还在**」——本仓的实际失效形态是**后者**
      （能力修好了、接线被后来重构吃掉 ⇒ 零执行且无人发现）。
    """

    @pytest.mark.parametrize("rel,callee", _PLAN_CRITIC_CALLSITES)
    def test_review_plan_is_called(self, rel: str, callee: str) -> None:
        p = _SRC / rel
        assert p.exists(), f"接线文件不存在：{rel}（重构搬走了？红线须同步更新）"
        tree = _parse(p)
        assert tree is not None
        hits = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and (
                (isinstance(n.func, ast.Name) and n.func.id == callee)
                or (isinstance(n.func, ast.Attribute) and n.func.attr == callee)
            )
        ]
        assert hits, (
            f"{rel} 未调用 {callee}() ⇒ 规划评委在该路径上**静默退化为零评审**。"
            f"若确为有意移除，必须先证明『能力的新家』（纪律 #12）并同步更新本红线。"
        )

    def test_plan_critic_is_imported_at_callsites(self) -> None:
        """调用点必须**从真源导入**（不得本地重定义同名函数绕开红线）。"""
        for rel, _ in _PLAN_CRITIC_CALLSITES:
            src = (_SRC / rel).read_text(encoding="utf-8")
            assert "from agent.core.story.plan_critic import" in src, (
                f"{rel} 未从 core.story.plan_critic 导入 ⇒ 可能调用了同名替身"
            )

    def test_review_plan_is_not_blocking(self) -> None:
        """★ ``review_plan`` 恒不阻断：其返回值**不得**参与 BLOCK 判定。

        判据（结构性）：``review_plan(...)`` 的结果在调用点**不得**被赋给任何
        变量后再用于控制流 —— D3 尚未定档，硬拦＝把阈值当摧毁扳机（纪律 #13）。
        """
        import agent.core.story.plan_critic as pc

        # 契约级断言：入口返回报告对象（不是错误列表），且模块不导出 BLOCK 级枚举
        assert not hasattr(pc, "LEVEL_BLOCK"), (
            "plan_critic 不得拥有 BLOCK 级枚举（采样期恒不阻断，纪律 #13）"
        )
        for lvl in (pc.LEVEL_NOTE, pc.LEVEL_WARN, pc.LEVEL_UNREACHABLE):
            assert lvl != "block"


# ============================================================
# D3：零调用点生产函数须登记豁免
# ============================================================
class TestNoUndeclaredOrphans:
    """★ 把纪律 #7（「供 XX 消费」必须证明消费者存在）机器化。

    ⚠ **作用域**（纪律 #18）刻意收窄，否则红线会被误报淹没：
      - 只查 ``src/agent`` 下的**模块级** public 函数
        （类方法有 ``self.`` 形态、误报率高；且本仓历史孤儿都是模块级符号）；
      - 排除 ``cli/`` 与 ``web/``（装饰器注册，按字符串派发）；
      - 排除带装饰器的定义（``_is_decorator_registered``）；
      - 字符串字面量也算「引用」（工具表 / hook 按字符串派发）。
    """

    def _orphans(self) -> dict[str, str]:
        """★ 扫描时**必须排除本文件自身**。

        否则 ``_ORPHAN_EXEMPT`` 的键（字符串字面量）会被 ``_all_referenced_names``
        当成"引用"，使**每个**豁免条目都看起来"不再零调用点" ⇒ 豁免表被自己的
        登记行为清空（实测踩到：11 条全报 stale，而真孤儿数为 0）。
        —— 这是"观测工具污染观测对象"的又一实例（同族：全量 pytest 期间
        编辑源码 ⇒ ``inspect.getsource`` 读瞬断 ⇒ 假失败）。
        """
        referenced = _all_referenced_names()
        self_file = Path(__file__).resolve()
        out: dict[str, str] = {}
        for p in _iter_py(_SRC):
            if p.resolve() == self_file:
                continue
            rel = p.relative_to(_SRC).as_posix()
            if rel.startswith("cli/") or rel.startswith("web/"):
                continue
            for node in _module_scoped_public_defs(p):
                if node.name not in referenced:
                    out[node.name] = f"{rel}:{node.lineno}"
        return out

    def test_no_undeclared_orphan_public_functions(self) -> None:
        orphans = self._orphans()
        undeclared = {k: v for k, v in orphans.items() if k not in _ORPHAN_EXEMPT}
        assert not undeclared, (
            "发现**零调用点**的模块级 public 生产函数（纪律 #7："
            "凡写『供 XX 消费』必须证明消费者存在）：\n"
            + "\n".join(f"  {k}  @ {v}" for k, v in sorted(undeclared.items()))
            + "\n\n若确有正当理由（装饰器/字符串派发/外部消费者），"
            "请登记进本文件 ``_ORPHAN_EXEMPT`` 并写明**消费者是谁**；"
            "否则删除或补接线（纪律 #12：删前先取证『能力的新家』）。"
        )

    def test_exempt_entries_are_not_stale(self) -> None:
        """★ 豁免表不得腐烂：登记了却**已不再零调用点**的条目必须移除。

        这是豁免表能长期有效的前提（同 ``NESTED_REPO_ALLOWLIST`` 的纪律：
        **移出后必销条目**——别为过闸补条目，也别让过期条目留成垃圾）。
        """
        orphans = self._orphans()
        stale = [k for k in _ORPHAN_EXEMPT if k not in orphans]
        assert not stale, (
            "豁免表条目已失效（这些函数现在**有**调用点了）⇒ 必须从 "
            f"``_ORPHAN_EXEMPT`` 移除，否则豁免表会掩护未来的真孤儿：{stale}"
        )

    def test_exempt_entries_documented_with_consumer(self) -> None:
        """每条豁免必须写明**消费者是谁**（不得只写「已知/暂留」）。"""
        for name, note in _ORPHAN_EXEMPT.items():
            assert len(note) >= 12, f"豁免 {name} 的说明过短，未写清消费者"
            assert any(
                kw in note
                for kw in ("注册", "派发", "字符串", "消费", "供", "hook", "装饰器", "子类")
            ), f"豁免 {name} 未说明消费者机制：{note!r}"

    def test_detects_planted_orphan(self, tmp_path: Path) -> None:
        """★ 反向验证：红线**真的**能抓到孤儿（纪律 #20：单测绿 ≠ 判据有效）。

        在临时副本里植入一个零调用点函数，确认扫描器报出来 —— 否则
        ``_ORPHAN_EXEMPT`` 全绿可能只是「扫描器根本没在工作」。
        """
        plant = tmp_path / "planted_orphan.py"
        plant.write_text(
            "def a_totally_unreferenced_production_function_xyz() -> None:\n"
            "    return None\n",
            encoding="utf-8",
        )
        tree = _parse(plant)
        assert tree is not None
        defs = _module_scoped_public_defs(plant)
        assert [d.name for d in defs] == [
            "a_totally_unreferenced_production_function_xyz"
        ], "扫描器必须能看到真实 def 节点（以实际定义点为锚）"

    def test_decorated_and_private_defs_are_excluded(self, tmp_path: Path) -> None:
        """误报防线：装饰器注册与私有函数**不得**被当成候选。"""
        f = tmp_path / "deco.py"
        f.write_text(
            "import typer\n"
            "app = typer.Typer()\n"
            "@app.command()\n"
            "def some_command() -> None: ...\n"
            "def _private_helper() -> None: ...\n",
            encoding="utf-8",
        )
        assert _module_scoped_public_defs(f) == [], (
            "装饰器注册 / 私有函数必须被排除（否则红线被误报淹没，纪律 #18）"
        )
