"""快照与回滚器（M10）

职责：
    - 设定集/关系网/路线/章节状态的版本快照
    - 分支重写：回滚到第 N 章的分叉点，后续章节标记为废弃
    - 续作恢复：长时间未操作后输出续作简报

快照存储：
    settings_snapshots/<timestamp>_<label>/
    relations/snapshots/<timestamp>_<label>.md
    .state/checkpoint.json
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ResumeBriefing:
    """续作简报"""

    last_position: str               # 上次写到哪（支线/小剧集/章节）
    pending_storylines: list[str]    # 3 条悬而未决的剧情线
    unresolved_foreshadows: list[dict[str, Any]]  # 未回收伏笔
    recent_relation_changes: list[str]  # 关系网最近 3 次变化
    suggested_next: str              # 建议下一步


class SnapshotManager:
    """快照与回滚器"""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.snapshots_dir = project_dir / "settings_snapshots"

    def create_snapshot(self, label: str) -> Path:
        """创建全量快照（world + sublines + characters + relations + state）"""
        # TODO: 实现
        timestamp = ""  # TODO: 时间戳
        snapshot_dir = self.snapshots_dir / f"{timestamp}_{label}"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        # TODO: 复制各文件
        return snapshot_dir

    def list_snapshots(self) -> list[Path]:
        """列出所有快照"""
        if not self.snapshots_dir.exists():
            return []
        return sorted(self.snapshots_dir.iterdir())

    def rollback_to_chapter(self, chapter_index: int) -> None:
        """回滚到指定章节的分叉点（M10 F10.1）

        后续章节标记为废弃，移动到 _archived/，不删除。
        """
        # TODO: 实现
        # 1. 找到该章节对应的快照
        # 2. 恢复 world / subline / characters / relations / state
        # 3. 后续章节移到 sublines/.../_archived/
        raise NotImplementedError

    def archive_chapters(self, from_index: int) -> Path:
        """把指定章节之后的章节归档到 _archived/"""
        # TODO: 实现
        raise NotImplementedError

    def generate_resume_briefing(self) -> ResumeBriefing:
        """生成续作简报（M10 F10.2）"""
        # TODO: 实现
        # 1. 读取 state.json 获取上次位置
        # 2. 从压力曲线 + 伏笔表推断悬而未决的剧情线
        # 3. 列出未回收伏笔（按优先级）
        # 4. 关系网最近 3 次变化
        # 5. 建议下一步
        raise NotImplementedError
