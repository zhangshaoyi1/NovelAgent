"""技法资产库（G15 P0-4 技法工程化）

对标 DeepWrite `learning-imitation`：三阶段学习仿写的产出（六槽位）先落「预览区」，
确认后才进入「技法资产库」，未确认不入库。严格沿用存储范式：
独立 ``.state/learning/`` 文件 + ``safe_remove`` + 损坏降级为空、不阻断。

- 预览区：``.state/learning/preview/<asset_id>.json``（临时，可清空）。
- 资产库：``.state/learning/library.json``（确认后的可召回技法资产）。

依赖规则：仅依赖 base / 标准库，放置于引擎层 core/story/。
"""

from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.utils import safe_remove

_SLOTS = ("gimmick", "character", "pacing", "intro", "plot_refine", "draft_excerpt")


@dataclass
class TechniqueAsset:
    """一条技法资产（六槽位可召回）"""

    id: str
    title: str = ""
    category: str = "general"        # gimmick / character / pacing / intro / plot_refine / general
    source: list = field(default_factory=list)   # 来源样本 id/章节
    is_common: bool = False          # 共性（≥2 篇）还是变体（单篇）
    occurrences: int = 1             # 出现的样本数（≥2 才算共性）
    slots: dict = field(default_factory=dict)    # 六槽位
    created_at: str = ""


class TechniqueStore:
    """技法资产库 + 预览区（先预览后确认）。"""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = Path(project_dir)
        self.preview_dir = self.project_dir / ".state" / "learning" / "preview"
        self.library_file = self.project_dir / ".state" / "learning" / "library.json"

    # ---------------- 预览区 ----------------
    def preview_dir_exists(self) -> bool:
        return self.preview_dir.exists()

    def write_preview(self, asset: TechniqueAsset) -> TechniqueAsset:
        """把待确认资产写入预览区（不修改资产库）。"""
        if not asset.id:
            asset.id = "asset-" + uuid.uuid4().hex[:8]
        self.preview_dir.mkdir(parents=True, exist_ok=True)
        (self.preview_dir / f"{asset.id}.json").write_text(
            json.dumps(asdict(asset), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return asset

    def list_preview(self) -> list[TechniqueAsset]:
        if not self.preview_dir_exists():
            return []
        out: list[TechniqueAsset] = []
        for f in sorted(self.preview_dir.glob("*.json")):
            a = self._load_preview_file(f)
            if a is not None:
                out.append(a)
        return out

    def confirm(self, asset_id: str) -> TechniqueAsset | None:
        """确认预览资产：写入资产库并从预览区移除；未在预览区则报 None。"""
        fp = self.preview_dir / f"{asset_id}.json"
        if not fp.exists():
            return None
        a = self._load_preview_file(fp)
        if a is None:
            return None
        self.add_to_library(a)
        safe_remove(fp)
        return a

    def confirm_all(self) -> list[TechniqueAsset]:
        confirmed = [a for a in self.list_preview() if self.add_to_library(a)]
        if self.preview_dir_exists():
            shutil.rmtree(self.preview_dir, ignore_errors=True)
        return confirmed

    def clear_preview(self) -> int:
        if not self.preview_dir_exists():
            return 0
        n = len(list(self.preview_dir.glob("*.json")))
        shutil.rmtree(self.preview_dir, ignore_errors=True)
        return n

    # ---------------- 资产库 ----------------
    def load_library(self) -> list[TechniqueAsset]:
        if not self.library_file.exists():
            return []
        try:
            data = json.loads(self.library_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, ValueError, TypeError):
            return []
        out: list[TechniqueAsset] = []
        for d in (data.get("assets") or []):
            if not isinstance(d, dict):
                continue
            out.append(TechniqueAsset(
                id=str(d.get("id", "")),
                title=str(d.get("title", "")),
                category=str(d.get("category", "general")),
                source=list(d.get("source") or []),
                is_common=bool(d.get("is_common", False)),
                occurrences=int(d.get("occurrences", 1)),
                slots=dict(d.get("slots") or {}),
                created_at=str(d.get("created_at", "")),
            ))
        return out

    def add_to_library(self, asset: TechniqueAsset) -> TechniqueAsset:
        items = self.load_library()
        # 同 id / 同 title+category 去重（覆写）
        items = [x for x in items if x.id != asset.id]
        if not asset.created_at:
            asset.created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        items.append(asset)
        self._save_library(items)
        return asset

    def _save_library(self, items: list[TechniqueAsset]) -> None:
        self.library_file.parent.mkdir(parents=True, exist_ok=True)
        self.library_file.write_text(
            json.dumps({"assets": [asdict(x) for x in items]},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def clear_library(self) -> int:
        n = len(self.load_library())
        if self.library_file.exists():
            safe_remove(self.library_file)
        return n

    # ---------------- 内部 ------------
    def _load_preview_file(self, f: Path) -> TechniqueAsset | None:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, ValueError, TypeError):
            return None
        if not isinstance(d, dict):
            return None
        return TechniqueAsset(
            id=str(d.get("id", "")),
            title=str(d.get("title", "")),
            category=str(d.get("category", "general")),
            source=list(d.get("source") or []),
            is_common=bool(d.get("is_common", False)),
            occurrences=int(d.get("occurrences", 1)),
            slots=dict(d.get("slots") or {}),
            created_at=str(d.get("created_at", "")),
        )


SLOT_NAMES = list(_SLOTS)


__all__ = ["TechniqueAsset", "TechniqueStore", "SLOT_NAMES"]