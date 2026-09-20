"""「计数器/账本 → 动作」链条普查（纪律 #31 · B1 · 2026-09-20）

纪律 #31
--------
凡用**计数器增长**判「副作用发生了没」的链条（预算/评分/计数 → 熔断/降档/回退），
必须能证：
    ① 计数器**只在成功后**自增；
    ② 失败时**不动**，或**显性上报**（不得静默）。
反例（本仓已发生两起，方向相反）：
    * ``TracedLLMClient`` + provider hook **双记** ⇒ token 虚增 100% ⇒ **假熔断** ⇒
      自动降档 ⇒ 削掉 boost 层评审维度（"坏账自洽"）；
    * ``notify_llm_usage`` 先 ``epoch += 1`` 再调 hook，hook 内 ``except: pass``
      吞掉写盘异常 ⇒ 下游判「已被覆盖」⇒ **0 span 无日志**（静默漏记）。

本模块做什么
------------
**不判对错**，只把候选链条**列出来**交给人工复核，并把复核结论**记进台账**
（:data:`KNOWN_CHAINS`）—— 让"普查"成为一次可复现、可增量收敛的动作，
而不是一次性的印象。未复核候选数的下降趋势即收敛证据。

判据刻意粗（名字启发式）：普查工具的假阳性远多于真阳性（纪律 #28），
目标是**不漏**，再由人工把已知项记进台账（记错的代价只是噪音）。
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Any

#: 计数器/账本味的标识符（自增目标）
COUNTER_HINTS = (
    "count", "counter", "epoch", "total", "totals", "budget", "usage",
    "tokens", "n_", "_n", "retry", "attempt", "failures", "hits", "size",
)

#: 阈值/熔断味的比较目标
THRESHOLD_HINTS = (
    "budget", "limit", "max", "threshold", "cap", "quota", "rollback",
    "tripped", "stall", "timeout", "window",
)

_STRIP_RE = re.compile(r"^(self\.|_)|(_used|_count|_total)$")


def _normalize(name: str) -> str:
    return _STRIP_RE.sub("", name)


def _looks_counter(name: str) -> bool:
    n = _normalize(name).lower()
    return any(h.strip("_") in n for h in COUNTER_HINTS if h.strip("_"))


def _looks_threshold(name: str) -> bool:
    n = _normalize(name).lower()
    return any(h in n for h in THRESHOLD_HINTS)


@dataclass
class Chain:
    """一个候选链条（同一函数内既有计数器自增、又有阈值比较）。"""

    rel: str
    func: str
    increments: list[str] = field(default_factory=list)
    thresholds: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.rel}::{self.func}"


def _aug_targets(node: ast.AST) -> list[str]:
    """取出 ``x += 1`` 形态的目标名（**必须**是 ``+`` 且加的是整数字面量）。

    ★ 精度收紧（2026-09-20，复核 11 条候选后按证据调整）：首版把**任何**
    ``AugAssign`` 都当自增，于是把 ``token_limit *= self._budget_margin``（缩放）、
    ``_hygiene_warn += check_debut_echo(...)``（**列表**累积）、
    ``total += len(sents)``（统计分子）都算成"计数器自增"——4 条假阳性。
    现要求 ``op is Add`` 且右值是 ``int`` 字面量：这正是"计数器"的形态。

    ⚠ 已知召回边界（刻意保留，勿"顺手补全"）：``counter += len(xs)`` 这类
    **非字面量**自增不再匹配。普查工具宁可少报也不要噪音淹没真阳性
    （纪律 #28：假阳性会让人不再看报告），未匹配形态由人工巡检兜。
    """
    if not isinstance(node, ast.AugAssign) or not isinstance(node.op, ast.Add):
        return []
    val = node.value
    if not (isinstance(val, ast.Constant) and isinstance(val.value, int)
            and not isinstance(val.value, bool)):
        return []
    t = node.target
    if isinstance(t, ast.Name):
        return [t.id]
    if isinstance(t, ast.Attribute):
        return [t.attr]
    if isinstance(t, ast.Subscript):
        return ["[subscript]"]
    return []


def _assign_plus_one_targets(node: ast.AST) -> list[str]:
    """``x = x + …`` 形态的目标名（**必须自引用**）。

    ★ 精度收紧（2026-09-20）：首版只要求右侧是 ``Add``，于是把
    ``total = s["success"] + s["fail"]``（求和赋值）误判为自增，
    凭空造出 ``ModelRouter.is_tripped`` 这个"候选"。现要求右侧
    **出现同名标识符**（``x = x + …``）才算自增。
    """
    if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.BinOp):
        return []
    if not isinstance(node.value.op, ast.Add):
        return []
    referenced = {n.id for n in ast.walk(node.value) if isinstance(n, ast.Name)}
    out: list[str] = []
    for t in node.targets:
        if isinstance(t, ast.Name) and t.id in referenced:
            out.append(t.id)
    return out


def _compare_names(node: ast.AST) -> list[str]:
    """比较表达式两侧出现过的名字（用于识别"阈值比较"）。"""
    if not isinstance(node, ast.Compare):
        return []
    names: list[str] = []
    for side in [node.left, *node.comparators]:
        for sub in ast.walk(side):
            if isinstance(sub, ast.Name):
                names.append(sub.id)
            elif isinstance(sub, ast.Attribute):
                names.append(sub.attr)
    return names


def _iter_functions(tree: ast.AST) -> list[tuple[str, ast.AST]]:
    """模块级函数 + 类方法（``类.方法`` 命名）。"""
    out: list[tuple[str, ast.AST]] = []
    for node in getattr(tree, "body", []):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append((node.name, node))
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.append((f"{node.name}.{sub.name}", sub))
    return out


def scan_chains(text: str, rel: str) -> list[Chain]:
    """扫描单个源文件，返回候选链条（按函数聚合）。"""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    out: list[Chain] = []
    for name, fn in _iter_functions(tree):
        incs: list[str] = []
        ths: list[str] = []
        for node in ast.walk(fn):
            for target in _aug_targets(node) + _assign_plus_one_targets(node):
                if target != "[subscript]" and _looks_counter(target):
                    incs.append(target)
            for nm in _compare_names(node):
                if _looks_threshold(nm):
                    ths.append(nm)
        if incs and ths:
            out.append(Chain(rel=rel, func=name,
                             increments=sorted(set(incs)),
                             thresholds=sorted(set(ths))))
    return out


#: 复核台账：key = ``rel::函数``（与 :func:`scan_chains` 的 ``Chain.key`` 同构）
#: → 结论（**写清为什么不是缺陷，或已如何守**）。
#: 新增条目必须写明复核依据（最好含行号）；僵尸条目（已扫不到）由红线拦截。
KNOWN_CHAINS: dict[str, str] = {
    "agents/evaluator.py::EvaluatorAgent.evaluate_with_repair":
        "★ 已守（本轮读函数体，最高危项——它控制**不可逆回退**的上限）："
        "attempts += 1 **只在副作用成功后**（L534 在 rewriter 正常返回后；"
        "L576 在 trigger_rollback 已 rolled_back（L553 校验）且 rewriter 成功后）；"
        "三条失败路径（L529 定向修复失败 / L554 回退被前置闸拒 / L571 重写失败）"
        "一律先 escalate + 写 escalated_reason 再 return，**不自增**；"
        "阈值检查（L512 attempts >= max）在动作**之前**。⇒ 符合纪律 #31① ②。",
    "core/project_lock.py::acquire_project_lock":
        "非缺陷：stale_rounds（L143）计的是**重试轮次**（确认陈旧锁之后才自增），"
        "达上限后的动作（L144）是**保守拒绝**（raise ProjectLockBusy，绝不双写），"
        "不是不可逆动作；陈旧锁删除被 safe-delete 护栏拦时走 degrade"
        "（L155，namespace project_lock.acquire 已登记）⇒ #31② 满足。",
    "core/quality/scoring/quality_checker.py::QualityChecker.revise_loop":
        "已守：attempts += 1（L574）位于 revise_fn（L572）+ 复检（L573）**之后** ⇒ "
        "只在动作完成后自增；循环上界由 L571 条件（attempts < MAX_REVISION_ATTEMPTS）"
        "保证，达界即返回当时文本与报告（不静默继续）。",
    "workflows/pipeline/agentic_pipeline_events.py::_PipelineEventsMixin._note_gate_blind":
        "已守（且是「计数器跨运行」的正面样本）：计数按来源**分桶**"
        "（write_gate_streak / blind_streak，防章末 `_note_gate_ok` 互相清零）"
        "+ 落盘跨运行累计（L109 _gate_blind_save）；save/load 均有 degrade 留痕"
        "（registry: pipeline.gate_blind.save / .load）；有行为级红线 "
        "tests/test_gate_signal_reachability.py 覆盖。",
}

#: **读数消费者**（有"读数 → 阈值 → 动作"但**不产生自增**）：扫描器按设计不产出
#: 它们的候选（判据只看自增），但它们是 #31 真正关心的"读数驱动动作"链条
#: ⇒ 在此**建档**，供复核者一眼看清"测量点在哪、唯一性靠谁"。
#: 红线断言这些 key 仍指向真实函数（防文档过期）。
READONLY_CONSUMERS: dict[str, str] = {
    "client/router.py::ModelRouter.is_tripped":
        "纯读者：total = success + fail（L133）后与 min_samples / "
        "circuit_breaker_threshold 比较，**无自增**；计数在记录侧累加。",
    "workflows/pipeline/agentic_pipeline_cost.py::_PipelineCostMixin._check_budget":
        "读数消费者：读 _used_tokens()/get_tracer().totals() 与 token_limit 比较 ⇒ 熔断。"
        "**读数唯一性由 #6 用量记账唯一收口负责**（token 虚增 100% ⇒ 假熔断的根因已收口），"
        "本处不自增 ⇒ #31① 由上游客服保证。",
    "workflows/pipeline/agentic_pipeline_cost.py::_PipelineCostMixin._maybe_downgrade_tier":
        "同族读数消费者（读 totals + 比较 ⇒ 降档）；动作强度有显式闸："
        "--no-auto-downgrade 可关、未知档位/异常一律 False（保守）、最低档仍超限退回既有熔断。",
}

#: 历史事故（**文档性**，不参与僵尸检查——它们未必命中扫描器的名字启发式）。
#: 用途：后来人复核同类链条时，先看这两起的方向相反的故障形态。
HISTORICAL_INCIDENTS: dict[str, str] = {
    "core/llmops/traced_llm.py::TracedLLMClient._record":
        "★ 真实事故（2026-09-18）：与 provider hook **双记** ⇒ token 虚增 100% ⇒ "
        "假熔断 ⇒ 自动降档 ⇒ 削掉 boost 层评审维度（坏账自洽）。已修（#6）："
        "调用前后各读 usage_epoch()，仅当**未增长**才补记。",
    "client/llm_usage.py::notify_llm_usage":
        "★ 真实事故（方向相反）：先 epoch+=1 再调 hook，hook 内 except:pass 吞掉写盘"
        "异常 ⇒ 下游判「已被覆盖」⇒ 0 span 无日志（静默漏记）。已修（#31）："
        "显式哨兵 hook->bool，仅 is False 算漏记，禁用 is True。",
}
