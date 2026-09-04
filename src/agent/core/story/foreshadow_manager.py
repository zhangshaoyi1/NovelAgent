"""伏笔管理器（M13）

职责：维护伏笔登记表，每章前检查应埋/应回收的伏笔。

伏笔表结构（foreshadows.md）：
    | ID | 内容 | 埋设位置 | 预期回收点 | 状态 | 关联角色 |

状态：未埋 / 已埋 / 已回收 / 已废弃

规则：
    - 每 10 章强制埋 ≥1 长线伏笔
    - 每 10 章回收 ≥1 旧伏笔
    - 支线结束时检查未回收伏笔
    - 完结时生成伏笔回收报告
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class ForeshadowState(str, Enum):
    """伏笔状态"""

    PENDING = "未埋"        # 计划中，尚未埋设
    PLANTED = "已埋"        # 已埋设，等待回收
    RESOLVED = "已回收"     # 已回收
    ABANDONED = "已废弃"    # 放弃回收


@dataclass
class Foreshadow:
    """伏笔条目"""

    id: str
    content: str
    planted_at: str = ""          # 埋设位置（章节 ID）
    expected_resolve: str = ""    # 预期回收点
    state: ForeshadowState = ForeshadowState.PENDING
    related_characters: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.related_characters is None:
            self.related_characters = []


# 每 10 章强制埋/回收伏笔
FORESHADOW_INTERVAL = 10


class ForeshadowManager:
    """伏笔管理器"""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.foreshadows_file = project_dir / "foreshadows.md"
        self.foreshadows: list[Foreshadow] = []

    def load(self) -> None:
        """从 foreshadows.md 加载伏笔表"""
        # TODO: 解析 markdown 表格
        raise NotImplementedError

    def save(self) -> None:
        """保存伏笔表到 foreshadows.md"""
        # TODO: 渲染为 markdown 表格
        raise NotImplementedError

    def plan_for_chapter(self, chapter_index: int) -> dict[str, Any]:
        """本章伏笔任务规划

        Args:
            chapter_index: 当前章节序号

        Returns:
            {"should_plant": [...], "should_resolve": [...]}
        """
        # TODO: 检查
        # 1. 是否到 10 章节点需埋新伏笔
        # 2. 是否有到期该回收的伏笔（expected_resolve 命中当前支线/章节）
        raise NotImplementedError

    def plant(self, foreshadow_id: str, at_chapter: str) -> None:
        """标记伏笔已埋设"""
        # TODO: 实现
        raise NotImplementedError

    def resolve(self, foreshadow_id: str, at_chapter: str) -> None:
        """标记伏笔已回收"""
        # TODO: 实现
        raise NotImplementedError

    def abandon(self, foreshadow_id: str, reason: str) -> None:
        """废弃伏笔"""
        # TODO: 实现
        raise NotImplementedError

    def check_subline_end(self, subline_id: str) -> list[Foreshadow]:
        """支线结束时检查未回收伏笔"""
        # TODO: 返回该支线相关的未回收伏笔
        raise NotImplementedError

    def generate_final_report(self) -> str:
        """完结时生成伏笔回收报告"""
        # TODO: 统计已埋/已回收/已废弃/未回收
        raise NotImplementedError
