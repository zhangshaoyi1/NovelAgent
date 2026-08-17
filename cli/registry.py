"""命令单一注册点装饰器（T-1）

`@command` 一次性完成：
  1. 向 typer `app` 注册命令（保持连字符命名约定，与旧 ``@app.command()`` 行为一致）
  2. 把 ``CommandMeta`` 追加到 ``command_router.COMMAND_REGISTRY``
  3. 声明 ``allowed_states`` / ``is_global`` 供门禁派生

新增命令只需在该命令模块写一次：

    from agent.cli.registry import command
    from agent.core.state_machine import State

    @command(allowed_states=(State.WRITING,))
    def write(...): ...

或辅助/全局命令：

    @command(global_=True)
    def status(...): ...

命令即注册点，门禁由命令元数据（``allowed_states`` / ``is_global``）自动派生，
无需手维护命令清单双表。
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Optional

from agent.core.command_router import COMMAND_REGISTRY, CommandMeta


def command(
    name: Optional[str] = None,
    allowed_states: Optional[Iterable[Any]] = None,
    global_: bool = False,
    help: Optional[str] = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """命令注册装饰器（单一注册点）。

    Args:
        name: 显式命令名（默认取函数名）。注册表与 typer 统一使用连字符形式，
            末尾多余的连字符会被去除（兼容 ``help_`` → ``help`` 这类函数名）。
        allowed_states: 允许执行该命令的状态集合（iterable[State]）。
        global_: 若为 True，任意状态下可用（辅助/全局命令）。
        help: 命令描述（默认取函数 docstring）。

    Returns:
        装饰器。
    """
    from agent.cli._app import app  # 延迟导入，避免与 _app 的循环依赖

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        raw = name or fn.__name__
        # typer / 注册表统一使用连字符约定（兼容 help_ → help）
        display = raw.replace("_", "-").strip("-")
        # 1) 向 typer 注册命令。
        #    不传显式 name（name 默认 None）→ typer 从 callback 函数名派生命令名，
        #    并保持 c.name=None，兼容既有测试 ``c.name or c.callback.__name__`` 的取值。
        #    若调用方显式传入 name，则作为 typer 命令名（与注册表 display 一致）。
        app.command(name, help=help)(fn)
        # 2) 登记/补全元数据（命令名唯一键）。
        #    命令模块经 @command 装饰即注册点；但若命令名已存在于基线 COMMAND_REGISTRY
        #    （如 command_router 中 curated 的元数据），则跳过覆盖，保留基线描述与门禁字段，
        #    避免装饰器的 docstring 覆盖既有 curated 描述（既有测试依赖）。
        meta = CommandMeta(
            "/" + display,
            help or (fn.__doc__ or ""),
            allowed_states=tuple(allowed_states) if allowed_states else None,
            is_global=bool(global_),
        )
        if any(c.name == meta.name for c in COMMAND_REGISTRY):
            return fn
        COMMAND_REGISTRY.append(meta)
        return fn

    return decorator
