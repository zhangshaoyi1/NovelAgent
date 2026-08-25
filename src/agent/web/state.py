"""项目 / 状态 / 看板辅助层（Web UI 用）。

集中封装对 agent 内部（状态机 / AgentService / 题材包）的只读访问，
供 FastAPI 路由与前端页面消费。所有函数对缺失文件 / 异常均做降级，
绝不因观测失败阻断主流程（遵循项目「降级不阻断」哲学）。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

# agent 仓库根（含 projects/ 与 src/），web 包位于 src/agent/web/
# __file__: .../agent/src/agent/web/state.py → parent×4 = agent 仓库根
AGENT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
# 小说数据根：默认 agent 仓库之外的 novels/（与 compose_runner 保持一致）；
# 可用 NOVEL_DATA_ROOT 覆盖。agent 仓库只保留代码，小说数据统一落在 novels/。
PROJECTS_ROOT = Path(
    os.environ.get("NOVEL_DATA_ROOT", str(AGENT_ROOT.parent / "novels"))
)

# 状态机阶段顺序（用于前端进度条渲染）
STATE_FLOW = [
    "INIT",
    "CONFIGURING",
    "DISCUSSING",
    "ARCHITECTING",
    "ARCH_CONFIRMED",
    "OUTLINING",
    "CHARACTER_DESIGN",
    "WRITING",
    "COMPLETED",
]


def project_path(name: str) -> Path:
    """项目绝对路径（已防目录穿越）。"""
    safe = "".join(c for c in name if c.isalnum() or c in "-_")
    return (PROJECTS_ROOT / safe).resolve()


def list_projects() -> list[dict[str, Any]]:
    """扫描 projects/ 下列出所有小说项目及其概要状态。"""
    if not PROJECTS_ROOT.exists():
        return []
    out: list[dict[str, Any]] = []
    for d in sorted(PROJECTS_ROOT.iterdir()):
        if not d.is_dir():
            continue
        info: dict[str, Any] = {
            "name": d.name,
            "state": "INIT",
            "has_world": (d / "world.md").exists(),
            "chapters": 0,
            "updated": None,
        }
        state_file = d / ".state" / "state.json"
        if state_file.exists():
            try:
                sd = json.loads(state_file.read_text(encoding="utf-8"))
                info["state"] = sd.get("state", "INIT")
            except Exception:
                pass
        chapters_dir = d / "chapters"
        if chapters_dir.exists():
            info["chapters"] = len(
                [p for p in chapters_dir.glob("*.md") if p.is_file()]
            )
        try:
            info["updated"] = d.stat().st_mtime
        except Exception:
            pass
        out.append(info)
    return out


def get_project_state(name: str) -> dict[str, Any]:
    """读取项目状态机：当前状态 / 模式 / 进度 / 当前可用命令。"""
    from agent.core.state_machine import StateMachine

    pdir = project_path(name)
    sm = StateMachine(pdir)
    sm.load()
    return {
        "state": sm.state.value,
        "mode": sm.mode,
        "autonomy_level": sm.autonomy_level,
        "progress": sm.progress,
        "available_commands": sm.allowed_commands(),
    }


def get_summary(name: str) -> dict[str, Any]:
    """读取 AgentService 看板快照（成本 / 评测 / 模型路由 / MCP）。"""
    from agent.service.agent_service import AgentService

    try:
        svc = AgentService(project_dir=str(project_path(name)))
        return svc.summarize()
    except Exception as e:  # noqa: BLE001 - 降级不阻断
        return {"error": str(e)}


def list_project_files(name: str) -> list[dict[str, Any]]:
    """列出项目下所有 markdown 文件（排除 .state 内部文件）。"""
    pdir = project_path(name)
    files: list[dict[str, Any]] = []
    if not pdir.exists():
        return files
    for p in sorted(pdir.rglob("*.md")):
        if ".state" in p.parts:
            continue
        rel = str(p.relative_to(pdir))
        files.append({"rel": rel, "size": p.stat().st_size})
    return files


def read_project_file(name: str, rel: str) -> str | None:
    """读取项目内某个 markdown 文件内容（含路径穿越防护）。"""
    pdir = project_path(name)
    target = (pdir / rel).resolve()
    if target != pdir and PROJECTS_ROOT not in target.parents:
        return None
    if not target.exists() or not target.is_file():
        return None
    return target.read_text(encoding="utf-8", errors="replace")


def get_chapters(name: str) -> list[dict[str, Any]]:
    """列出项目 chapters/ 下已生成的章节（按章节号排序）。"""
    pdir = project_path(name)
    chapters_dir = pdir / "chapters"
    out: list[dict[str, Any]] = []
    if not chapters_dir.exists():
        return out
    for p in sorted(chapters_dir.glob("*.md")):
        if not p.is_file():
            continue
        m = re.match(r"(\d+)", p.stem)
        num = int(m.group(1)) if m else 0
        out.append(
            {"num": num, "rel": f"chapters/{p.name}", "name": p.name, "size": p.stat().st_size}
        )
    out.sort(key=lambda x: x["num"])
    return out


def get_command_meta() -> list[dict[str, Any]]:
    """全量命令元数据（名称 / 描述 / 用法 / 门禁），供前端渲染可用操作。"""
    from agent.core.command_router import COMMAND_REGISTRY

    return [
        {
            "name": c.name,
            "description": c.description,
            "usage": c.usage,
            "is_global": c.is_global,
            "allowed_states": [s.value for s in (c.allowed_states or [])],
        }
        for c in COMMAND_REGISTRY
    ]


def list_genres() -> list[dict[str, Any]]:
    """列出已注册题材包（渐进式：仅 id/中文 label/简介，供 UI 多选与表单）。

    返回 [{id, label, description}]，不加载全量内容（成本可控）。
    """
    from agent.core.genre_pack import GenrePackRegistry

    try:
        return GenrePackRegistry().list_genres_light() or []
    except Exception:
        return []


def get_conflicts(name: str) -> dict[str, Any]:
    """读取项目待裁决的题材合并冲突（.state/merge_conflicts.json）。

    返回 {sources, conflicts, pending, total}；无冲突记录时 pending=0。
    """
    from agent.cli.commands.merge_genres import pending_conflicts

    try:
        data = pending_conflicts(project_path(name)) or {}
    except Exception:
        data = {}
    conflicts = data.get("conflicts", []) or []
    total = len(conflicts)
    pending = sum(
        1 for c in conflicts if c.get("resolved_index") is None and not c.get("manual")
    )
    return {
        "sources": data.get("sources", []) or [],
        "conflicts": conflicts,
        "pending": pending,
        "total": total,
    }
