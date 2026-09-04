"""Planner：ExpansionPolicy + StaticDAG + TemplateRetrieval + Plan IR 校验

M3.3 新增模块。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .task import TaskKind, TaskSpec


# ===== 数据模型 =====


@dataclass
class PlanNode:
    """计划节点"""
    node_id: str = ""
    task_name: str = ""
    task_kind: TaskKind = TaskKind.LLM
    depends_on: list[str] = field(default_factory=list)
    input: dict[str, Any] = field(default_factory=dict)
    budget_category: str = "default"
    timeout_s: float = 300.0
    tags: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.node_id:
            self.node_id = f"n-{uuid.uuid4().hex[:8]}"


@dataclass
class Plan:
    """计划"""
    plan_id: str = ""
    name: str = ""
    nodes: list[PlanNode] = field(default_factory=list)
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.plan_id:
            self.plan_id = f"plan-{uuid.uuid4().hex[:12]}"
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    def add_node(self, node: PlanNode) -> None:
        self.nodes.append(node)

    def validate(self) -> list[str]:
        """校验计划 IR"""
        errors: list[str] = []
        node_ids = {n.node_id for n in self.nodes}
        task_names = {n.task_name for n in self.nodes}

        # 无环校验
        visited: set[str] = set()
        in_stack: set[str] = set()

        def has_cycle(node_id: str, node_map: dict[str, PlanNode]) -> bool:
            visited.add(node_id)
            in_stack.add(node_id)
            node = node_map.get(node_id)
            if node:
                for dep in node.depends_on:
                    if dep not in node_map:
                        continue
                    if dep not in visited:
                        if has_cycle(dep, node_map):
                            return True
                    elif dep in in_stack:
                        errors.append(f"检测到循环依赖: {node_id} -> {dep}")
                        return True
            in_stack.discard(node_id)
            return False

        node_map = {n.node_id: n for n in self.nodes}
        for nid in node_ids:
            if nid not in visited:
                has_cycle(nid, node_map)

        # 依赖存在性校验
        for node in self.nodes:
            for dep in node.depends_on:
                if dep not in node_ids:
                    errors.append(f"节点 {node.node_id} 依赖不存在的节点: {dep}")

        if not self.nodes:
            errors.append("计划为空")

        return errors


# ===== ExpansionPolicy =====


class ExpansionPolicy(Protocol):
    """展开策略协议"""

    def expand(self, plan: Plan, context: dict[str, Any]) -> Plan:
        ...


class StaticDAG:
    """静态 DAG 策略：按依赖关系拓扑排序"""

    def expand(self, plan: Plan, context: dict[str, Any] | None = None) -> Plan:
        """拓扑排序展开"""
        # 计算入度：node.depends_on 列出的是当前节点依赖的前驱节点，
        # 所以当前节点 node.node_id 的入度 = 它的 depends_on 数量
        in_degree: dict[str, int] = {n.node_id: len(n.depends_on) for n in plan.nodes}

        # Kahn 拓扑排序
        queue = [nid for nid, deg in in_degree.items() if deg == 0]
        sorted_nodes: list[PlanNode] = []
        node_map = {n.node_id: n for n in plan.nodes}

        while queue:
            nid = queue.pop(0)
            if nid in node_map:
                sorted_nodes.append(node_map[nid])
                # 遍历所有节点，找到以后继身份依赖 nid 的节点
                for other in plan.nodes:
                    if other.node_id == nid:
                        continue
                    if nid in other.depends_on:
                        oid = other.node_id
                        if oid in in_degree:
                            in_degree[oid] -= 1
                            if in_degree[oid] == 0:
                                queue.append(oid)

        plan.nodes = sorted_nodes
        return plan

    @staticmethod
    def create_linear_plan(task_names: list[str], kind: TaskKind = TaskKind.LLM) -> Plan:
        """创建线性流水线计划"""
        plan = Plan(name="linear_plan")
        prev_node_id: str = ""
        for i, task_name in enumerate(task_names):
            node = PlanNode(
                task_name=task_name,
                task_kind=kind,
                depends_on=[prev_node_id] if prev_node_id else [],
            )
            plan.add_node(node)
            prev_node_id = node.node_id
        return plan

    @staticmethod
    def create_write_chapter_plan() -> Plan:
        """创建写章流水线计划"""
        plan = Plan(name="write_chapter_pipeline")
        outline = PlanNode(task_name="generate_outline", task_kind=TaskKind.LLM)
        write = PlanNode(task_name="write_chapter", task_kind=TaskKind.LLM, depends_on=[outline.node_id])
        review = PlanNode(task_name="review_chapter", task_kind=TaskKind.LLM, depends_on=[write.node_id])
        analyze = PlanNode(task_name="analyze_content", task_kind=TaskKind.LLM, depends_on=[write.node_id])
        plan.add_node(outline)
        plan.add_node(write)
        plan.add_node(review)
        plan.add_node(analyze)
        return plan


# ===== TemplateRetrieval =====


class TemplateRetrieval:
    """模板检索：从历史 Plan 复用"""

    def __init__(self, db_path: str | Path = "") -> None:
        self._db_path = str(db_path) if db_path else ":memory:"
        self._conn = sqlite3.connect(self._db_path)
        self._init_db()

    def _init_db(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS plan_templates (
                template_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                plan_json TEXT NOT NULL,
                usage_count INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                last_used_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def save_template(self, name: str, plan: Plan) -> str:
        """保存计划为模板"""
        template_id = f"tpl-{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()
        nodes_json = [
            {
                "node_id": n.node_id,
                "task_name": n.task_name,
                "task_kind": n.task_kind.value,
                "depends_on": n.depends_on,
                "budget_category": n.budget_category,
                "timeout_s": n.timeout_s,
            }
            for n in plan.nodes
        ]
        self._conn.execute(
            "INSERT OR REPLACE INTO plan_templates (template_id, name, plan_json, usage_count, created_at, last_used_at) VALUES (?, ?, ?, 1, ?, ?)",
            (template_id, name, json.dumps(nodes_json, ensure_ascii=False), now, now),
        )
        self._conn.commit()
        return template_id

    def find_by_name(self, name: str) -> Plan | None:
        """按名称查找模板"""
        row = self._conn.execute(
            "SELECT plan_json FROM plan_templates WHERE name = ? ORDER BY last_used_at DESC LIMIT 1",
            (name,),
        ).fetchone()
        if row is None:
            return None
        return self._json_to_plan(row[0])

    def list_templates(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT template_id, name, usage_count, created_at, last_used_at FROM plan_templates ORDER BY usage_count DESC"
        ).fetchall()
        return [
            {
                "template_id": r[0],
                "name": r[1],
                "usage_count": r[2],
                "created_at": r[3],
                "last_used_at": r[4],
            }
            for r in rows
        ]

    def increment_usage(self, template_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE plan_templates SET usage_count = usage_count + 1, last_used_at = ? WHERE template_id = ?",
            (now, template_id),
        )
        self._conn.commit()

    @staticmethod
    def _json_to_plan(plan_json: str) -> Plan:
        nodes_data = json.loads(plan_json)
        plan = Plan(name="retrieved")
        for nd in nodes_data:
            node = PlanNode(
                node_id=nd["node_id"],
                task_name=nd["task_name"],
                task_kind=TaskKind(nd["task_kind"]),
                depends_on=nd.get("depends_on", []),
                budget_category=nd.get("budget_category", "default"),
                timeout_s=nd.get("timeout_s", 300.0),
            )
            plan.add_node(node)
        return plan

    def close(self) -> None:
        self._conn.close()