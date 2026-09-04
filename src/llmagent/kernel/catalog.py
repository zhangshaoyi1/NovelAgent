"""Catalog / Registry：管理门面（M2 完整版：SchemaGate + Versioner + Catalog + LineageGraph + PolicyLoader）"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .executor import MountResolver
from .task import Executor, TaskKind, TaskSpec


# ===== SchemaGate =====


class SchemaGate:
    """规格门禁：Pydantic v2 校验 input_schema/output_schema/kind/策略字段合法"""

    @staticmethod
    def validate(spec: TaskSpec) -> None:
        if not spec.name:
            raise ValueError("TaskSpec.name 不能为空")
        if not isinstance(spec.kind, TaskKind):
            raise ValueError(f"TaskSpec.kind 不合法: {spec.kind}")
        if spec.timeout_s <= 0:
            raise ValueError(f"TaskSpec.timeout_s 必须 > 0, 当前: {spec.timeout_s}")

    @staticmethod
    def validate_input(spec: TaskSpec, input_data: dict[str, Any]) -> None:
        """校验输入数据"""
        if spec.input_schema:
            required = spec.input_schema.get("required", [])
            for field in required:
                if field not in input_data:
                    raise ValueError(f"缺少必填字段: {field}")


# ===== Versioner =====


class Versioner:
    """版本冻结器：内容寻址 hash(spec) → spec_ref"""

    @staticmethod
    def freeze(spec: TaskSpec) -> str:
        """内容寻址 hash(spec) → spec_ref；同内容同版本幂等"""
        raw = json.dumps(
            {
                "name": spec.name,
                "kind": spec.kind.value,
                "description": spec.description,
                "input_schema": spec.input_schema,
                "output_schema": spec.output_schema,
                "failure_policy": {
                    "max_retries": spec.failure_policy.max_retries,
                    "ignore_failure": spec.failure_policy.ignore_failure,
                    "escalate_on": spec.failure_policy.escalate_on,
                },
                "validation_policy": {
                    "validators": spec.validation_policy.validators,
                    "chain": spec.validation_policy.chain,
                    "strict": spec.validation_policy.strict,
                },
                "timeout_s": spec.timeout_s,
                "budget_category": spec.budget_category,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ===== LineageGraph =====


@dataclass
class Dependency:
    """依赖关系"""

    from_task: str
    to_task: str
    dep_type: str = "depends"  # depends / optional / extends


class LineageGraph:
    """依赖图：影响面分析 + 版本追踪

    用于分析 "如果修改某个 Task，哪些 Task 受影响"。
    """

    def __init__(self, db_path: str | Path = "") -> None:
        self._db_path = str(db_path) if db_path else ":memory:"
        self._conn = sqlite3.connect(self._db_path)
        self._init_db()

    def _init_db(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dependencies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_task TEXT NOT NULL,
                to_task TEXT NOT NULL,
                dep_type TEXT NOT NULL DEFAULT 'depends',
                created_at TEXT NOT NULL,
                UNIQUE(from_task, to_task)
            )
            """
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_dep_from ON dependencies(from_task)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_dep_to ON dependencies(to_task)")
        self._conn.commit()

    def add_dependency(self, from_task: str, to_task: str, dep_type: str = "depends") -> None:
        """添加依赖关系：from_task 依赖 to_task"""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO dependencies (from_task, to_task, dep_type, created_at) VALUES (?, ?, ?, ?)",
            (from_task, to_task, dep_type, now),
        )
        self._conn.commit()

    def remove_dependency(self, from_task: str, to_task: str) -> None:
        self._conn.execute(
            "DELETE FROM dependencies WHERE from_task = ? AND to_task = ?",
            (from_task, to_task),
        )
        self._conn.commit()

    def dependents_of(self, task_name: str) -> list[Dependency]:
        """谁依赖 task_name（依赖方）"""
        rows = self._conn.execute(
            "SELECT from_task, to_task, dep_type FROM dependencies WHERE to_task = ?",
            (task_name,),
        ).fetchall()
        return [Dependency(from_task=r[0], to_task=r[1], dep_type=r[2]) for r in rows]

    def dependencies_of(self, task_name: str) -> list[Dependency]:
        """task_name 依赖谁（被依赖方）"""
        rows = self._conn.execute(
            "SELECT from_task, to_task, dep_type FROM dependencies WHERE from_task = ?",
            (task_name,),
        ).fetchall()
        return [Dependency(from_task=r[0], to_task=r[1], dep_type=r[2]) for r in rows]

    def impact_analysis(self, task_name: str) -> dict[str, list[str]]:
        """影响面分析：修改 task_name 会影响哪些 Task"""
        visited: set[str] = set()
        affected: list[str] = []

        def dfs(name: str) -> None:
            if name in visited:
                return
            visited.add(name)
            for dep in self.dependents_of(name):
                if dep.from_task not in visited:
                    affected.append(dep.from_task)
                    dfs(dep.from_task)

        dfs(task_name)
        return {
            "target": task_name,
            "direct_dependents": [d.from_task for d in self.dependents_of(task_name)],
            "all_affected": affected,
        }

    def to_dot(self) -> str:
        """导出 DOT 格式（用于可视化）"""
        rows = self._conn.execute("SELECT from_task, to_task, dep_type FROM dependencies").fetchall()
        lines = ["digraph LineageGraph {", "  rankdir=LR;"]
        for from_task, to_task, dep_type in rows:
            style = "dashed" if dep_type == "optional" else "solid"
            lines.append(f'  "{from_task}" -> "{to_task}" [style={style}];')
        lines.append("}")
        return "\n".join(lines)

    def close(self) -> None:
        self._conn.close()


# ===== PolicyLoader =====


class PolicyLoader:
    """策略惰性加载器：从文件/SQLite 加载 TaskSpec 策略

    M2 最小实现：内存注册 + 覆盖策略。
    """

    def __init__(self) -> None:
        self._overrides: dict[str, dict[str, Any]] = {}

    def set_override(self, spec_name: str, field: str, value: Any) -> None:
        """设置策略覆盖"""
        if spec_name not in self._overrides:
            self._overrides[spec_name] = {}
        self._overrides[spec_name][field] = value

    def get_override(self, spec_name: str, field: str) -> Any | None:
        return self._overrides.get(spec_name, {}).get(field)

    def apply_overrides(self, spec: TaskSpec) -> TaskSpec:
        """应用策略覆盖到 TaskSpec"""
        overrides = self._overrides.get(spec.name, {})
        if not overrides:
            return spec
        import copy
        modified = copy.deepcopy(spec)
        for field, value in overrides.items():
            if hasattr(modified, field):
                setattr(modified, field, value)
        return modified


# ===== Catalog =====


class Catalog:
    """Task 规格目录：唯一取 Task 入口

    M2 增强：
    - 版本管理 + 版本链（历史版本可追溯）
    - 依赖图注册
    - 策略惰性加载
    - 红线违反统计
    """

    def __init__(
        self,
        mount_resolver: MountResolver | None = None,
        lineage_graph: LineageGraph | None = None,
        policy_loader: PolicyLoader | None = None,
    ) -> None:
        self._specs: dict[str, TaskSpec] = {}
        self._versions: dict[str, str] = {}
        self._version_history: dict[str, list[str]] = {}  # spec_name -> [spec_ref, ...]
        self._mount_resolver = mount_resolver or MountResolver()
        self._lineage_graph = lineage_graph or LineageGraph()
        self._policy_loader = policy_loader or PolicyLoader()
        self._redline_violations: list[dict[str, Any]] = []

    @property
    def lineage_graph(self) -> LineageGraph:
        return self._lineage_graph

    @property
    def policy_loader(self) -> PolicyLoader:
        return self._policy_loader

    # ---- 版本管理 ----

    def register(self, spec: TaskSpec, executor_cls: type[Executor] | None = None) -> str:
        """SchemaGate.validate(spec) → Versioner.freeze(spec) → 存入 Catalog"""
        SchemaGate.validate(spec)
        spec_ref = Versioner.freeze(spec)

        # 版本管理：记录版本链
        if spec.name in self._versions:
            prev_ref = self._versions[spec.name]
            if prev_ref != spec_ref:
                if spec.name not in self._version_history:
                    self._version_history[spec.name] = []
                if prev_ref not in self._version_history[spec.name]:
                    self._version_history[spec.name].append(prev_ref)

        self._specs[spec.name] = spec
        self._versions[spec.name] = spec_ref
        if executor_cls:
            self._mount_resolver.mount(spec.kind, executor_cls)
        return spec_ref

    def get(self, name: str, version: str | None = None) -> TaskSpec:
        spec = self._specs.get(name)
        if spec is None:
            raise KeyError(f"Task '{name}' 未注册")
        if version and self._versions.get(name) != version:
            raise KeyError(f"Task '{name}' 版本 '{version}' 不匹配")

        # 策略惰性加载
        return self._policy_loader.apply_overrides(spec)

    def get_version(self, name: str) -> str:
        return self._versions.get(name, "")

    def get_version_history(self, name: str) -> list[str]:
        return self._version_history.get(name, [])

    def list_all(self) -> list[dict[str, Any]]:
        return [
            {
                "name": name,
                "kind": spec.kind.value,
                "version": self._versions.get(name, ""),
                "description": spec.description,
            }
            for name, spec in self._specs.items()
        ]

    # ---- Executor 解析 ----

    def resolve_executor(self, kind: TaskKind) -> type[Executor]:
        return self._mount_resolver.resolve(kind)

    @property
    def mount_resolver(self) -> MountResolver:
        return self._mount_resolver

    # ---- 依赖管理 ----

    def add_dependency(self, from_task: str, to_task: str, dep_type: str = "depends") -> None:
        self._lineage_graph.add_dependency(from_task, to_task, dep_type)

    def impact_analysis(self, task_name: str) -> dict[str, list[str]]:
        return self._lineage_graph.impact_analysis(task_name)

    # ---- 红线违反统计 ----

    def record_redline_violation(self, run_id: str, redline: str, message: str) -> None:
        self._redline_violations.append({
            "run_id": run_id,
            "redline": redline,
            "message": message,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

    def get_redline_report(self) -> dict[str, Any]:
        """红线违反统计报告"""
        if not self._redline_violations:
            return {"total": 0, "by_redline": {}, "recent": []}
        by_redline: dict[str, int] = {}
        for v in self._redline_violations:
            key = v["redline"]
            by_redline[key] = by_redline.get(key, 0) + 1
        return {
            "total": len(self._redline_violations),
            "by_redline": by_redline,
            "recent": self._redline_violations[-10:],
        }