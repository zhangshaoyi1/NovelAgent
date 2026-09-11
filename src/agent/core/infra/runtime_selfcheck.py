"""运行时生效性自检（设施③，2026-09-11）

背景
----
2026-09-10~11 两天内团队 5 次踩「改动没生效」的坑：

- 浏览器刷新 ≠ 服务端重载 → 两次误判「修复无效」
- **改 .py 不影响已启动进程** → 每次都要人工「三件套验证」
- 档位 ``timeout=90s`` 与上游 glm-5.2 思考型脱节 → 拖了整整两天
- CLI ``--no-wait`` 的 ``env_extra={}`` → 档位静默回退
- 宿主 safe-delete shim 语义随环境变化（同一行代码在不同宿主下行为不同）

根因：这些全是**隐式假设**——「我改了就生效」「进程跑的是新码」「档位就是我配的」。
本模块把「当前进程实际生效的运行时状态」做成**可断言指纹**，供 ``doctor`` /
daemon 启动 / 长任务入口复用，把隐式假设变成显式可查。

设计约束
--------
- ``core/infra`` 层：只依赖 ``base`` 与标准库，不 import agents/workflows/daemon。
- 只读安全：唯一写操作是「记录本进程指纹」与「把死进程记录改名」（**不删除**，
  遵循宿主 safe-delete 约定——改名 ``os.replace`` 不触发护栏）。
- 降级不阻断：任何探测失败都返回空值而非抛异常（探测本身不得影响写作）。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

#: 进程指纹记录目录（相对项目根的 .state）
_RECORD_DIRNAME = "runtime_processes"

#: timeout 低于该值即告警——思考型上游成功调用 p90 实测 92.3s（2026-09-11 复盘）
TIMEOUT_WARN_BELOW = 180

#: 需要显式可见的关键环境变量（决定档位/宿主语义是否被意外覆盖）
_TRACKED_ENV = (
    "NOVEL_MODEL_PROFILE",
    "LLM_TIMEOUT",
    "NOVEL_MODEL_TIMEOUT",
    "LLM_MAX_TOKENS",
    "CODEBUDDY_SAFE_DELETE_ENABLED",
    "CODEBUDDY_SESSION_ID",
    "NOVEL_AGENT_DOTENV",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL",
)


def src_root() -> Path:
    """agent 源码根（``.../agent/src/agent``）。"""
    return Path(__file__).resolve().parents[2]


# ============================================================
# 指纹采集
# ============================================================
def code_fingerprint() -> dict[str, Any]:
    """源码聚合指纹：文件数 + 最新改动时间 + 内容摘要 hash。

    改动任一 ``.py`` 都会改变 ``sha256`` 前缀——据此可判定「运行中进程是否在跑旧代码」。
    """
    root = src_root()
    h = hashlib.sha256()
    latest = 0.0
    n = 0
    for py in sorted(root.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        try:
            st = py.stat()
        except OSError:
            continue  # noqa: SILENT_DEGRADE - 单文件读失败不影响整体指纹
        latest = max(latest, st.st_mtime)
        n += 1
        h.update(
            f"{py.relative_to(root).as_posix()}:{st.st_size}:{int(st.st_mtime)}".encode()
        )
    return {
        "sha256": h.hexdigest()[:16],
        "files": n,
        "mtime": (
            datetime.fromtimestamp(latest).strftime("%Y-%m-%d %H:%M:%S") if latest else ""
        ),
    }


def profile_state() -> dict[str, Any] | None:
    """当前激活模型档位（base 层，无则 None → 走 .env）。"""
    try:
        from agent.base.model_profiles import active_profile
    except Exception:  # noqa: BLE001 - 探测失败不阻断
        return None
    try:
        return active_profile()
    except Exception:  # noqa: BLE001
        return None


def env_state() -> dict[str, Any]:
    """关键环境变量的实际取值（缺失记 ``None``）。"""
    return {k: os.environ.get(k) for k in _TRACKED_ENV}


def shim_state() -> dict[str, Any]:
    """宿主 safe-delete shim 是否注入本进程（决定「删除」的真实语义）。"""
    sc = sys.modules.get("sitecustomize")
    return {
        "sitecustomize_loaded": sc is not None,
        "sitecustomize_file": str(getattr(sc, "__file__", "") or ""),
        "safe_delete_enabled": os.environ.get("CODEBUDDY_SAFE_DELETE_ENABLED"),
        "session_bound": bool(os.environ.get("CODEBUDDY_SESSION_ID")),
    }


def runtime_fingerprint() -> dict[str, Any]:
    """本进程「实际生效」的运行时快照。"""
    prof = profile_state()
    return {
        "pid": os.getpid(),
        "python": sys.version.split()[0],
        "cwd": os.getcwd(),
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "code": code_fingerprint(),
        "profile": (
            {
                "id": prof.get("id"),
                "model": prof.get("model"),
                "timeout": prof.get("timeout") or None,
                "max_tokens": prof.get("max_tokens") or None,
                "enable_thinking": prof.get("enable_thinking"),
            }
            if prof
            else None
        ),
        "env": env_state(),
        "shim": shim_state(),
    }


def format_fingerprint(fp: dict[str, Any] | None = None) -> str:
    """把指纹压成单行摘要（用于启动日志）。"""
    fp = fp or runtime_fingerprint()
    code = fp.get("code") or {}
    prof = fp.get("profile") or {}
    shim = fp.get("shim") or {}
    prof_txt = (
        f"{prof.get('id')}/{prof.get('model')}/timeout={prof.get('timeout') or 'env'}"
        if prof
        else "无档位(走.env)"
    )
    return (
        f"pid={fp.get('pid')} py={fp.get('python')} "
        f"code={code.get('sha256')}({code.get('files')} files, {code.get('mtime')}) "
        f"profile={prof_txt} "
        f"shim={'on' if shim.get('sitecustomize_loaded') else 'off'}"
        f"/safe_delete={shim.get('safe_delete_enabled') or '-'}"
    )


# ============================================================
# 陈旧进程检测（「改完没重启」）
# ============================================================
def pid_alive(pid: int) -> bool:
    """PID 是否仍存活（跨平台，失败视为不存活）。"""
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            try:
                code = ctypes.c_ulong()
                ok = k32.GetExitCodeProcess(handle, ctypes.byref(code))
                return bool(ok) and code.value == STILL_ACTIVE
            finally:
                k32.CloseHandle(handle)
        os.kill(pid, 0)
        return True
    except (OSError, SystemExit, ValueError):
        return False


def _record_dir(project_dir: Path | str) -> Path:
    return Path(project_dir) / ".state" / _RECORD_DIRNAME


def remember_process(project_dir: Path | str, role: str) -> Path | None:
    """记录本进程指纹（daemon / Web / 长任务启动时调用，供陈旧检测）。"""
    try:
        d = _record_dir(project_dir)
        d.mkdir(parents=True, exist_ok=True)
        rec = {
            "pid": os.getpid(),
            "role": role,
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "code_sha": code_fingerprint()["sha256"],
            "argv": list(sys.argv[:4]),
        }
        p = d / f"{os.getpid()}.json"
        p.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        return p
    except Exception:  # noqa: BLE001 - 记录失败不得影响启动
        return None  # noqa: SILENT_DEGRADE


def stale_running_processes(project_dir: Path | str) -> list[str]:
    """返回「仍存活但跑的是旧代码」的进程描述列表（同时清理死进程记录）。"""
    d = _record_dir(project_dir)
    if not d.exists():
        return []
    cur = code_fingerprint()["sha256"]
    out: list[str] = []
    for f in sorted(d.glob("*.json")):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue  # noqa: SILENT_DEGRADE - 坏记录跳过
        pid = int(rec.get("pid") or 0)
        if not pid_alive(pid):
            # 死进程记录：**改名**而非删除（宿主 safe-delete 护栏下删除会抛 SystemExit）
            try:
                os.replace(str(f), str(f) + ".dead")
            except (OSError, SystemExit):
                pass  # noqa: SILENT_DEGRADE
            continue
        if rec.get("code_sha") != cur:
            out.append(
                f"进程 {pid}（{rec.get('role', '?')}，启动于 {rec.get('started_at', '?')}）"
                f"仍在运行旧代码 {rec.get('code_sha', '?')}（当前 {cur}）——改动未生效，需重启"
            )
    return out


# ============================================================
# 供 doctor / 启动日志消费的结构化检查结果
# ============================================================
def runtime_checks(project_dir: Path | str) -> list[dict[str, str]]:
    """运行时生效性检查项：``[{"status", "detail", "fix"}, ...]``。

    status 取值与 :class:`agent.core.infra.doctor.CheckItem` 对齐（ok/info/warn/error）。
    """
    items: list[dict[str, str]] = []
    fp = code_fingerprint()
    items.append(
        {
            "status": "ok",
            "detail": (
                f"代码指纹 {fp['sha256']}（{fp['files']} 个 .py，最新改动 {fp['mtime']}）"
            ),
            "fix": "",
        }
    )

    prof = profile_state()
    if prof is None:
        items.append(
            {"status": "info", "detail": "未启用模型档位（走 .env 配置）", "fix": ""}
        )
    else:
        to = int(prof.get("timeout") or 0)
        detail = (
            f"档位 {prof.get('id')}｜model={prof.get('model')}｜"
            f"timeout={to or 'env'}｜max_tokens={prof.get('max_tokens') or 'env'}｜"
            f"thinking={prof.get('enable_thinking')}"
        )
        if to and to < TIMEOUT_WARN_BELOW:
            items.append(
                {
                    "status": "warn",
                    "detail": detail
                    + f" ⚠ timeout<{TIMEOUT_WARN_BELOW}s：思考型上游 p90 常超 90s，"
                    "易致超时→重试→质检降级",
                    "fix": f"把该档位 timeout 提到 ≥300（改 models.json 后需重启进程生效）",
                }
            )
        else:
            items.append({"status": "ok", "detail": detail, "fix": ""})

    shim = shim_state()
    if shim["sitecustomize_loaded"] and not shim["safe_delete_enabled"]:
        items.append(
            {
                "status": "info",
                "detail": (
                    "宿主 safe-delete 护栏未启用（CODEBUDDY_SAFE_DELETE_ENABLED!=1）；"
                    f"shim={Path(shim['sitecustomize_file']).name or '?'}"
                ),
                "fix": "",
            }
        )
    else:
        items.append(
            {
                "status": "ok",
                "detail": (
                    f"删除语义：shim={'on' if shim['sitecustomize_loaded'] else 'off'}"
                    f"｜safe_delete={shim['safe_delete_enabled'] or '-'}"
                    f"｜session={'bound' if shim['session_bound'] else 'free'}"
                ),
                "fix": "",
            }
        )

    stale = stale_running_processes(project_dir)
    if stale:
        items.append(
            {
                "status": "warn",
                "detail": "；".join(stale),
                "fix": "重启对应进程（Web / daemon）后再验证修复是否生效",
            }
        )
    else:
        items.append(
            {"status": "ok", "detail": "无「跑旧代码」的在途进程", "fix": ""}
        )
    return items
