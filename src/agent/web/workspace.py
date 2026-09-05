"""项目空间（小说数据根目录）管理（Web UI 用）。

解决「web 页面只能用默认 novels/ 目录、不能切换本地小说路径」的问题：
- 支持登记多个本地目录作为「项目空间」，任意切换当前激活空间；
- 激活空间持久化到 agent 仓库根 ``workspaces.json``（与 models.json 同级，
  复用 model_profiles 的存储模式：env 覆盖 > 仓库根定位）；
- state.py 的 PROJECTS_ROOT 由本模块动态提供，runner 启动 CLI 子进程时
  把激活空间经 NOVEL_DATA_ROOT 环境变量透传，CLI 与 Web 看到同一份数据。

遵循项目「降级不阻断」哲学：存储文件缺失 / 损坏时回落到默认空间，
绝不让配置问题阻断 Web 主流程。
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

_STORE_LOCK = threading.Lock()

# 默认空间：环境变量 NOVEL_DATA_ROOT 优先，否则 agent 仓库同级 novels/
_AGENT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def default_root() -> Path:
    """默认小说数据根（与旧 PROJECTS_ROOT 语义一致）。"""
    env = os.environ.get("NOVEL_DATA_ROOT", "").strip()
    if env:
        return Path(env).expanduser()
    return (_AGENT_ROOT.parent / "novels").resolve()


def store_path() -> Path:
    """定位 workspaces.json：环境变量 NOVEL_WORKSPACES_FILE > agent 仓库根。"""
    override = os.environ.get("NOVEL_WORKSPACES_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    return _AGENT_ROOT / "workspaces.json"


def load_store() -> dict[str, Any]:
    """读取空间存储（缺失 / 损坏时返回只含默认空间的兜底结构）。"""
    dft = {
        "id": "default",
        "name": "默认空间",
        "path": str(default_root()),
        "builtin": True,
    }
    try:
        data = json.loads(store_path().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 缺失/损坏降级为默认
        data = {}
    spaces = data.get("workspaces") or []
    if not any(s.get("id") == "default" for s in spaces):
        spaces.insert(0, dft)
    active = data.get("active") or "default"
    if not any(s.get("id") == active for s in spaces):
        active = "default"
    return {"active": active, "workspaces": spaces}


def _save_store(data: dict[str, Any]) -> None:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp.replace(path)


def active_root() -> Path:
    """当前激活空间的本地路径（供 state.py 动态计算项目根）。"""
    data = load_store()
    for s in data["workspaces"]:
        if s.get("id") == data["active"]:
            return Path(str(s.get("path") or default_root())).expanduser()
    return default_root()


def active_workspace() -> dict[str, Any]:
    """当前激活空间完整记录（名称 / 路径 / 是否内置）。"""
    data = load_store()
    for s in data["workspaces"]:
        if s.get("id") == data["active"]:
            return s
    return data["workspaces"][0]


def list_workspaces() -> list[dict[str, Any]]:
    """全部空间（含激活标记与目录有效性）。"""
    data = load_store()
    out: list[dict[str, Any]] = []
    for s in data["workspaces"]:
        p = Path(str(s.get("path") or "")).expanduser()
        out.append(
            {
                "id": s.get("id", ""),
                "name": s.get("name", ""),
                "path": str(p),
                "builtin": bool(s.get("builtin")),
                "active": s.get("id") == data["active"],
                "exists": p.is_dir(),
                "project_count": (
                    sum(1 for c in p.iterdir() if c.is_dir()) if p.is_dir() else 0
                ),
            }
        )
    return out


def validate_path(raw: str) -> tuple[bool, str, Path]:
    """校验空间路径：绝对路径、目录可创建。返回 (ok, message, path)。"""
    p = Path(raw.strip().strip('"')).expanduser()
    if not raw.strip():
        return False, "路径不能为空", p
    if not p.is_absolute():
        return False, "必须是绝对路径（如 D:\\mynovels）", p
    if p.exists() and not p.is_dir():
        return False, "该路径已存在且不是目录", p
    return True, "", p


def add_workspace(name: str, path: str) -> tuple[bool, str, dict[str, Any] | None]:
    """登记一个新空间（路径若不存在则创建）。"""
    ok, msg, p = validate_path(path)
    if not ok:
        return False, msg, None
    name = (name or "").strip() or p.name or "未命名空间"
    with _STORE_LOCK:
        data = load_store()
        norm = str(p)
        for s in data["workspaces"]:
            if str(Path(str(s.get("path") or "")).expanduser()) == norm:
                return False, f"该路径已登记为空间「{s.get('name')}」", None
        rec = {
            "id": uuid.uuid4().hex[:8],
            "name": name,
            "path": norm,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        data["workspaces"].append(rec)
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return False, f"目录创建失败：{e}", None
        _save_store(data)
    return True, f"已添加空间「{name}」", rec


def switch_workspace(ws_id: str) -> tuple[bool, str]:
    """切换当前激活空间（切换后项目列表 / 新建落点立即随之变化）。"""
    with _STORE_LOCK:
        data = load_store()
        target = next((s for s in data["workspaces"] if s.get("id") == ws_id), None)
        if target is None:
            return False, "空间不存在"
        data["active"] = ws_id
        _save_store(data)
    return True, f"已切换到空间「{target.get('name')}」"


def delete_workspace(ws_id: str) -> tuple[bool, str]:
    """删除一个空间登记（只移除记录，不动磁盘目录）；内置默认空间不可删。"""
    with _STORE_LOCK:
        data = load_store()
        target = next((s for s in data["workspaces"] if s.get("id") == ws_id), None)
        if target is None:
            return False, "空间不存在"
        if target.get("builtin"):
            return False, "默认空间不可删除"
        data["workspaces"] = [s for s in data["workspaces"] if s.get("id") != ws_id]
        if data["active"] == ws_id:
            data["active"] = "default"
        _save_store(data)
    return True, f"已移除空间「{target.get('name')}」（磁盘目录未删除）"


# ============================================================
# 本地目录浏览器（供「添加空间」的目录选择器，替代手输路径）
# ============================================================

def fs_browse(raw: str = "") -> dict[str, Any]:
    """浏览本地目录：返回该目录下的子目录列表（仅目录，含错误降级）。

    path 为空时回落到用户主目录；Windows 下同时返回盘符列表供跳转。
    """
    import string

    if raw.strip():
        p = Path(raw.strip()).expanduser()
    else:
        p = Path.home()
    drives: list[str] = []
    if os.name == "nt":
        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            if Path(drive).exists():
                drives.append(drive)
    dirs: list[dict[str, str]] = []
    exists = p.is_dir()
    if exists:
        try:
            for child in sorted(p.iterdir(), key=lambda c: c.name.lower()):
                try:
                    if child.is_dir():
                        dirs.append({"name": child.name, "path": str(child)})
                except OSError:  # noqa: BLE001 - 无权限子项直接跳过
                    continue
        except OSError as e:
            return {"ok": False, "message": f"无法读取目录：{e}", "current": str(p),
                    "parent": "", "dirs": [], "drives": drives}
        return {
            "ok": True,
            "current": str(p),
            "parent": str(p.parent) if p.parent != p else "",
            "dirs": dirs,
            "drives": drives,
        }
    return {"ok": False, "message": "目录不存在", "current": str(p),
            "parent": "", "dirs": [], "drives": drives}
