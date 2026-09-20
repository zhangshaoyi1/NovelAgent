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
    """取出 AugAssign(+=1) 的目标名（含属性访问的末段）。"""
    if not isinstance(node, ast.AugAssign):
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
    """``x = x + 1`` 形态。"""
    if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.BinOp):
        return []
    if not isinstance(node.value.op, ast.Add):
        return []
    out: list[str] = []
    for t in node.targets:
        if isinstance(t, ast.Name):
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
#: 新增条目必须写明复核依据；僵尸条目（已扫不到）由红线拦截。
KNOWN_CHAINS: dict[str, str] = {
    "workflows/pipeline/agentic_pipeline_cost.py::_PipelineCostMixin._check_budget":
        "复核（本轮，读过函数体）：本函数是**读数消费者**而非自增点——读 "
        "_used_tokens()/get_tracer().totals() 后与 token_limit 比较决定熔断。"
        "读数唯一性由 #6 用量记账唯一收口负责（虚增 100% ⇒ 假熔断 的根因已收口），"
        "故本链条的 #31① 由上游收口保证；本处仅消费，不再自增。",
    "workflows/pipeline/agentic_pipeline_cost.py::_PipelineCostMixin._maybe_downgrade_tier":
        "复核（本轮，读过函数体）：同为读数消费者（读 totals + 比较 ⇒ 降档）。"
        "动作强度有显式闸：--no-auto-downgrade 可关、未知档位/异常一律 False（保守）、"
        "最低档仍超限则退回既有熔断 ⇒ 与判据可达性匹配（纪律 #13）。",
    "client/router.py::ModelRouter.is_tripped":
        "复核（本轮，读过函数体）：纯读 self._stats 后返回布尔，**不产生自增**；"
        "自增与阈值计数在记录侧（record_* 家族），本条不是链条端点。",
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
