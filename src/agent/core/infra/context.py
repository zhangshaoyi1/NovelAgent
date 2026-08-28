"""Context Engineering（Phase 5 · 上下文工程）

长篇小说写到中后期，章节、记忆、设定会快速撑爆上下文窗口。本模块提供四类能力，
让编排层在有限 token 预算内保留**最有价值**的上下文（伏笔 / 人物 / 关键转折），
把琐碎事件压缩成摘要，并为稳定的系统前缀标注 Prompt Caching 断点：

1. **重要性加权** ``importance``：按类别（伏笔 > 人物 > 情节 > 事件）给上下文条目打分。
2. **压缩** ``compress``：把早期可压缩事件合并为一条离线摘要（生产可注入真 LLM 摘要）。
3. **预算裁剪** ``fit``：按（重要性 + 新近度）排序，预算不足时优先丢弃低价值条目。
4. **Prompt Caching 断点** ``build_prompt_blocks``：对稳定前缀（system / 全书设定 /
   人物档案）标注 ``cache_control``，供支持缓存的模型复用前缀、降本提速。

全部离线、零依赖、零网络；token 估算与摘要函数均可注入（生产接入 tiktoken / 真 LLM）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

# 不同类别的基线重要性（越高越该保留）。
_KIND_BASE_IMPORTANCE: dict[str, float] = {
    "system": 1.00,     # 系统提示 / 动作协议
    "bible": 1.00,      # 全书设定 / Book Bible
    "character": 0.95,  # 人物档案
    "foreshadow": 0.90, # 伏笔
    "plot": 0.80,       # 关键情节 / 转折
    "recent": 0.70,     # 近期章节（保留）
    "fact": 0.60,       # 长期事实
    "event": 0.40,      # 普通章节事件（可压缩）
}


@dataclass
class ContextItem:
    """一条待纳入上下文的条目。"""

    key: str
    text: str
    kind: str = "fact"
    ts: int = 0  # 单调递增序号，越大越新


class ContextEngine:
    """上下文工程引擎。

    Args:
        token_budget: 上下文 token 预算（默认 8000）。
        est_fn: token 估算 ``Callable[[str], int]``；默认按字符数 / 4。
        summarizer: 压缩摘要 ``Callable[[list[ContextItem]], str]``；默认离线摘要。
        kind_weights: 类别重要性覆盖表。
        cache_breakpoint_kinds: 标注 Prompt Caching 断点的类别集合。
    """

    def __init__(
        self,
        token_budget: int = 8000,
        est_fn: Callable[[str], int] | None = None,
        summarizer: Callable[[list[ContextItem]], str] | None = None,
        kind_weights: dict[str, float] | None = None,
        cache_breakpoint_kinds: tuple[str, ...] = ("system", "bible", "character"),
    ) -> None:
        self.token_budget = token_budget
        self.est_fn = est_fn or (lambda s: max(1, len(s) // 4))
        self.summarizer = summarizer or self._default_summarize
        self.kind_weights = {**_KIND_BASE_IMPORTANCE, **(kind_weights or {})}
        self.cache_breakpoint_kinds = set(cache_breakpoint_kinds)

    # ------------------------------------------------------------------
    # 重要性 / 打分
    # ------------------------------------------------------------------
    def importance(self, item: ContextItem) -> float:
        """类别基线重要性（0~1）。"""
        return self.kind_weights.get(item.kind, 0.5)

    def score(self, item: ContextItem, total: int) -> float:
        """综合分 = 重要性为主 + 轻度新近度（仅可压缩类受时间影响）。"""
        imp = self.importance(item)
        recency = item.ts / max(1, total)
        recency_weight = 0.20 if item.kind in ("event", "recent") else 0.05
        return imp * (1 - recency_weight) + recency * recency_weight

    # ------------------------------------------------------------------
    # 压缩：把早期可压缩事件合并为一条离线摘要
    # ------------------------------------------------------------------
    def compress(self, items: list[ContextItem], keep_recent: int = 5) -> list[ContextItem]:
        ordered = sorted(items, key=lambda x: x.ts)
        n = len(ordered)
        recent_ids = {it.key for it in ordered[-keep_recent:]} if keep_recent else set()
        recent = [it for it in ordered if it.key in recent_ids]
        old = [it for it in ordered if it.key not in recent_ids]

        compressible = [it for it in old if it.kind in ("event", "fact")]
        others = [it for it in old if it.kind not in ("event", "fact")]
        if compressible:
            summary_text = self.summarizer(compressible)
            summary_item = ContextItem(
                key="__compressed__",
                text=summary_text,
                kind="event",
                ts=compressible[0].ts,
            )
            return others + [summary_item] + recent
        return ordered

    @staticmethod
    def _default_summarize(items: list[ContextItem]) -> str:
        head = "\n".join(f"- {it.text[:60]}" for it in items[:3])
        tail = f"\n…（其余 {len(items) - 3} 条早期事件已压缩）" if len(items) > 3 else ""
        return f"[已压缩 {len(items)} 条早期事件]\n{head}{tail}"

    # ------------------------------------------------------------------
    # 预算裁剪：保留最高价值条目，超出预算丢弃低价值
    # ------------------------------------------------------------------
    def fit(
        self, items: list[ContextItem], budget: int | None = None
    ) -> list[ContextItem]:
        budget = budget if budget is not None else self.token_budget
        total = len(items)
        scored = sorted(items, key=lambda it: self.score(it, total), reverse=True)
        picked: list[ContextItem] = []
        used = 0
        for it in scored:
            cost = self.est_fn(it.text)
            if used + cost <= budget:
                picked.append(it)
                used += cost
        # 按时间顺序输出，保持上下文连贯
        return sorted(picked, key=lambda x: x.ts)

    # ------------------------------------------------------------------
    # Prompt Caching 断点：为稳定前缀标注 cache_control
    # ------------------------------------------------------------------
    def build_prompt_blocks(self, items: list[ContextItem]) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        for it in sorted(items, key=lambda x: x.ts):
            is_stable = it.kind in self.cache_breakpoint_kinds
            block: dict[str, Any] = {
                "role": "system" if is_stable else "user",
                "content": it.text,
            }
            if is_stable:
                block["cache_control"] = {"type": "ephemeral"}
            blocks.append(block)
        return blocks

    # ------------------------------------------------------------------
    # 便捷组合：压缩 → 裁剪 → 组装块
    # ------------------------------------------------------------------
    def prepare(
        self, items: list[ContextItem], *, compress: bool = True, budget: int | None = None
    ) -> list[dict[str, Any]]:
        working = self.compress(items) if compress else list(items)
        fitted = self.fit(working, budget=budget)
        return self.build_prompt_blocks(fitted)
