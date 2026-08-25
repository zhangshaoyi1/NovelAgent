"""CLI 应用实例与共享控制台（从原 cli.py 拆出）

所有命令模块共享同一个 typer.Typer 实例与 rich.Console。

注意：模块名用 _app.py 而非 app.py，避免被包属性 `app`（Typer 实例）
在 `from agent.cli.app import app` 时被同名属性遮蔽。
"""

from __future__ import annotations

import sys
import typer
from rich.console import Console

# Windows / 中文系统默认控制台编码为 GBK（cp936）。rich 输出大量使用 ✓/✗/⚠/→
# 等符号，写入 GBK 流会抛 UnicodeEncodeError（直跑崩溃；Web 子进程经 runner.py
# 以 UTF-8 解码也会乱码）。修复：在 Windows 上把控制台输出代码页切到 UTF-8
# （65001）并同步把 stdout/stderr 设为 UTF-8 编码——中文与符号均正常显示，
# 子进程 PIPE 输出 UTF-8 后父进程 decode("utf-8") 也正确。
if sys.platform == "win32":
    try:
        import ctypes

        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:  # noqa: BLE001 - 无控制台（服务/管道）时静默跳过
        pass
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
except (ValueError, OSError):
    pass

app = typer.Typer(
    name="novel-agent",
    help="共创式小说写作 Agent - 设定集驱动的长篇一致性 + 剧集树 + 关系演化",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
console = Console()

# 命令单一注册点装饰器（T-1）：命令模块 ``from agent.cli._app import command``
from agent.cli.registry import command  # noqa: E402,F401
