"""关系网管理器

职责：维护角色关系图，支持自动演化与手动维护，输出 Mermaid 可视化。

数据结构：
    - 节点：角色（id, name, faction, realm）
    - 边：关系（source, target, type, strength, hidden）

关系类型示例：师徒 / 朋友 / 敌人 / 恋人 / 同门 / 血亲

演化触发：
    - 章节产出后扫描关系事件 → 自动更新
    - 用户手动调整命令 /adjust-relation
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CharacterNode:
    """角色节点"""

    id: str
    name: str
    faction: str = ""
    realm: str = ""          # 境界（修仙用）
    is_protagonist: bool = False


@dataclass
class RelationEdge:
    """关系边"""

    source: str              # 角色 id
    target: str              # 角色 id
    relation_type: str       # 师徒/朋友/敌人/恋人/同门/血亲
    strength: int = 50       # 0-100，强度
    hidden: bool = False     # 是否暗线关系
    note: str = ""           # 关系说明


@dataclass
class RelationGraph:
    """关系图"""

    nodes: list[CharacterNode] = field(default_factory=list)
    edges: list[RelationEdge] = field(default_factory=list)

    def add_node(self, node: CharacterNode) -> None:
        self.nodes.append(node)

    def add_edge(self, edge: RelationEdge) -> None:
        self.edges.append(edge)

    def subgraph(self, character_ids: list[str]) -> "RelationGraph":
        """提取子图（用于上下文加载）"""
        # TODO: 实现
        raise NotImplementedError

    def to_mermaid(self) -> str:
        """渲染为 Mermaid graph 代码"""
        # TODO: 实现
        raise NotImplementedError


class RelationManager:
    """关系网管理器"""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.graph_file = project_dir / "relations" / "graph.md"
        self.snapshots_dir = project_dir / "relations" / "snapshots"
        self.graph = RelationGraph()

    def load(self) -> None:
        """从 graph.md 加载关系网"""
        # TODO: 解析结构化数据 + Mermaid
        raise NotImplementedError

    def save(self) -> None:
        """保存关系网到 graph.md"""
        # TODO: 渲染 Mermaid + 结构化数据
        raise NotImplementedError

    def evolve(self, chapter_text: str, chapter_ctx: dict[str, Any]) -> list[str]:
        """章节产出后自动演化关系

        Args:
            chapter_text: 章节正文
            chapter_ctx: 章节上下文（涉及角色等）

        Returns:
            变更描述列表（用于输出给用户）
        """
        # TODO: 扫描章节中的关系事件关键词，更新边
        raise NotImplementedError

    def manual_adjust(
        self,
        source: str,
        target: str,
        relation_type: str | None = None,
        strength: int | None = None,
    ) -> None:
        """用户手动调整关系"""
        # TODO: 实现
        raise NotImplementedError

    def create_snapshot(self, label: str) -> Path:
        """创建关系网快照"""
        # TODO: 实现
        raise NotImplementedError
