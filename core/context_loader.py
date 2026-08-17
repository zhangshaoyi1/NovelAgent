"""上下文分层加载器（M12）

职责：按场景智能加载设定，控制 LLM 上下文 token 用量。

加载策略：
    - 必载层：world.md 摘要 + 当前支线 subline.md + 涉及角色档案 + 关系网子图
    - 按需层：其他支线设定摘要、历史章节摘要、相关伏笔条目
    - 摘要机制：每 5 章把旧章节压缩为结构化摘要
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class LoadedContext:
    """已加载的上下文"""

    world_summary: str = ""
    subline: dict[str, Any] = field(default_factory=dict)
    characters: list[dict[str, Any]] = field(default_factory=list)
    relation_subgraph: dict[str, Any] = field(default_factory=dict)
    foreshadows: list[dict[str, Any]] = field(default_factory=list)
    chapter_summaries: list[dict[str, Any]] = field(default_factory=list)
    # 估算 token 数（用于成本控制）
    estimated_tokens: int = 0


class ContextLoader:
    """上下文分层加载器"""

    SUMMARY_INTERVAL = 5  # 每 5 章触发摘要（M0 决策 7）

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir

    def load_for_writing(
        self,
        subline_id: str,
        involved_characters: list[str],
        chapter_index: int,
    ) -> LoadedContext:
        """写章前加载上下文（必载层 + 按需层）

        Args:
            subline_id: 当前支线 ID
            involved_characters: 本章涉及角色名
            chapter_index: 当前章节序号

        Returns:
            LoadedContext
        """
        # TODO: 实现
        # 1. 加载 world.md 摘要
        # 2. 加载当前支线 subline.md
        # 3. 加载涉及角色档案
        # 4. 加载关系网当前子图
        # 5. 查询本章应埋/回收的伏笔条目
        # 6. 如 chapter_index > SUMMARY_INTERVAL，加载历史章节摘要
        raise NotImplementedError

    def load_for_review(self, chapter_range: tuple[int, int]) -> LoadedContext:
        """加载指定章节范围的上下文（用于审计/修订）"""
        # TODO: 实现
        raise NotImplementedError

    def estimate_tokens(self, text: str) -> int:
        """估算文本 token 数（粗略：字符数 / 2，中文）"""
        # TODO: 用 tiktoken 精确计算
        return max(1, len(text) // 2)

    def summarize_chapters(self, chapter_files: list[Path]) -> str:
        """把多个章节压缩为结构化摘要（由主 Agent 调用 LLM 生成，v2 拆 subagent）"""
        # TODO: 调用 llm_client 生成摘要
        raise NotImplementedError
