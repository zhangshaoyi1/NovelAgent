"""模型档案（Model Profile）存储 — 多模型管理的数据层

职责：把「在 Web UI 配置的多个模型端点」持久化为 JSON 档案库，并作为
``ConfigLoader`` 解析 LLMConfig 的**最高优先级来源**（高于 .env）。

设计约束：
- base 层模块：仅依赖标准库，禁止 import agent 包内任何其他模块。
- JSON 存储于 agent 仓库根 ``models.json``（与 .env 同级；含 API Key，
  已加入 .gitignore，绝不入库）。可用环境变量 ``NOVEL_MODELS_FILE`` 覆盖路径。
- 降级不阻断：文件缺失 / 损坏时返回空库，绝不抛异常打断写作流程。

解析优先级（在 ``base.config._build_llm_config_from_env`` 落地）：
    1. 环境变量 ``NOVEL_MODEL_PROFILE``（Web 端按次运行指定的档案 id）
    2. 档案库中 ``active`` 指向的已启用档案（Web 端「设为默认」）
    3. 纯 .env / 进程环境变量（原有行为，完全向后兼容）
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

_STORE_LOCK = threading.Lock()

# 档案字段默认值（Web 端表单与存储共用一份形状定义）
_PROFILE_DEFAULTS: dict[str, Any] = {
    "id": "",
    "name": "",
    "provider": "openai",  # openai=OpenAI 兼容协议 | ollama=本地 Ollama
    "base_url": "",
    "api_key": "",
    "model": "",
    "enable_thinking": None,  # None=不干预 False=强制关闭 True=强制开启
    "timeout": 0,  # 0=未设置（配置解析时回退 .env 的 LLM_TIMEOUT）
    "max_retries": 3,
    "enabled": True,
    "notes": "",
    "created_at": "",
    "updated_at": "",
}

_ID_RE = re.compile(r"[^a-zA-Z0-9_-]")


def store_path() -> Path:
    """定位 models.json：环境变量 NOVEL_MODELS_FILE > agent 仓库根。"""
    override = os.environ.get("NOVEL_MODELS_FILE", "").strip()
    if override:
        return Path(override)
    try:
        import agent as _pkg

        # __file__ = .../agent/src/agent/__init__.py → 上三级 = agent 仓库根
        root = Path(_pkg.__file__).resolve().parent.parent.parent
        return root / "models.json"
    except Exception:  # noqa: BLE001 - 包定位失败降级为当前目录
        return Path("models.json").resolve()


def load_store() -> dict[str, Any]:
    """读取档案库（缺失/损坏 → 空库，降级不阻断）。"""
    path = store_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"version": 1, "active": "", "profiles": []}
    if not isinstance(data, dict):
        return {"version": 1, "active": "", "profiles": []}
    data.setdefault("version", 1)
    data.setdefault("active", "")
    data.setdefault("profiles", [])
    return data


def save_store(data: dict[str, Any]) -> None:
    """原子写回档案库（tmp + replace，避免并发写坏文件）。"""
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _normalize(raw: dict[str, Any]) -> dict[str, Any]:
    """补齐默认字段 + 类型归一化（Web 表单来的 dict 可能缺字段）。"""
    p = dict(_PROFILE_DEFAULTS)
    for key in _PROFILE_DEFAULTS:
        if key in raw:
            p[key] = raw[key]
    if not p["id"]:
        p["id"] = "mp-" + uuid.uuid4().hex[:10]
    p["id"] = _ID_RE.sub("", str(p["id"]))[:40] or "mp-" + uuid.uuid4().hex[:10]
    p["provider"] = str(p["provider"] or "openai").strip().lower()
    if p["provider"] not in ("openai", "ollama"):
        p["provider"] = "openai"
    for key in ("name", "base_url", "api_key", "model", "notes"):
        p[key] = str(p.get(key) or "").strip()
    try:
        p["timeout"] = int(p["timeout"] or 0)
    except (TypeError, ValueError):
        p["timeout"] = 0
    if p["timeout"] and p["timeout"] < 5:
        p["timeout"] = 5
    try:
        p["max_retries"] = max(0, int(p["max_retries"] or 3))
    except (TypeError, ValueError):
        p["max_retries"] = 3
    p["enabled"] = bool(p.get("enabled", True))
    if p["enable_thinking"] is not None:
        p["enable_thinking"] = bool(p["enable_thinking"])
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not p["created_at"]:
        p["created_at"] = now
    p["updated_at"] = now
    return p


def list_profiles() -> list[dict[str, Any]]:
    """全部档案（已归一化）。"""
    with _STORE_LOCK:
        return [ _normalize(p) for p in load_store().get("profiles", []) ]


def get_profile(profile_id: str) -> dict[str, Any] | None:
    for p in list_profiles():
        if p["id"] == profile_id:
            return p
    return None


def upsert_profile(raw: dict[str, Any]) -> dict[str, Any]:
    """创建或更新档案（按 id 匹配），返回归一化后的档案。"""
    with _STORE_LOCK:
        data = load_store()
        norm = _normalize(raw)
        profiles: list[dict[str, Any]] = data.get("profiles", [])
        for i, old in enumerate(profiles):
            if old.get("id") == norm["id"]:
                norm["created_at"] = old.get("created_at") or norm["created_at"]
                profiles[i] = norm
                break
        else:
            profiles.append(norm)
        data["profiles"] = profiles
        # 首个档案自动激活，保证「配置即可用」
        if not data.get("active"):
            data["active"] = norm["id"]
        save_store(data)
    return norm


def delete_profile(profile_id: str) -> bool:
    """删除档案；若删除的是激活档案则清空 active。返回是否删除成功。"""
    with _STORE_LOCK:
        data = load_store()
        before = len(data.get("profiles", []))
        data["profiles"] = [
            p for p in data.get("profiles", []) if p.get("id") != profile_id
        ]
        if len(data["profiles"]) == before:
            return False
        if data.get("active") == profile_id:
            data["active"] = ""
        save_store(data)
    return True


def set_active(profile_id: str) -> bool:
    """设为默认档案（未启用档案不允许激活）。"""
    with _STORE_LOCK:
        data = load_store()
        target = next(
            (p for p in data.get("profiles", []) if p.get("id") == profile_id),
            None,
        )
        if target is None:
            return False
        if not target.get("enabled", True):
            return False
        data["active"] = profile_id
        save_store(data)
    return True


def active_profile() -> dict[str, Any] | None:
    """当前激活且已启用的档案（无 → None，走 .env 行为）。"""
    with _STORE_LOCK:
        data = load_store()
    active_id = str(data.get("active") or "").strip()
    if not active_id:
        return None
    for p in data.get("profiles", []):
        if p.get("id") == active_id and p.get("enabled", True):
            return _normalize(p)
    return None


def resolve_profile() -> dict[str, Any] | None:
    """按优先级解析本次应使用的档案。

    1. 环境变量 NOVEL_MODEL_PROFILE（Web 端按次运行指定，含已禁用档案也可用）
    2. 档案库激活档案
    """
    explicit = os.environ.get("NOVEL_MODEL_PROFILE", "").strip()
    if explicit:
        p = get_profile(explicit)
        if p is not None:
            return p
    return active_profile()


def profile_to_llm_kwargs(p: dict[str, Any]) -> dict[str, Any]:
    """把档案映射为 LLMConfig 关键字参数（未填字段返回 None，由调用方回退 env）。"""
    return {
        "provider": p.get("provider") or None,
        "api_key": p.get("api_key") or None,
        "base_url": p.get("base_url") or None,
        "model": p.get("model") or None,
        "enable_thinking": p.get("enable_thinking"),
        "timeout": p.get("timeout") or None,
        "max_retries": p.get("max_retries") or None,
    }


def mask_key(key: str) -> str:
    """API Key 脱敏展示（保留前 4 后 4）。"""
    key = str(key or "")
    if len(key) <= 8:
        return "••••" if key else ""
    return f"{key[:4]}••••{key[-4:]}"


def masked_profile(p: dict[str, Any]) -> dict[str, Any]:
    """返回脱敏后的档案副本（供 Web API 返回前端）。"""
    out = dict(p)
    out["api_key"] = mask_key(p.get("api_key", ""))
    out["has_key"] = bool(p.get("api_key"))
    return out
