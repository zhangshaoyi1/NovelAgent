"""E 项目学习闭环存储（增量 E / T05）

``LearningStore`` 读写 ``.state/learnings/learnings.json``（项目长期记忆），
严格遵循 ``InjectedTropeStore`` 存储范式：独立 ``.state/`` 文件 + ``safe_remove``
+ 解析失败降级为空、不阻断写章。

与 ``InjectedTropeStore`` 的区别：learnings 是**长期保留**的项目记忆（写前注入 M5），
不是一次性运行期上下文；故 ``clear`` 需用户显式触发，写章不会自动清空。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.utils import safe_remove


@dataclass
class Learning:
    """一条学习沉淀（可复用的写法 / 钩子 / 节奏模板）"""

    id: str
    category: str = "general"   # hook / pacing / character / style / general
    text: str = ""
    source_chapters: list[int] = field(default_factory=list)  # 来源章节（供审阅/删除）
    created_at: str = ""


class LearningStore:
    """学习沉淀存储（.state/learnings/learnings.json）"""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = Path(project_dir)
        self.file = self.project_dir / ".state" / "learnings" / "learnings.json"

    # ============================================================
    # 读写（损坏降级空，绝不抛异常）
    # ============================================================
    def load(self) -> list[Learning]:
        """加载全部学习沉淀；文件缺失/损坏一律降级为空列表"""
        if not self.file.exists():
            return []
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, ValueError, TypeError):
            return []
        out: list[Learning] = []
        for d in (data.get("learnings") or []):
            if not isinstance(d, dict):
                continue
            out.append(Learning(
                id=str(d.get("id", "")),
                category=str(d.get("category", "general")),
                text=str(d.get("text", "")),
                source_chapters=[int(x) for x in (d.get("source_chapters") or [])],
                created_at=str(d.get("created_at", "")),
            ))
        return out

    def save(self, items: list[Learning]) -> None:
        """持久化学习沉淀列表"""
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(
            json.dumps(
                {"learnings": [asdict(x) for x in items]},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    # ============================================================
    # 增量操作
    # ============================================================
    def add(
        self,
        category: str,
        text: str,
        source_chapters: list[int] | None = None,
        learning_id: str | None = None,
    ) -> Learning:
        """新增一条学习沉淀（同 category+text 去重），返回新增/已存在的条目"""
        items = self.load()
        # 去重：同 category + 同 text 视为重复
        for x in items:
            if x.category == category and x.text == text:
                return x
        lid = learning_id or f"L-{len(items) + 1:03d}"
        item = Learning(
            id=lid,
            category=category,
            text=text,
            source_chapters=list(source_chapters or []),
            created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        items.append(item)
        self.save(items)
        return item

    def list(self) -> list[Learning]:
        """返回全部学习沉淀"""
        return self.load()

    def clear(self) -> int:
        """清空学习沉淀（删除文件），返回被清除条数"""
        n = len(self.load())
        if self.file.exists():
            safe_remove(self.file)
        return n
