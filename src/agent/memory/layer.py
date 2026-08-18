"""Memory Layer 门面（Phase 2）

把语义记忆 / 会话记忆 / 整合记忆统一为一个对外接口，供 Planner / Writer / Editor /
Pipeline 注入复用。组件均可单独注入（便于离线测试用纯内存实例）。

设计对应设计文档 §2.3 / §2.5：Writer 写章后入 Memory；Evaluator 终审判定后更新整合
记忆；Planner 据 Memory 修订计划。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.memory.consolidated import ConsolidatedMemory
from agent.memory.conversation import ConversationMemory
from agent.memory.semantic import SemanticMemory


class MemoryLayer:
    """统一记忆门面。

    Args:
        project_dir: 小说项目目录；None 表示三层全为纯内存（测试用）。
        semantic / conversation / consolidated: 可注入的具体实现（替换默认）。
        scorer: 传入 SemanticMemory 的检索打分器。
    """

    def __init__(
        self,
        project_dir: str | Path | None = None,
        semantic: SemanticMemory | None = None,
        conversation: ConversationMemory | None = None,
        consolidated: ConsolidatedMemory | None = None,
        scorer: Any = None,
    ) -> None:
        self.project_dir = Path(project_dir) if project_dir else None
        self.semantic = semantic or SemanticMemory(self.project_dir, scorer=scorer)
        self.conversation = conversation or ConversationMemory(self.project_dir)
        self.consolidated = consolidated or ConsolidatedMemory(self.project_dir)

    # ---------------------------------------------------------------- 语义层
    def remember(
        self,
        text: str,
        *,
        type: str = "fact",
        tags: list[str] | None = None,
        source: str = "",
        meta: dict[str, Any] | None = None,
    ) -> Any:
        """写入一条长期事实记忆。"""
        return self.semantic.add(
            text, type=type, tags=tags, source=source, meta=meta
        )

    def recall(
        self, query: str, *, top_k: int = 5, types: list[str] | None = None
    ) -> list[tuple[Any, float]]:
        """语义召回。"""
        return self.semantic.retrieve(query, top_k=top_k, types=types)

    # ---------------------------------------------------------------- 会话层
    def log(self, kind: str, message: str, data: dict[str, Any] | None = None) -> Any:
        """记录一条会话事件。"""
        return self.conversation.append(kind, message, data)

    def recent_decisions(self, n: int = 10) -> list[Any]:
        """取最近 N 条决策/规划类事件，供 Planner 修订计划。"""
        evs = self.conversation.query(kinds=["plan", "decision", "rollback"], recent=n)
        return evs

    # ---------------------------------------------------------------- 整合层
    def consolidate(self, **kwargs: Any) -> None:
        """增量整合（见 ConsolidatedMemory.update）。"""
        self.consolidated.update(**kwargs)

    def build_consolidated(self, **kwargs: Any) -> None:
        """一次性重建整合快照（见 ConsolidatedMemory.build_from）。"""
        self.consolidated.build_from(**kwargs)

    def book_bible(self) -> dict[str, Any]:
        """返回当前整合快照（Book Bible）。"""
        return self.consolidated.to_dict()

    # ---------------------------------------------------------------- 章节回写
    def record_chapter(
        self, chapter_num: int, title: str, summary: str = "", facts: list[str] | None = None
    ) -> None:
        """写章后回写记忆：语义层记事实 + 会话层记事件 + 整合层推进章节指针。"""
        if summary:
            self.remember(
                f"第{chapter_num}章《{title}》：{summary}",
                type="event",
                tags=[f"ch{chapter_num:03d}", title],
                source=f"ch{chapter_num:03d}",
            )
        for f in facts or []:
            self.remember(f, type="fact", tags=[f"ch{chapter_num:03d}"], source=f"ch{chapter_num:03d}")
        self.conversation.log_chapter(chapter_num, title, summary)
        self.consolidate(last_consolidated_chapter=chapter_num)
