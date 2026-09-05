"""RAG 配置管理（Web UI 用）。

支撑 /rag 配置页的读写逻辑：
- 读取 / 保存 agent 仓库根 .env 的 embedding 相关键（EMBEDDING_*、HF_HUB_CACHE、HF_ENDPOINT）；
- 汇总当前空间各小说项目的 RAG 索引状态（.state/rag/index.json）；
- 在后台线程跑 embedding 连通性测试与索引重建（降级不阻断，前端轮询任务状态）。

遵循项目「降级不阻断」哲学：任何读取失败都返回兜底结构，绝不让配置问题
阻断 Web 主流程。embedding 推理复用 client/embedding_router 的 Provider，
不在此处重新实现。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

_JOBS_LOCK = threading.Lock()
_JOBS: dict[str, dict[str, Any]] = {}

# 允许经 Web 修改的 .env 键（白名单，防任意键写入）
_ENV_KEYS = (
    "EMBEDDING_PROVIDER",
    "EMBEDDING_MODEL_ID",
    "EMBEDDING_BASE_URL",
    "EMBEDDING_API_KEY",
    "EMBEDDING_DEVICE",
    "HF_HUB_CACHE",
    "HF_ENDPOINT",
)

PROVIDER_LABELS = {
    "qwen_local": "本地 HF 模型（transformers 离线推理）",
    "ollama": "本地 Ollama（/api/embeddings）",
    "openai": "OpenAI 兼容 /embeddings 端点",
    "": "跟随主模型（LLM_PROVIDER）",
}


def env_path() -> Path:
    """定位 agent 仓库根 .env（与 ConfigLoader 的向上搜索保持一致）。"""
    import agent as _pkg

    pkg_dir = Path(_pkg.__file__).resolve().parent
    for cand in (pkg_dir, pkg_dir.parent, pkg_dir.parent.parent):
        if (cand / ".env").exists():
            return cand / ".env"
    return pkg_dir.parent.parent / ".env"


def _read_env_text() -> str:
    p = env_path()
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def read_rag_config() -> dict[str, str]:
    """读取当前 embedding 配置（.env 值优先，缺失回退进程环境变量）。"""
    text = _read_env_text()
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if key in _ENV_KEYS and key not in values:
            values[key] = val.strip().strip('"').strip("'")
    for key in _ENV_KEYS:
        if not values.get(key):
            values[key] = os.environ.get(key, "") or ""
    return values


def write_rag_config(update: dict[str, str]) -> dict[str, Any]:
    """把白名单内的键 upsert 进 .env（保留原有注释与其它行）。

    返回 {ok, message}。键不在白名单的静默丢弃。
    """
    clean = {k: v.strip() for k, v in update.items() if k in _ENV_KEYS and v is not None}
    if not clean:
        return {"ok": False, "message": "没有可保存的配置项"}

    p = env_path()
    text = _read_env_text()
    lines = text.splitlines()
    remaining = dict(clean)
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        key = stripped.split("=", 1)[0].strip() if "=" in stripped and not stripped.startswith("#") else None
        if key in remaining:
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)
    if remaining:
        if out and out[-1].strip():
            out.append("")
        out.append("# ---- RAG / Embedding 配置（Web 端写入）----")
        for key, val in remaining.items():
            out.append(f"{key}={val}")
    try:
        p.write_text("\n".join(out) + "\n", encoding="utf-8")
    except OSError as e:
        return {"ok": False, "message": f"写入 .env 失败：{e}"}

    # 同步进程环境，使本次运行立即生效（无需重启 Web 服务）
    for key, val in clean.items():
        os.environ[key] = val
    from agent.base.config import ConfigLoader

    ConfigLoader.reset()
    return {"ok": True, "message": "配置已保存并即时生效"}


def project_rag_status(project_dir: Path) -> dict[str, Any]:
    """读取单个项目的 RAG 索引状态（缺索引/损坏都降级为空状态）。"""
    idx = project_dir / ".state" / "rag" / "index.json"
    status: dict[str, Any] = {
        "indexed": False,
        "chunks": 0,
        "vectors": 0,
        "dim": 0,
        "updated_at": "",
    }
    try:
        data = json.loads(idx.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return status
    chunks = data.get("chunks") if isinstance(data, dict) else data
    if not isinstance(chunks, list):
        return status
    status["indexed"] = True
    status["chunks"] = len(chunks)
    vecs = [c for c in chunks if isinstance(c, dict) and c.get("embedding")]
    status["vectors"] = len(vecs)
    if vecs:
        status["dim"] = len(vecs[0]["embedding"])
    try:
        status["updated_at"] = datetime.fromtimestamp(idx.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    except OSError:
        pass
    return status


def list_projects_status() -> list[dict[str, Any]]:
    """当前空间下所有小说项目的索引状态清单。"""
    from agent.web.workspace import active_root
    from agent.web.state import list_projects

    root = active_root()
    rows: list[dict[str, Any]] = []
    known = {p.get("name") for p in list_projects()}
    for d in sorted(root.iterdir()) if root.exists() else []:
        if not d.is_dir() or d.name.startswith((".", "_")) or d.name not in known:
            continue
        st = project_rag_status(d)
        st["name"] = d.name
        rows.append(st)
    return rows


def _mask(secret: str) -> str:
    if not secret:
        return ""
    if len(secret) <= 8:
        return "*" * len(secret)
    return secret[:4] + "*" * (len(secret) - 8) + secret[-4:]


def config_summary() -> dict[str, Any]:
    """页面渲染用配置视图（api key 打码）。"""
    cfg = read_rag_config()
    provider = cfg.get("EMBEDDING_PROVIDER", "")
    return {
        "provider": provider,
        "provider_label": PROVIDER_LABELS.get(provider, provider or "（未设置）"),
        "model": cfg.get("EMBEDDING_MODEL_ID", ""),
        "base_url": cfg.get("EMBEDDING_BASE_URL", ""),
        "api_key_masked": _mask(cfg.get("EMBEDDING_API_KEY", "")),
        "hf_cache": cfg.get("HF_HUB_CACHE", ""),
        "hf_endpoint": cfg.get("HF_ENDPOINT", ""),
        "env_file": str(env_path()),
    }


# ============================================================
# 后台任务：embedding 连通性测试 / 索引重建
# ============================================================


def _start_job(kind: str, payload: dict[str, Any]) -> str:
    job_id = f"{kind}-{int(time.time() * 1000)}"
    with _JOBS_LOCK:
        # 只保留最近 20 个任务，防内存膨胀
        for old in sorted(_JOBS)[:-20]:
            _JOBS.pop(old, None)
        _JOBS[job_id] = {
            "kind": kind,
            "status": "running",
            "message": "",
            "result": None,
            "started_at": datetime.now().strftime("%H:%M:%S"),
        }
    t = threading.Thread(target=_run_job, args=(job_id, kind, payload), daemon=True)
    t.start()
    return job_id


def job_status(job_id: str) -> dict[str, Any] | None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job else None


def _set_job(job_id: str, **fields: Any) -> None:
    with _JOBS_LOCK:
        if job_id in _JOBS:
            _JOBS[job_id].update(fields)


def _run_job(job_id: str, kind: str, payload: dict[str, Any]) -> None:
    try:
        if kind == "test":
            result = _test_embedding(payload)
        else:
            result = _reindex_project(payload)
        _set_job(job_id, status="done", result=result)
    except Exception as e:  # noqa: BLE001 - 后台任务降级不阻断
        _set_job(
            job_id,
            status="error",
            message=f"{e}",
            result={"trace": traceback.format_exc(limit=3)},
        )


def _apply_env_for_embedding() -> None:
    """把 .env 的 HF 相关配置刷进进程环境（transformers 加载前必须生效）。"""
    cfg = read_rag_config()
    if cfg.get("HF_HUB_CACHE"):
        os.environ["HF_HUB_CACHE"] = cfg["HF_HUB_CACHE"]
    if cfg.get("HF_ENDPOINT"):
        os.environ["HF_ENDPOINT"] = cfg["HF_ENDPOINT"]
    if cfg.get("EMBEDDING_DEVICE"):
        os.environ["EMBEDDING_DEVICE"] = cfg["EMBEDDING_DEVICE"]


def _test_embedding(payload: dict[str, Any]) -> dict[str, Any]:
    """用当前配置真实跑一次 embed，返回维度 / 耗时 / 采样文本。"""
    from agent.base.config import ConfigLoader
    from agent.client.embedding_router import get_embedding_provider

    _apply_env_for_embedding()
    ConfigLoader.reset()
    config = ConfigLoader.get_llm_config()
    provider = get_embedding_provider(config)

    sample = payload.get("text") or "少年握紧手中的剑，向着灵山之巅走去。"
    t0 = time.time()
    vecs = provider.embed([sample, "第二段样例文本，用于验证批量接口。"])
    elapsed = time.time() - t0
    dim = len(vecs[0]) if vecs and vecs[0] else 0
    failed = [i for i, v in enumerate(vecs) if not v]
    return {
        "ok": dim > 0,
        "dim": dim,
        "elapsed_s": round(elapsed, 2),
        "provider_type": type(provider).__name__,
        "model": getattr(config, "embedding_model", "") or getattr(config, "model", ""),
        "failed_indexes": failed,
        "cache_dir": os.environ.get("HF_HUB_CACHE", ""),
    }


def _reindex_project(payload: dict[str, Any]) -> dict[str, Any]:
    """后台重建指定项目的 RAG 索引（复用 core/rag/indexer.Indexer）。"""
    name = payload.get("name", "")
    from agent.web.workspace import active_root

    project_dir = active_root() / name
    if not project_dir.is_dir():
        return {"ok": False, "message": f"项目不存在：{name}"}

    from agent.core.rag.indexer import Indexer

    _apply_env_for_embedding()
    try:
        from agent.core.event_sourcing.llm_wiring import wire_llm_event_hook

        wire_llm_event_hook(str(project_dir))
    except Exception:  # noqa: BLE001 - 事件接线失败不阻断索引
        pass

    stats = Indexer(project_dir).reindex()
    status = project_rag_status(project_dir)
    return {
        "ok": True,
        "message": (
            f"索引完成：{stats.get('indexed_chunks', 0)} 切片 / {stats.get('chapters', 0)} 章"
            + (f"，{stats.get('embedding_failed', 0)} 片 embed 失败（已降级 BM25-only）"
               if stats.get("embedding_failed") else "")
        ),
        "stats": stats,
        "status": status,
    }


def start_test(text: str = "") -> str:
    return _start_job("test", {"text": text})


def start_reindex(name: str) -> str:
    return _start_job("reindex", {"name": name})


def validate_config(cfg: dict[str, str]) -> list[str]:
    """保存前的轻校验，返回错误列表（空列表 = 通过）。"""
    errs: list[str] = []
    provider = (cfg.get("EMBEDDING_PROVIDER") or "").strip()
    model = (cfg.get("EMBEDDING_MODEL_ID") or "").strip()
    if provider not in ("", "qwen_local", "ollama", "openai"):
        errs.append(f"未知的 EMBEDDING_PROVIDER：{provider}")
    if provider == "qwen_local" and not model:
        errs.append("本地 HF 模型需要填写 EMBEDDING_MODEL_ID（如 BAAI/bge-small-zh-v1.5）")
    if provider == "ollama" and not model:
        errs.append("Ollama 需要填写模型名（如 bge-m3）")
    cache = (cfg.get("HF_HUB_CACHE") or "").strip()
    if cache and not Path(cache).exists():
        errs.append(f"HF_HUB_CACHE 目录不存在：{cache}")
    endpoint = (cfg.get("HF_ENDPOINT") or "").strip()
    if endpoint and not re.match(r"^https?://", endpoint):
        errs.append("HF_ENDPOINT 需为 http(s):// 开头的镜像地址")
    return errs
