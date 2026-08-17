"""设定集管理器（M7）

职责：读写双层设定集（world.md + subline.md）+ 角色档案，支持版本快照与冻结字段。

数据契约：
    - 所有设定文件采用 Markdown + YAML front matter
    - 冻结字段（境界体系、金手指上限）修改需显式解冻

目录结构：
    project_dir/
    ├── world.md                      # 总设定集
    ├── characters/<name>.md          # 角色档案
    ├── sublines/S<NN>_<name>/
    │   └── subline.md                # 支线设定集
    ├── relations/graph.md
    └── settings_snapshots/<ts>_<label>/  # 版本快照
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import frontmatter

from agent.core.exceptions import FrozenFieldError
from agent.utils import safe_remove


class SettingManager:
    """设定集管理器

    管理：
        - world.md（总设定集，全局唯一）
        - sublines/S*/subline.md（支线设定集）
        - characters/*.md（角色档案）
        - settings_snapshots/（版本快照）
    """

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = Path(project_dir)
        self.world_file = self.project_dir / "world.md"
        self.characters_dir = self.project_dir / "characters"
        self.sublines_dir = self.project_dir / "sublines"
        self.snapshots_dir = self.project_dir / "settings_snapshots"
        # 当前会话已解冻的字段（session 级，重启重置）
        self._unfrozen: set[str] = set()

    # ------ 总设定集 ------
    def load_world(self) -> dict[str, Any]:
        """加载 world.md

        Returns:
            {"metadata": {...}, "content": "...", "exists": bool}
        """
        if not self.world_file.exists():
            return {"metadata": {}, "content": "", "exists": False}
        post = frontmatter.load(self.world_file)
        return {
            "metadata": dict(post.metadata),
            "content": post.content,
            "exists": True,
        }

    def save_world(self, metadata: dict[str, Any], content: str) -> Path:
        """保存 world.md

        Args:
            metadata: front matter 字段
            content: 正文

        Returns:
            写入的文件路径
        """
        self.world_file.parent.mkdir(parents=True, exist_ok=True)
        # 校验冻结字段
        self._check_frozen_before_save(metadata)
        post = frontmatter.Post(content, **metadata)
        self.world_file.write_text(frontmatter.dumps(post), encoding="utf-8")
        return self.world_file

    def _check_frozen_before_save(self, new_metadata: dict[str, Any]) -> None:
        """保存前校验：冻结字段若改动需先解冻"""
        if not self.world_file.exists():
            return
        old = frontmatter.load(self.world_file)
        frozen_fields: list[str] = old.metadata.get("frozen_fields", []) or []
        for field in frozen_fields:
            if field in self._unfrozen:
                continue
            old_val = old.metadata.get(field)
            new_val = new_metadata.get(field)
            if old_val != new_val and new_val is not None:
                raise FrozenFieldError(
                    f"字段 '{field}' 已冻结，修改前请先调用 unfreeze('{field}')"
                )

    def append_revision_log(self, message: str) -> Path:
        """追加一条修订日志到 world.md frontmatter（F-E3.4 / E4 审计）

        幂等地维护 world.md 的 ``revision_log`` 列表字段（仅追加，不改其他字段）。
        该字段不参与冻结校验，便于冲突仲裁、证据链校验等过程自由记录。

        Args:
            message: 日志条目（如 "[仲裁-高] 前置冲突检测拦截生成：..."）

        Returns:
            写入的文件路径

        Note:
            若 world.md 尚不存在，先以占位 front matter 创建文件再追加。
        """
        data = self.load_world()
        metadata = dict(data["metadata"])
        log: list[str] = list(metadata.get("revision_log", []) or [])
        log.append(message)
        metadata["revision_log"] = log
        return self.save_world(metadata, data["content"])

    # ------ 冻结字段管理 ------
    def unfreeze(self, field: str) -> None:
        """解冻字段（仅当前会话有效）"""
        self._unfrozen.add(field)

    def freeze(self, field: str) -> None:
        """重新冻结字段"""
        self._unfrozen.discard(field)

    def is_frozen(self, field: str) -> bool:
        """字段是否处于冻结状态"""
        if not self.world_file.exists():
            return False
        post = frontmatter.load(self.world_file)
        frozen_fields: list[str] = post.metadata.get("frozen_fields", []) or []
        return field in frozen_fields and field not in self._unfrozen

    def update_frozen_field(self, field: str, value: Any) -> Path:
        """更新冻结字段（需先解冻）

        Raises:
            FrozenFieldError: 字段已冻结且未解冻
        """
        if self.is_frozen(field):
            raise FrozenFieldError(
                f"字段 '{field}' 已冻结，修改前请先调用 unfreeze('{field}')"
            )
        data = self.load_world()
        metadata = data["metadata"]
        metadata[field] = value
        return self.save_world(metadata, data["content"])

    # ------ 支线设定集 ------
    def load_subline(self, subline_id: str) -> dict[str, Any]:
        """加载支线 subline.md

        Args:
            subline_id: 支线 ID，如 "S01_悟道之旅"
        """
        path = self._subline_path(subline_id)
        if not path.exists():
            return {"metadata": {}, "content": "", "exists": False}
        post = frontmatter.load(path)
        return {
            "metadata": dict(post.metadata),
            "content": post.content,
            "exists": True,
        }

    def save_subline(
        self, subline_id: str, metadata: dict[str, Any], content: str
    ) -> Path:
        """保存支线 subline.md"""
        path = self._subline_path(subline_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        post = frontmatter.Post(content, **metadata)
        path.write_text(frontmatter.dumps(post), encoding="utf-8")
        return path

    def _subline_path(self, subline_id: str) -> Path:
        """支线 subline.md 路径"""
        return self.sublines_dir / subline_id / "subline.md"

    def list_sublines(self) -> list[str]:
        """列出所有支线 ID"""
        if not self.sublines_dir.exists():
            return []
        return sorted(
            d.name for d in self.sublines_dir.iterdir() if d.is_dir()
        )

    # ------ 角色档案 ------
    def load_character(self, name: str) -> dict[str, Any]:
        """加载角色档案

        Args:
            name: 角色名（用作文件名）
        """
        path = self._character_path(name)
        if not path.exists():
            return {"metadata": {}, "content": "", "exists": False}
        post = frontmatter.load(path)
        return {
            "metadata": dict(post.metadata),
            "content": post.content,
            "exists": True,
        }

    def save_character(
        self, name: str, metadata: dict[str, Any], content: str
    ) -> Path:
        """保存角色档案"""
        path = self._character_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        post = frontmatter.Post(content, **metadata)
        path.write_text(frontmatter.dumps(post), encoding="utf-8")
        return path

    def _character_path(self, name: str) -> Path:
        """角色档案路径（name 中的特殊字符替换为下划线）"""
        safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)
        return self.characters_dir / f"{safe_name}.md"

    def list_characters(self) -> list[str]:
        """列出所有角色档案文件名（不含扩展名）"""
        if not self.characters_dir.exists():
            return []
        return sorted(p.stem for p in self.characters_dir.glob("*.md"))

    # ------ 版本快照 ------
    def create_snapshot(self, label: str = "") -> Path:
        """创建设定集版本快照

        复制 world.md / sublines / characters / relations 到
        settings_snapshots/<timestamp>_<label>/

        Returns:
            快照目录路径
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_label = "".join(c if c.isalnum() else "_" for c in label) if label else "snapshot"
        snapshot_dir = self.snapshots_dir / f"{timestamp}_{safe_label}"
        snapshot_dir.mkdir(parents=True, exist_ok=True)

        # 复制 world.md
        if self.world_file.exists():
            shutil.copy2(self.world_file, snapshot_dir / "world.md")
        # 复制 characters
        if self.characters_dir.exists():
            shutil.copytree(self.characters_dir, snapshot_dir / "characters")
        # 复制 sublines
        if self.sublines_dir.exists():
            shutil.copytree(self.sublines_dir, snapshot_dir / "sublines")
        # 复制 relations
        relations_dir = self.project_dir / "relations"
        if relations_dir.exists():
            shutil.copytree(relations_dir, snapshot_dir / "relations")

        return snapshot_dir

    def list_snapshots(self) -> list[Path]:
        """列出所有快照（按时间倒序）"""
        if not self.snapshots_dir.exists():
            return []
        return sorted(
            self.snapshots_dir.iterdir(),
            reverse=True,
        )

    def rollback_to_snapshot(self, snapshot_dir: Path) -> None:
        """回滚到指定快照

        Args:
            snapshot_dir: 快照目录路径
        """
        snapshot_dir = Path(snapshot_dir)
        if not snapshot_dir.exists():
            raise FileNotFoundError(f"快照不存在: {snapshot_dir}")

        # world.md
        snap_world = snapshot_dir / "world.md"
        if snap_world.exists():
            shutil.copy2(snap_world, self.world_file)
        # characters
        snap_chars = snapshot_dir / "characters"
        if snap_chars.exists():
            if self.characters_dir.exists():
                safe_remove(self.characters_dir)
            shutil.copytree(snap_chars, self.characters_dir)
        # sublines
        snap_sublines = snapshot_dir / "sublines"
        if snap_sublines.exists():
            if self.sublines_dir.exists():
                safe_remove(self.sublines_dir)
            shutil.copytree(snap_sublines, self.sublines_dir)
        # relations
        snap_relations = snapshot_dir / "relations"
        relations_dir = self.project_dir / "relations"
        if snap_relations.exists():
            if relations_dir.exists():
                safe_remove(relations_dir)
            shutil.copytree(snap_relations, relations_dir)
