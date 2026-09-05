"""import 守卫（红线三层中的运行期钩子，对应 m0-guardrails T2）

用 ``sys.addaudithook`` 在运行期强制「仅 gateway/client 可 import provider SDK」：
- 任何位于 agent/llmagent 源码树内的模块，若不在白名单目录
  （`llmagent/gateway/`、`agent/client/`）中，import `openai` / `ollama`
  会立即抛 ``ImportError``；
- 白名单外的源码树模块（业务层）即使绕过静态检查，运行期也会被硬拦截；
- 源码树之外的调用方（site-packages、tests）不受影响，避免破坏依赖内部实现。

启用方式::

    from llmagent.kernel.import_guard import install_import_guard

    install_import_guard()          # 进程级安装一次（幂等），如 agent/__main__.py

纯函数 ``violates()`` 可单独测试，不必真的 import provider SDK。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# provider SDK 顶层模块名（R1 红线）
FORBIDDEN_TOP_MODULES = ("openai", "ollama")

# 允许 import provider SDK 的目录（相对各 src 根的 posix 前缀，R1 白名单）
ALLOWED_PREFIXES = ("llmagent/gateway", "agent/client")

_installed = False


def _norm(path: str) -> str:
    """统一为小写 posix 绝对路径。"""
    return str(Path(path).resolve()).replace("\\", "/").lower()


def _src_roots() -> list[str]:
    """agent 包的 src 根（src/ 的父目录，即包含 agent/ 与 llmagent/ 的目录）。"""
    here = Path(__file__).resolve()  # .../src/llmagent/kernel/import_guard.py
    src = here.parent.parent.parent  # .../src
    return [_norm(str(src))]


def _rel_under(filename: str, root: str) -> str | None:
    """filename 在 root 下则返回相对 posix 路径，否则 None。"""
    fn = _norm(filename)
    if not fn.startswith(root + "/"):
        return None
    return fn[len(root) + 1:]


def violates(caller_file: str, module_name: str) -> str | None:
    """判定一次 import 是否违反 R1。返回违规说明，None 表示放行。

    规则：调用方位于 agent/llmagent 源码树内、且相对路径不在
    `llmagent/gateway` 或 `agent/client` 前缀下时，import provider SDK 违规。
    """
    top = module_name.split(".")[0] if module_name else ""
    if top not in FORBIDDEN_TOP_MODULES:
        return None
    if not caller_file:
        return None
    for root in _src_roots():
        rel = _rel_under(caller_file, root)
        if rel is None:
            continue
        if rel.startswith(ALLOWED_PREFIXES):
            return None
        return (
            f"[redline] {rel} 禁止直接 import '{top}'：provider SDK 仅允许 "
            f"llmagent/gateway/ 与 agent/client/ 引用（R1）"
        )
    return None


def _find_caller_file() -> str:
    """从审计钩子的调用栈向上找第一个非 importlib 的业务帧。"""
    frame = sys._getframe(2)  # 0=hook, 1=_audit_import, 2=调用方
    while frame is not None:
        fname = frame.f_code.co_filename
        if fname and "importlib" not in fname and "<frozen" not in fname:
            return fname
        frame = frame.f_back
    return ""


def _audit_import(event: str, args: tuple) -> None:
    if event != "import":
        return
    name = args[0]
    if not isinstance(name, str):
        return
    file = _find_caller_file()
    msg = violates(file, name)
    if msg:
        raise ImportError(msg)


def install_import_guard(*, force: bool | None = None) -> bool:
    """安装运行期 import 守卫（幂等）。

    可用环境变量 ``LLMAGENT_IMPORT_GUARD=0`` 显式关闭（调试逃生口，
    本身不需要 escape_hatch 豁免——它不绕过任何门禁，只是关闭增强监控）。
    返回是否实际安装。
    """
    global _installed
    if _installed:
        return False
    if force is None:
        force = os.environ.get("LLMAGENT_IMPORT_GUARD", "1").strip().lower() not in (
            "0", "false", "no", "off",
        )
    if not force:
        return False
    sys.addaudithook(_audit_import)
    _installed = True
    return True
