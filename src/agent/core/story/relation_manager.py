"""关系网 / 世界关系图谱管理器

职责：维护可拖拽的「世界关系图谱」，节点按类型分为
人物(character) / 势力(faction) / 地点(location) / 物品(item) / 伏笔(foreshadow)，
边表示任意两节点之间的关系（含强度、方向、暗线标记）。

数据持久化：
    - 主存储：``.state/world_graph.json``（结构化，供 Web 可拖拽图谱消费）
    - 快照：``.state/graph_snapshots/<label>.json``

可视化：
    - ``to_mermaid()`` 输出 Mermaid 力导向图，可供 graph.md 渲染

设计取舍：
    - 本管理器是「交互式世界图谱」的权威存储，与 M6 adjust_relation 维护的
      ``relations/graph.md``（纯角色关系，用于 LLM 一致性演化）互不干扰，
      二者可并存，分别服务于「作者可视化编排」与「自动一致性调整」。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# ============================================================
# 节点类型
# ============================================================
NODE_KINDS: list[str] = ["character", "faction", "location", "item", "foreshadow"]
NODE_KIND_LABELS: dict[str, str] = {
    "character": "人物",
    "faction": "势力",
    "location": "地点",
    "item": "物品",
    "foreshadow": "伏笔",
}
# 节点类型 → 展示色（与前端 graph.html 保持一致）
NODE_KIND_COLORS: dict[str, str] = {
    "character": "#4f46e5",
    "faction": "#dc2626",
    "location": "#16a34a",
    "item": "#d97706",
    "foreshadow": "#7c3aed",
}


@dataclass
class WorldNode:
    """世界图谱节点（人物/势力/地点/物品/伏笔）"""

    id: str
    name: str
    kind: str = "character"          # character/faction/location/item/foreshadow
    description: str = ""
    x: float | None = None           # 画布坐标（拖拽持久化）
    y: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("meta", None)
        d.update(self.meta or {})
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "WorldNode":
        known = {"id", "name", "kind", "description", "x", "y"}
        meta = {k: v for k, v in d.items() if k not in known}
        return cls(
            id=d.get("id", ""),
            name=d.get("name", ""),
            kind=d.get("kind", "character"),
            description=d.get("description", ""),
            x=d.get("x"),
            y=d.get("y"),
            meta=meta,
        )


@dataclass
class WorldEdge:
    """世界图谱关系边"""

    source: str                     # 节点 id
    target: str                     # 节点 id
    relation_type: str = ""         # 关系类型（师徒/敌对/所属/藏匿…）
    strength: int = 50              # 0-100，关系强度
    direction: str = "both"         # one（单向）/ both（双向）
    hidden: bool = False            # 是否暗线（默认不显示标签）
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "WorldEdge":
        return cls(
            source=d.get("source", ""),
            target=d.get("target", ""),
            relation_type=d.get("relation_type", ""),
            strength=int(d.get("strength", 50)),
            direction=d.get("direction", "both"),
            hidden=bool(d.get("hidden", False)),
            note=d.get("note", ""),
        )


@dataclass
class WorldGraph:
    """世界关系图谱"""

    nodes: list[WorldNode] = field(default_factory=list)
    edges: list[WorldEdge] = field(default_factory=list)

    # ------ 写入 ------
    def add_node(self, node: WorldNode) -> None:
        for i, n in enumerate(self.nodes):
            if n.id == node.id:
                self.nodes[i] = node
                return
        self.nodes.append(node)

    def add_edge(self, edge: WorldEdge) -> None:
        for i, e in enumerate(self.edges):
            if (
                e.source == edge.source
                and e.target == edge.target
                and e.relation_type == edge.relation_type
            ):
                self.edges[i] = edge
                return
        self.edges.append(edge)

    def remove_node(self, node_id: str) -> None:
        self.nodes = [n for n in self.nodes if n.id != node_id]
        self.edges = [
            e for e in self.edges if e.source != node_id and e.target != node_id
        ]

    def remove_edge(self, source: str, target: str, relation_type: str = "") -> None:
        self.edges = [
            e
            for e in self.edges
            if not (
                e.source == source
                and e.target == target
                and (not relation_type or e.relation_type == relation_type)
            )
        ]

    # ------ 查询 ------
    def get_node(self, node_id: str) -> WorldNode | None:
        return next((n for n in self.nodes if n.id == node_id), None)

    def subgraph(self, node_ids: list[str]) -> "WorldGraph":
        ids = set(node_ids)
        nodes = [n for n in self.nodes if n.id in ids]
        edges = [e for e in self.edges if e.source in ids and e.target in ids]
        return WorldGraph(nodes=nodes, edges=edges)

    # ------ 序列化 ------
    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "WorldGraph":
        return cls(
            nodes=[WorldNode.from_dict(n) for n in d.get("nodes", [])],
            edges=[WorldEdge.from_dict(e) for e in d.get("edges", [])],
        )

    def to_mermaid(self) -> str:
        """渲染为 Mermaid 力导向图（供文档导出）"""
        lines = ["graph LR"]
        for n in self.nodes:
            label = f"{n.name}（{NODE_KIND_LABELS.get(n.kind, n.kind)}）"
            lines.append(f'    {n.id}["{label}"]')
        for e in self.edges:
            arrow = "->" if e.direction == "one" else "<->"
            tag = e.relation_type or "关联"
            lines.append(
                f'    {e.source} {arrow}|{tag}·{e.strength}| {e.target}'
            )
        return "\n".join(lines)


# ============================================================
# 管理器
# ============================================================
class RelationManager:
    """世界关系图谱管理器（持久化 + 演化 + 手动调整）"""

    def __init__(self, project_dir: Path | str) -> None:
        self.project_dir = Path(project_dir)
        self.store_dir = self.project_dir / ".state"
        self.graph_file = self.store_dir / "world_graph.json"
        self.snapshots_dir = self.store_dir / "graph_snapshots"
        self.graph = WorldGraph()

    # ------ 持久化 ------
    def load(self) -> bool:
        """从 world_graph.json 加载；不存在返回 False（不报错）"""
        if not self.graph_file.exists():
            return False
        try:
            data = json.loads(self.graph_file.read_text(encoding="utf-8"))
            self.graph = WorldGraph.from_dict(data)
            return True
        except Exception:  # noqa: BLE001 - 损坏文件降级为空图
            self.graph = WorldGraph()
            return False

    def save(self) -> Path:
        """保存图谱到 world_graph.json（含节点坐标）"""
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.graph_file.write_text(
            json.dumps(self.graph.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return self.graph_file

    def exists(self) -> bool:
        return self.graph_file.exists()

    # ------ 演化（best-effort，永不抛错）------
    def evolve(self, chapter_text: str, chapter_ctx: dict[str, Any]) -> list[str]:
        """章节产出后自动演化关系（轻量启发式：仅记录被同时提及的节点）

        说明：完整的语义级关系抽取由 M6 adjust_relation 承担；此处仅做
        非破坏性的「共现」提示，便于作者后续编排。
        """
        changes: list[str] = []
        mentioned = {
            n.id for n in self.graph.nodes if n.name and n.name in (chapter_text or "")
        }
        if len(mentioned) >= 2:
            changes.append(
                f"本章共提及 {len(mentioned)} 个图谱节点，可在图谱中补充/加强关系。"
            )
        return changes

    # ------ 手动调整 ------
    def manual_adjust(
        self,
        source: str,
        target: str,
        relation_type: str | None = None,
        strength: int | None = None,
        direction: str | None = None,
        hidden: bool | None = None,
        note: str | None = None,
    ) -> WorldEdge:
        """创建或更新一条关系边"""
        existing = next(
            (
                e
                for e in self.graph.edges
                if e.source == source and e.target == target
            ),
            None,
        )
        if existing:
            if relation_type is not None:
                existing.relation_type = relation_type
            if strength is not None:
                existing.strength = max(0, min(100, int(strength)))
            if direction is not None:
                existing.direction = direction
            if hidden is not None:
                existing.hidden = hidden
            if note is not None:
                existing.note = note
            edge = existing
        else:
            edge = WorldEdge(
                source=source,
                target=target,
                relation_type=relation_type or "",
                strength=strength if strength is not None else 50,
                direction=direction or "both",
                hidden=bool(hidden) if hidden is not None else False,
                note=note or "",
            )
            self.graph.add_edge(edge)
        self.save()
        return edge

    def upsert_node(
        self,
        node_id: str,
        name: str,
        kind: str = "character",
        description: str = "",
        x: float | None = None,
        y: float | None = None,
    ) -> WorldNode:
        """创建或更新节点"""
        node = WorldNode(
            id=node_id,
            name=name,
            kind=kind if kind in NODE_KINDS else "character",
            description=description,
            x=x,
            y=y,
        )
        self.graph.add_node(node)
        self.save()
        return node

    def update_positions(self, positions: dict[str, dict[str, float]]) -> None:
        """批量更新节点拖拽坐标（仅改坐标，不动其他字段）"""
        for nid, pos in positions.items():
            n = self.graph.get_node(nid)
            if n is not None:
                n.x = pos.get("x")
                n.y = pos.get("y")
        self.save()

    # ------ 快照 ------
    def create_snapshot(self, label: str) -> Path:
        """创建图谱快照（JSON 副本）"""
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        snap = self.snapshots_dir / f"{label}.json"
        snap.write_text(
            json.dumps(self.graph.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return snap

    # ------ 示例种子（首次打开空图谱时一键填充）------
    def seed_sample(self) -> None:
        """填充一套示例世界图谱（人物/势力/地点/物品/伏笔）"""
        sample_nodes = [
            ("lin_xun", "林寻", "character", "本作主角，法医出身的殡仪馆主理人"),
            ("zhao_wuji", "赵无极", "character", "长安城神秘权贵，亦正亦邪"),
            ("zhou_bosheng", "周伯生", "character", "老仵作，林寻的引路人"),
            ("yama_hall", "阎罗殿", "faction", "盘踞长安地下的幽冥势力"),
            ("changan", "长安城", "location", "故事主舞台，盛唐气象下的暗流"),
            ("bin_yi_guan", "殡仪馆", "location", "林寻经营之处，阴阳交界处"),
            ("soul_token", "引魂幡", "item", "可通阴阳的秘宝"),
            ("f01", "鬼不可怕", "foreshadow", "核心主题伏笔：真正可怕的是人心"),
        ]
        sample_edges = [
            ("lin_xun", "zhou_bosheng", "师徒", 80, "both", False, "引路之恩"),
            ("lin_xun", "zhao_wuji", "博弈", 55, "both", False, "彼此试探"),
            ("zhao_wuji", "yama_hall", "掌控", 90, "one", False, "幕后主使"),
            ("lin_xun", "bin_yi_guan", "经营", 100, "both", False, "主业所在"),
            ("yama_hall", "changan", "盘踞", 70, "one", False, "地下势力范围"),
            ("lin_xun", "soul_token", "持有", 85, "one", False, "秘宝认主"),
            ("f01", "lin_xun", "呼应", 60, "one", True, "主题暗线"),
        ]
        for nid, name, kind, desc in sample_nodes:
            self.graph.add_node(WorldNode(id=nid, name=name, kind=kind, description=desc))
        for s, t, rt, st, dr, hd, nt in sample_edges:
            self.graph.add_edge(
                WorldEdge(
                    source=s,
                    target=t,
                    relation_type=rt,
                    strength=st,
                    direction=dr,
                    hidden=hd,
                    note=nt,
                )
            )
        self.save()

    # ------ 从项目数据自动生成 ------
    # graph.md 分组 → 节点类型（graph.md 只记录角色）
    _GROUP_KIND = {
        "protagonist": "character",
        "antagonist": "character",
        "mentor": "character",
        "supporting": "character",
    }

    def sync_from_project(self) -> dict[str, int]:
        """根据项目当前数据自动生成世界图谱。

        数据来源（存在才用，逐项 best-effort）：
            - ``relations/graph.md``：节点表（ID/角色/分组）与边表（起/止/类型/强度/备注）
            - ``characters/*.md``：补齐角色描述（身份/势力 frontmatter）
            - ``foreshadows.md``：伏笔登记表 → 伏笔节点 + 到关联角色的暗线边

        已有节点按名称保留拖拽坐标与人工编辑的描述，未被删除。
        """
        old_by_name = {n.name: n for n in self.graph.nodes}
        nodes: dict[str, WorldNode] = {}
        edges: list[WorldEdge] = []
        id_to_name: dict[str, str] = {}

        def _make_node(node_id: str, name: str, kind: str, desc: str = "") -> WorldNode:
            old = old_by_name.get(name)
            return WorldNode(
                id=node_id,
                name=name,
                kind=old.kind if old else (kind if kind in NODE_KINDS else "character"),
                description=old.description if old else desc,
                x=old.x if old else None,
                y=old.y if old else None,
            )

        # 1) relations/graph.md —— 节点表
        graph_md = self.project_dir / "relations" / "graph.md"
        if graph_md.exists():
            try:
                text = graph_md.read_text(encoding="utf-8")
                node_sec = self._md_section(text, "节点")
                for m in re.finditer(
                    r"^\|\s*([A-Za-z0-9_\-]+)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|",
                    node_sec,
                    re.M,
                ):
                    rid, name, group = m.group(1), m.group(2), m.group(3)
                    if set(rid) <= {"-"} or name == "角色":  # 跳过表头/分隔行
                        continue
                    id_to_name[rid] = name
                    nodes[name] = _make_node(rid, name, self._GROUP_KIND.get(group.strip(), "character"))

                # 2) relations/graph.md —— 边表
                edge_sec = self._md_section(text, "边")
                for m in re.finditer(
                    r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|\s*(\d+)\s*\|[^|]*\|?([^|]*)\|",
                    edge_sec,
                    re.M,
                ):
                    src, dst, rtype, strength, note = (
                        m.group(1).strip(),
                        m.group(2).strip(),
                        m.group(3).strip(),
                        m.group(4).strip(),
                        m.group(5).strip() if m.group(5) else "",
                    )
                    if set(src) <= {"-"} or src == "起":  # 跳过表头/分隔行
                        continue
                    s_name, t_name = id_to_name.get(src, src), id_to_name.get(dst, dst)
                    if s_name not in nodes or t_name not in nodes:
                        continue
                    edges.append(
                        WorldEdge(
                            source=nodes[s_name].id,
                            target=nodes[t_name].id,
                            relation_type=rtype or "关联",
                            strength=min(100, max(0, int(strength) * 10)),
                            direction="one",
                            hidden=False,
                            note=note.strip("| ").strip(),
                        )
                    )
            except Exception:  # noqa: BLE001 - 图谱同步是 best-effort
                pass

        # 3) characters/*.md —— 补充未入网的角色 + frontmatter 描述
        chars_dir = self.project_dir / "characters"
        if chars_dir.exists():
            used_ids = {n.id for n in nodes.values()}
            for fp in sorted(chars_dir.glob("*.md")):
                try:
                    text = fp.read_text(encoding="utf-8")
                except Exception:  # noqa: BLE001
                    continue
                name_m = re.search(r'^name:\s*"?(.+?)"?\s*$', text, re.M)
                faction_m = re.search(r'^faction:\s*"?(.+?)"?\s*$', text, re.M)
                role_m = re.search(r'^role:\s*"?(.+?)"?\s*$', text, re.M)
                name = (name_m.group(1).strip() if name_m else fp.stem)
                if name in nodes:
                    if faction_m and not nodes[name].description:
                        nodes[name].description = f"势力：{faction_m.group(1).strip()}"
                    continue
                # 名称近似去重（如「魏长风（魏老）」vs「魏长风」）
                dup = next((nm for nm in nodes if name in nm or nm in name), None)
                if dup is not None:
                    if faction_m and not nodes[dup].description:
                        nodes[dup].description = f"势力：{faction_m.group(1).strip()}"
                    continue
                nid = "c_" + re.sub(r"\W+", "_", name) or fp.stem
                base = nid
                i = 2
                while nid in used_ids:
                    nid = f"{base}_{i}"
                    i += 1
                used_ids.add(nid)
                desc = ""
                if faction_m:
                    desc += f"势力：{faction_m.group(1).strip()}"
                if role_m:
                    desc += f" · 定位：{role_m.group(1).strip()}"
                nodes[name] = _make_node(nid, name, "character", desc.strip(" ·"))

        # 4) foreshadows.md —— 伏笔节点 + 到关联角色的暗线
        fs_md = self.project_dir / "foreshadows.md"
        if fs_md.exists():
            try:
                text = fs_md.read_text(encoding="utf-8")
                used_ids = {n.id for n in nodes.values()}
                for m in re.finditer(
                    r"^\|\s*(F-\d+)\s*\|\s*([^|]+?)\s*\|[^|]*\|[^|]*\|[^|]*\|\s*([^|]*)\|",
                    text,
                    re.M,
                ):
                    fid, content, rels = m.group(1), m.group(2).strip(), m.group(3).strip()
                    if fid == "ID":
                        continue
                    if fid not in used_ids:
                        # 画布标签取短（完整内容放 description / 悬浮提示）
                        short = content if len(content) <= 10 else content[:9] + "…"
                        node = _make_node(fid, f"伏笔·{short}", "foreshadow", content)
                        # 伏笔节点可能撞名，id 用 fid
                        nodes.setdefault(f"__fs__{fid}", node)
                        used_ids.add(fid)
                    for role in re.split(r"[,，、]", rels):
                        role = role.strip()
                        if not role:
                            continue
                        target = next(
                            (n for nm, n in nodes.items() if role in nm or nm in role),
                            None,
                        )
                        if target is not None:
                            edges.append(
                                WorldEdge(
                                    source=fid,
                                    target=target.id,
                                    relation_type="关联伏笔",
                                    strength=40,
                                    direction="one",
                                    hidden=True,
                                    note="",
                                )
                            )
            except Exception:  # noqa: BLE001
                pass

        self.graph = WorldGraph(nodes=list(nodes.values()), edges=edges)
        self.save()
        return {"nodes": len(self.graph.nodes), "edges": len(self.graph.edges)}

    @staticmethod
    def _md_section(text: str, title: str) -> str:
        """截取 markdown 中 `## <title>` 到下一个 `##` 之前的内容"""
        m = re.search(rf"^##\s*{re.escape(title)}.*?$", text, re.M)
        if not m:
            return ""
        rest = text[m.end():]
        nxt = re.search(r"^##\s", rest, re.M)
        return rest[: nxt.start()] if nxt else rest


# 便捷导出，供需要时引用
__all__ = [
    "NODE_KINDS",
    "NODE_KIND_LABELS",
    "NODE_KIND_COLORS",
    "WorldNode",
    "WorldEdge",
    "WorldGraph",
    "RelationManager",
]
