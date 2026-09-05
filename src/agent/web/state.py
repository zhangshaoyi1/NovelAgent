"""项目 / 状态 / 看板辅助层（Web UI 用）。

集中封装对 agent 内部（状态机 / AgentService / 题材包）的只读访问，
供 FastAPI 路由与前端页面消费。所有函数对缺失文件 / 异常均做降级，
绝不因观测失败阻断主流程（遵循项目「降级不阻断」哲学）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# agent 仓库根（含 projects/ 与 src/），web 包位于 src/agent/web/
# __file__: .../agent/src/agent/web/state.py → parent×4 = agent 仓库根
AGENT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def projects_root() -> Path:
    """当前项目空间的小说数据根（由 workspace 模块动态提供，可切换）。

    默认 agent 仓库之外的 novels/（与 compose_runner 保持一致）；
    NOVEL_DATA_ROOT 环境变量覆盖默认值，Web 端可在「项目空间设置」
    里登记多个本地目录并随时切换。
    """
    from agent.web.workspace import active_root

    return active_root()

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

# 状态机阶段中文标签 + 一句话说明（项目工作台文案用）
STATE_META: dict[str, dict[str, str]] = {
    "INIT": {"label": "初始化", "desc": "创建项目，生成世界观设定集"},
    "CONFIGURING": {"label": "设定世界", "desc": "正在汇集体量 / 题材 / 核心梗"},
    "DISCUSSING": {"label": "脉络讨论", "desc": "与 Agent 讨论故事脉络与关键设定"},
    "ARCHITECTING": {"label": "故事架构", "desc": "生成整体故事架构初稿"},
    "ARCH_CONFIRMED": {"label": "架构确认", "desc": "确认故事架构，解锁后续阶段"},
    "OUTLINING": {"label": "创作大纲", "desc": "生成章节大纲"},
    "CHARACTER_DESIGN": {"label": "角色设计", "desc": "设计主要角色与关系"},
    "WRITING": {"label": "写作中", "desc": "逐章推进正文"},
    "COMPLETED": {"label": "已完本", "desc": "正文已完成，可复核或续写"},
}

# 每个状态唯一的「推荐下一步」动作（命令名含前导斜杠）
RECOMMENDED_ACTION: dict[str, str | None] = {
    "INIT": "/start",
    "CONFIGURING": "/discuss",
    "DISCUSSING": "/discuss",
    "ARCHITECTING": "/architecture",
    "ARCH_CONFIRMED": "/confirm-architecture",
    "OUTLINING": "/outline",
    "CHARACTER_DESIGN": "/design-characters",
    "WRITING": "/write",
    "COMPLETED": None,
}

# 推荐命令的可读动作标签
ACTION_LABEL: dict[str, str] = {
    "/start": "初始化世界设定",
    "/discuss": "开展脉络讨论",
    "/architecture": "构建故事架构",
    "/confirm-architecture": "确认故事架构",
    "/outline": "生成章节大纲",
    "/design-characters": "设计主要角色",
    "/write": "写下一章",
    "/autowrite": "一键续写",
}

# 不需要额外自定义参数、可直接一键运行的推荐命令
SAFE_DIRECT_RUN: set[str] = {
    "/start",
    "/architecture",
    "/confirm-architecture",
    "/outline",
    "/design-characters",
}
# 直接运行命令时的默认参数（避免交互式提示卡住）
NEXT_ARGV: dict[str, list[str]] = {
    "/confirm-architecture": ["--yes"],
    "/architecture": [],
    "/outline": [],
    "/design-characters": [],
    "/start": [],
}


def build_next_action(state_val: str, available_commands: list[str]) -> dict[str, Any]:
    """由当前状态推导「推荐下一步」：供工作台首屏唯一主 CTA 使用。

    type 取值：
    - "run"   : 直接一键运行（无需自定义输入）
    - "guide" : 需引导输入（如 discuss 的切入点）→ 跳转引导向导
    - "write" : 写下一章 → 跳转实时写作间
    - "done"  : 已完本，无推进动作
    """
    meta = STATE_META.get(state_val, {})
    cmd = RECOMMENDED_ACTION.get(state_val)
    base = {
        "cmd": cmd,
        "label": ACTION_LABEL.get(cmd, "") if cmd else "",
        "state_label": meta.get("label", state_val),
        "state_desc": meta.get("desc", ""),
    }
    if not cmd:
        return {
            **base,
            "type": "done",
            "enabled": False,
            "desc": "创作闭环已完成，可复核正文、查看看板，或开启新书。",
        }
    enabled = cmd in available_commands
    if not enabled:
        return {
            **base,
            "type": "done",
            "enabled": False,
            "desc": meta.get("desc", ""),
        }
    if cmd in ("/write", "/autowrite"):
        return {**base, "type": "write", "enabled": True, "desc": meta.get("desc", "")}
    if cmd in SAFE_DIRECT_RUN:
        return {
            **base,
            "type": "run",
            "enabled": True,
            "argv": NEXT_ARGV.get(cmd, []),
            "desc": meta.get("desc", ""),
        }
    return {**base, "type": "guide", "enabled": True, "desc": meta.get("desc", "")}


def _tokens_total(name: str) -> int:
    """累计消耗 tokens（从 llmops 看板快照读取，缺失降级为 0）。"""
    try:
        return int(get_summary(name).get("trace_totals", {}).get("tokens_total") or 0)
    except Exception:  # noqa: BLE001 - 降级不阻断
        return 0


def _tokens_cached(name: str) -> int:
    """累计缓存命中 tokens（prompt cache，缺失降级为 0）。"""
    try:
        return int(get_summary(name).get("trace_totals", {}).get("tokens_cached") or 0)
    except Exception:  # noqa: BLE001 - 降级不阻断
        return 0


def _genre_label(name: str) -> str:
    """题材标签（尽力而为：state.json 的 genres 字段 → 题材合并来源 → '—'）。"""
    pdir = project_path(name)
    try:
        sd = json.loads((pdir / ".state" / "state.json").read_text(encoding="utf-8"))
        g = sd.get("genres") or sd.get("genre")
        if isinstance(g, list):
            g = ", ".join(str(x) for x in g if x)
        if g:
            return str(g)
    except Exception:  # noqa: BLE001
        pass
    srcs = get_conflicts(name).get("sources") or []
    if srcs:
        return ", ".join(srcs)
    return "—"


def project_path(name: str) -> Path:
    """项目绝对路径（已防目录穿越）。"""
    safe = "".join(c for c in name if c.isalnum() or c in "-_")
    return (projects_root() / safe).resolve()


def list_projects() -> list[dict[str, Any]]:
    """扫描 projects/ 下列出所有小说项目及其概要状态。"""
    if not projects_root().exists():
        return []
    out: list[dict[str, Any]] = []
    for d in sorted(projects_root().iterdir()):
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
    """读取项目状态机：当前状态 / 模式 / 进度 / 当前可用命令（含工作台展示字段）。"""
    from agent.core.engine.state_machine import StateMachine

    pdir = project_path(name)
    sm = StateMachine(pdir)
    sm.load()
    meta = STATE_META.get(sm.state.value, {})
    return {
        "state": sm.state.value,
        "state_label": meta.get("label", sm.state.value),
        "state_desc": meta.get("desc", ""),
        "mode": sm.mode,
        "autonomy_level": sm.autonomy_level,
        "progress": sm.progress,
        "available_commands": sm.allowed_commands(),
        "chapters_count": len(get_chapters(name)),
        "tokens_total": _tokens_total(name),
        "tokens_cached": _tokens_cached(name),
        "genre": _genre_label(name),
        "has_world": (pdir / "world.md").exists(),
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
    if target != pdir and projects_root() not in target.parents:
        return None
    if not target.exists() or not target.is_file():
        return None
    return target.read_text(encoding="utf-8", errors="replace")


# 引导向导「阶段 → 可编辑产物文件」：允许用户对照流程回显 / 编辑 / 保存本地。
# value: (相对路径 or 目录前缀, 对应的生成命令)
STAGE_FILE = {
    "world": ("world.md", "/start"),
    "discussion": ("discussion.md", "/discuss"),
    "architecture": ("architecture.md", "/architecture"),
    "outline": ("outline.md", "/outline"),
    "characters": ("characters/", "/design-characters"),
}

# 保存时允许写回的相对路径（白名单，防路径穿越）
_EDITABLE_FILES = {"world.md", "discussion.md", "architecture.md", "outline.md"}


def stage_cmd_for_rel(rel: str) -> str:
    """由文件名反查该产物对应的生成命令（白名单回落到 /start）。"""
    if rel in _EDITABLE_FILES:
        for _k, (path, cmd) in STAGE_FILE.items():
            if path == rel:
                return cmd
    if rel.startswith("characters/") and rel.count("/") == 1:
        return "/design-characters"
    return "/start"


def stage_writable(name: str, rel: str, avail: list[str]) -> bool:
    """阶段产物是否允许编辑/保存：文件已存在，或对应的生成命令当前可用。

    这样「已生成 → 可随时微调」，且「当前流程已解锁的阶段」也可直接编辑，
    但不会让用户误写尚未解锁的后续阶段。
    """
    if not rel:
        return False
    pdir = project_path(name)
    target = (pdir / rel).resolve()
    exists = target.exists() and target.is_file()
    return exists or stage_cmd_for_rel(rel) in avail


def write_project_file(name: str, rel: str, content: str, avail: list[str]) -> tuple[bool, str]:
    """将编辑后的阶段产物写回本地（门禁 + 白名单 + 路径穿越防护）。

    Returns:
        (ok, message)
    """
    if not rel:
        return False, "缺少文件路径"
    # 白名单校验：只能是 {STAGE_FILE} 列出的产物或其 characters/ 下的直属 md
    chars_prefix = rel.startswith("characters/") and rel.count("/") == 1 and rel.endswith(".md")
    if rel not in _EDITABLE_FILES and not chars_prefix:
        return False, f"不允许编辑该文件：{rel}"
    if not stage_writable(name, rel, avail):
        return False, "当前流程尚未解锁该阶段，不能保存"
    pdir = project_path(name)
    target = (pdir / rel).resolve()
    if target != pdir and projects_root() not in target.parents:
        return False, "路径不合法"
    if not str(target).startswith(str(pdir)):
        return False, "路径不合法"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return True, f"已保存 {rel}"
    except OSError as e:
        return False, f"保存失败：{e}"


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
        # re.search 而非 re.match：实际文件名形如 ch001.md（数字不在开头），
        # match 会永远匹配不到导致全部显示「第 0 章」
        m = re.search(r"(\d+)", p.stem)
        num = int(m.group(1)) if m else 0
        out.append(
            {"num": num, "rel": f"chapters/{p.name}", "name": p.name, "size": p.stat().st_size}
        )
    out.sort(key=lambda x: x["num"])
    return out


def get_command_meta() -> list[dict[str, Any]]:
    """全量命令元数据（名称 / 描述 / 用法 / 门禁），供前端渲染可用操作。"""
    from agent.core.engine.command_router import COMMAND_REGISTRY

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
    from agent.core.registry.genre_pack import GenrePackRegistry

    try:
        return GenrePackRegistry().list_genres_light() or []
    except Exception:
        return []


def get_conflicts(name: str) -> dict[str, Any]:
    """读取项目待裁决的题材合并冲突（.state/merge_conflicts.json）。

    返回 {sources, conflicts, pending, total}；无冲突记录时 pending=0。
    """
    # D-K（2026-08-29）：读取入口收敛到 core（不再经 cli 中转，消除 web→cli 反向依赖）
    from agent.core.registry.genre_merger import load_conflicts

    try:
        data = load_conflicts(project_path(name)) or {}
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


# ============================================================
# 阶段状态模型（B1/B2）：依赖图 + 受影响推导 + 确认动作
#
# 「受影响」是读时推导（纯确定性、零钩子）：
#   affected(D) = D 已确认，且存在某个上游产物当前 mtime > 确认时记录的基线。
# 这样无论上游是被手动保存、CLI 生成还是 Web 生成改动，都会自动使下游「待复核」，
# 而确认架构时写 confirmed 到 frontmatter 引起的 mtime 变化不会误伤（基线是快照）。
# ============================================================

# 内容阶段（「确认」本身是动作，不产文件；architecture 的确认态由 state 机与 frontmatter 承载）
STAGE_KEYS = ["world", "discussion", "architecture", "outline", "characters"]

# 每阶段的产物（目录时取目录内 md 的最大 mtime）
STAGE_FILES: dict[str, str] = {
    "world": "world.md",
    "discussion": "discussion.md",
    "architecture": "architecture.md",
    "outline": "outline.md",
    "characters": "characters",
}

# 静态依赖：每阶段直接依赖的上游阶段（内容层面，写死为确定性 DAG）
STAGE_UPSTREAM: dict[str, list[str]] = {
    "world": [],
    "discussion": ["world"],
    "architecture": ["world", "discussion"],
    "outline": ["world", "discussion", "architecture"],
    "characters": ["world", "discussion", "architecture", "outline"],
}

# 阶段中文名（展示用）
STAGE_LABEL: dict[str, str] = {
    "world": "设定世界",
    "discussion": "脉络讨论",
    "architecture": "故事架构",
    "outline": "创作大纲",
    "characters": "角色设计",
}


def _stage_status_file(pdir: Path) -> Path:
    return pdir / ".state" / "stages.json"


def _load_stage_status(pdir: Path) -> dict[str, Any]:
    try:
        data = json.loads(_stage_status_file(pdir).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 缺失/损坏均降级为空
        return {}
    return data if isinstance(data, dict) else {}


def _save_stage_status(pdir: Path, data: dict[str, Any]) -> None:
    _stage_status_file(pdir).parent.mkdir(parents=True, exist_ok=True)
    tmp = _stage_status_file(pdir).with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_stage_status_file(pdir))


def _stage_file_mtime(pdir: Path, rel: str) -> float:
    """阶段产物当前最大 mtime（目录取目录内 md 的最大值，缺失为 0）。"""
    p = pdir / rel
    if p.is_file():
        try:
            return p.stat().st_mtime
        except Exception:  # noqa: BLE001
            return 0.0
    if p.is_dir():
        m = 0.0
        try:
            for f in p.glob("*.md"):
                if f.is_file():
                    m = max(m, f.stat().st_mtime)
        except Exception:  # noqa: BLE001
            pass
        return m
    return 0.0


def _stage_upstream_mtimes(pdir: Path, stage_key: str) -> dict[str, float]:
    """返回该阶段所有上游产物的当前 mtime（不存在为 0.0）。"""
    out: dict[str, float] = {}
    for up in STAGE_UPSTREAM.get(stage_key, []):
        out[up] = _stage_file_mtime(pdir, STAGE_FILES[up])
    return out


def _stage_relevant_baseline(rec: dict[str, Any]) -> dict[str, Any]:
    """决定「待复核」的比对基线：优先用复核基线，否则用确认基线。

    复核基线（review_baseline）在「复核完成」时打点：
    - 一次复核未发现需调整项（空检查单）→ 视为已看完上游 → 以此基线消除待复核；
    - 检查单项被逐一裁决完毕 → 视为已处理 → 更新基线消除待复核。
    之后若上游再次被改动（mtime > 复核基线），会自动重新标回「待复核」。
    """
    rb = rec.get("review_baseline")
    if isinstance(rb, dict) and rb:
        return rb
    return rec.get("baseline") or {}


def confirm_stage(name: str, stage_key: str) -> tuple[bool, str]:
    """确认（或重新确认）阶段：记录上游产物 mtime 基线，消除受影响标记。

    Args:
        name: 项目名
        stage_key: 阶段 key（STAGE_KEYS 之一）

    Returns:
        (ok, message)
    """
    if stage_key not in STAGE_KEYS:
        return False, f"未知阶段：{stage_key}"
    pdir = project_path(name)
    if _stage_file_mtime(pdir, STAGE_FILES[stage_key]) <= 0:
        return False, f"「{STAGE_LABEL.get(stage_key, stage_key)}」尚无内容，无需确认"
    from datetime import datetime

    data = _load_stage_status(pdir)
    rec = data.get(stage_key) or {}
    # 只更新确认字段与基线，保留已有的复核数据（reviews / adopted_history / review_summary），
    # 避免确认以消除「待复核」时误清空作者已处理过的检查单与采纳记录。
    rec["confirmed"] = True
    rec["confirmed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rec["baseline"] = _stage_upstream_mtimes(pdir, stage_key)
    # 显式确认视为「已看过当前上游」，清掉旧的复核基线，统一以该确认为基准。
    rec.pop("review_baseline", None)
    data[stage_key] = rec
    _save_stage_status(pdir, data)
    return True, f"已确认「{STAGE_LABEL.get(stage_key, stage_key)}」"


def stage_status(name: str, stage_key: str) -> dict[str, Any]:
    """读取单阶段状态：confirmed / affected / 原因 / 是否有内容（读时推导）。"""
    pdir = project_path(name)
    data = _load_stage_status(pdir)
    rec = data.get(stage_key, {}) or {}
    confirmed = rec.get("confirmed") is True
    baseline = _stage_relevant_baseline(rec)
    affected = False
    reason = ""
    changed_up: list[str] = []
    if confirmed:
        cur = _stage_upstream_mtimes(pdir, stage_key)
        for up in STAGE_UPSTREAM.get(stage_key, []):
            if cur.get(up, 0.0) > float(baseline.get(up, 0.0)) + 1e-6:
                affected = True
                changed_up.append(STAGE_LABEL.get(up, up))
        if changed_up:
            reason = "上游「" + "、".join(changed_up) + "」有改动"
    return {
        "key": stage_key,
        "label": STAGE_LABEL.get(stage_key, stage_key),
        "confirmed": confirmed,
        "confirmed_at": rec.get("confirmed_at", ""),
        "affected": affected,
        "reason": reason,
        "has_content": _stage_file_mtime(pdir, STAGE_FILES[stage_key]) > 0,
    }


def all_stage_status(name: str) -> list[dict[str, Any]]:
    """全部内容阶段的当前状态（前端进度/标签渲染用）。"""
    return [stage_status(name, k) for k in STAGE_KEYS]


def stage_status_map(name: str) -> dict[str, dict[str, Any]]:
    """按阶段 key 索引的状态映射（模板内按 key 取值）。"""
    return {s["key"]: s for s in all_stage_status(name)}


# ============================================================
# 复核检查单（B3/B4）：保存 / 读取 / 逐条裁决
#
# 检查单由 M19 工作流（LLM）生成，存到 stages.json 对应阶段记录的 reviews[]，
# 前端面板逐条「采纳 / 忽略」写入 status，供作者对照同步调整后再重新确认。
# ============================================================

def changed_upstreams(name: str, stage_key: str) -> list[str]:
    """返回自最近一次「确认/复核完成」后发生改动的上游阶段 key 列表（无改动为空）。

    比对基线优先用复核基线（review_baseline），无则退回确认基线，
    保证重新复核时只把「上次没看过的新改动」拿给 LLM 对比，避免重复提出旧问题。
    """
    pdir = project_path(name)
    rec = _load_stage_status(pdir).get(stage_key, {}) or {}
    if rec.get("confirmed") is not True:
        return []
    baseline = _stage_relevant_baseline(rec)
    cur = _stage_upstream_mtimes(pdir, stage_key)
    return [
        up
        for up in STAGE_UPSTREAM.get(stage_key, [])
        if cur.get(up, 0.0) > float(baseline.get(up, 0.0)) + 1e-6
    ]


def save_review_items(
    name: str, stage_key: str, findings: list[dict[str, Any]], summary: str
) -> list[dict[str, Any]]:
    """保存复核检查单到 stages.json（覆盖旧条目，全部为 pending），返回带 id 的条目。"""
    pdir = project_path(name)
    data = _load_stage_status(pdir)
    rec = data.setdefault(stage_key, {})
    items: list[dict[str, Any]] = []
    for i, f in enumerate(findings, 1):
        items.append(
            {
                "id": f"{stage_key}-r{i}",
                "kind": str(f.get("kind", "conflict")),
                "severity": str(f.get("severity", "medium")),
                "target": str(f.get("target", "")),
                "issue": str(f.get("issue", "")),
                "upstream_ref": str(f.get("upstream_ref", "")),
                "suggestion": str(f.get("suggestion", "")),
                "status": "pending",
                "handled_at": "",
            }
        )
    rec["review_summary"] = summary
    rec["reviews"] = items
    # 复核未发现需调整项 → 相当于已看完当前上游，打复核基线以消除「待复核」；
    # 存在待处理项则不打点，保持待复核提醒作者处理。
    if not items:
        rec["review_baseline"] = _stage_upstream_mtimes(pdir, stage_key)
    _save_stage_status(pdir, data)
    return items


def review_items(name: str, stage_key: str) -> list[dict[str, Any]]:
    """读取已保存的复核检查单条目（未生成过则空列表）。"""
    rec = _load_stage_status(project_path(name)).get(stage_key, {}) or {}
    return rec.get("reviews", []) or []


def review_summary(name: str, stage_key: str) -> str:
    """读取已保存的复核总体结论（未生成过则空串）。"""
    rec = _load_stage_status(project_path(name)).get(stage_key, {}) or {}
    return str(rec.get("review_summary", ""))


def review_decision(
    name: str, stage_key: str, item_id: str, action: str
) -> tuple[bool, str]:
    """采纳 / 忽略某条复核发现：更新状态与处理时间。

    采纳时同时把该条目追加进 ``adopted_history``（持久化、不随重新生成清空），
    供下一次复核作为已确认上下文带进 prompt，避免重复提出、保证既有决策不丢失。
    """
    if action not in ("accepted", "ignored"):
        return False, f"未知处理方式：{action}"
    pdir = project_path(name)
    data = _load_stage_status(pdir)
    rec = data.get(stage_key) or {}
    for it in rec.get("reviews", []) or []:
        if it.get("id") == item_id:
            from datetime import datetime

            it["status"] = action
            it["handled_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if action == "accepted":
                history = rec.setdefault("adopted_history", [])
                summary_item = {
                    "kind": str(it.get("kind", "conflict")),
                    "target": str(it.get("target", "")),
                    "issue": str(it.get("issue", "")),
                    "suggestion": str(it.get("suggestion", "")),
                }
                if not any(h.get("target") == summary_item["target"] for h in history):
                    history.append(summary_item)
            _save_stage_status(pdir, data)
            label = "已采纳" if action == "accepted" else "已忽略"
            # 全部检查单项裁决完毕 → 复核视为处理完成，打复核基线消除「待复核」，
            # 之后只有上游再次改动才会重新标「待复核」。
            all_done = all(
                (x.get("status", "pending") in ("accepted", "ignored"))
                for x in rec.get("reviews", [])
            ) if rec.get("reviews") else False
            if all_done:
                rec["review_baseline"] = _stage_upstream_mtimes(pdir, stage_key)
                _save_stage_status(pdir, data)
            return True, f"{label}「{it.get('target') or item_id}」"
    return False, "未找到该检查项"


def adopted_history(name: str, stage_key: str) -> list[dict[str, Any]]:
    """读取该阶段历史已采纳的复核条目（持久化，重生成后仍保留）。"""
    rec = _load_stage_status(project_path(name)).get(stage_key, {}) or {}
    return rec.get("adopted_history", []) or []


# ============================================================
# A 系列：问答引导结果保存 / 读取
#
# 问答面板收集的 answers / skipped / supplementary 保存到
# ``.state/qa/{stage}.json``，生成工作流读取并注入 prompt（qa_sync.py）。
# ============================================================

def save_qa(
    name: str,
    stage_key: str,
    answers: dict[str, Any] | None,
    skipped: dict[str, Any] | None,
    supplementary: str = "",
) -> tuple[bool, str]:
    """保存某阶段的问答结果。"""
    if stage_key not in STAGE_KEYS:
        return False, f"未知阶段：{stage_key}"
    import json
    from datetime import datetime

    pdir = project_path(name)
    qa_dir = pdir / ".state" / "qa"
    qa_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "answers": answers or {},
        "skipped": skipped or {},
        "supplementary": supplementary or "",
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    (qa_dir / f"{stage_key}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return True, f"已保存「{STAGE_LABEL.get(stage_key, stage_key)}」的问答结果"


def load_qa(name: str, stage_key: str) -> dict[str, Any]:
    """读取已保存的问答结果（未生成过则空 dict）。"""
    import json

    f = project_path(name) / ".state" / "qa" / f"{stage_key}.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
