"""命令单一注册点装饰器（T-1）

`@command` 一次性完成：
  1. 向 typer `app` 注册命令（保持连字符命名约定，与旧 ``@app.command()`` 行为一致）
  2. 把 ``CommandMeta`` 追加到 ``command_router.COMMAND_REGISTRY``
  3. 声明 ``allowed_states`` / ``is_global`` 供门禁派生
  4. 声明 ``writes`` 供**项目写锁自动生效**（L1-2）

新增命令只需在该命令模块写一次：

    from agent.cli.registry import command
    from agent.core.engine.state_machine import State

    @command(allowed_states=(State.WRITING,))
    def write(...): ...

或辅助/全局命令：

    @command(global_=True)
    def status(...): ...

会向小说项目落盘的命令加 ``writes=True``，派发时自动获取项目写锁，无需在命令
函数体里手写加锁：

    @command(writes=True)
    def rewrite(...): ...

    # 仅 --apply 时落盘（其余为只读巡检），用 writes_when 精确控制
    @command(writes=True, writes_when=lambda kw: bool(kw.get("apply")))
    def deslop(...): ...

命令即注册点，门禁由命令元数据（``allowed_states`` / ``is_global``）自动派生，
写锁由 ``writes`` 元数据自动派生，无需手维护命令清单双表。
"""

from __future__ import annotations

import functools
import inspect
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from agent.core.engine.command_router import COMMAND_REGISTRY, CommandMeta

#: 项目目录参数名候选（按优先级）。命令函数里声明了其中任一个即视为「面向某项目」。
_DIR_ARG_NAMES = ("project_dir", "directory", "dir", "project", "project_path")

#: 判定某目录是否真为小说项目的标记文件/目录（存在任一即算）。
_PROJECT_MARKERS = ("world.md", ".state", "chapters")


def _unwrap_option_default(value: Any) -> Any:
    """把 typer 的 ``OptionInfo``/``ArgumentInfo`` 还原为其真实默认值。

    命令函数签名里 ``project_dir: str = typer.Option(".", ...)`` 的默认值是
    ``OptionInfo`` 实例而非字符串，需取 ``.default`` 才是实际路径。
    """
    cls_name = type(value).__name__
    if cls_name in ("OptionInfo", "ArgumentInfo") and hasattr(value, "default"):
        return value.default
    return value


def _looks_like_project(path: Path) -> bool:
    """目录是否存在且像一本小说项目。

    用途：避免把 cwd（如仓库根目录）误当成项目而加锁——默认参数常为 ``"."``，
    在仓库根下跑命令时不该锁住整个仓库。
    """
    try:
        if not path.exists() or not path.is_dir():
            return False
    except OSError:
        return False
    return any((path / marker).exists() for marker in _PROJECT_MARKERS)


def resolve_project_dir(fn: Callable[..., Any], args: tuple, kwargs: dict) -> Path | None:
    """从实际调用参数里解析命令作用的项目目录；解析不出则返回 None（跳过加锁）。"""
    try:
        bound = inspect.signature(fn).bind_partial(*args, **kwargs)
        bound.apply_defaults()
    except (TypeError, ValueError):
        return None
    for name in _DIR_ARG_NAMES:
        if name not in bound.arguments:
            continue
        raw = _unwrap_option_default(bound.arguments[name])
        if raw is None or isinstance(raw, bool):
            continue
        if not isinstance(raw, (str, Path)):
            continue
        text = str(raw).strip()
        if not text:
            continue
        try:
            candidate = Path(text).resolve()
        except (OSError, ValueError) as exc:
            import logging

            logging.warning(
                "写锁：项目目录解析失败，本次跳过加锁（dir=%r, cmd=%s）：%s",
                text,
                getattr(fn, "__name__", "?"),
                exc,
            )
            continue
        if _looks_like_project(candidate):
            return candidate
    return None


def _acquire_write_lock(fn: Callable[..., Any], display: str, args: tuple, kwargs: dict) -> None:
    """写命令派发前获取项目写锁；锁被占用则打印友好提示并以退出码 2 结束。"""
    project_dir = resolve_project_dir(fn, args, kwargs)
    if project_dir is None:
        return
    from agent.core.project_lock import ProjectLockBusy, acquire_project_lock

    try:
        acquire_project_lock(project_dir, display)
    except ProjectLockBusy as exc:
        import typer

        from agent.cli._app import console

        console.print(f"[bold red]✗[/bold red] {exc}")
        raise typer.Exit(code=2) from exc


def _with_write_lock(
    display: str,
    fn: Callable[..., Any],
    writes_when: Optional[Callable[[dict], bool]],
) -> Callable[..., Any]:
    """给命令函数套一层「派发前自动加项目写锁」的外壳。

    用 ``functools.wraps`` 保持原始签名与注解，typer 仍按原函数签名构建 CLI
    （已验证 typer 0.27 在 ``from __future__ import annotations`` 场景下正确解析）。
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if writes_when is None or writes_when(kwargs):
            _acquire_write_lock(fn, display, args, kwargs)
        return fn(*args, **kwargs)

    return wrapper


def command(
    name: Optional[str] = None,
    allowed_states: Optional[Iterable[Any]] = None,
    global_: bool = False,
    help: Optional[str] = None,
    writes: bool = False,
    writes_when: Optional[Callable[[dict], bool]] = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """命令注册装饰器（单一注册点）。

    Args:
        name: 显式命令名（默认取函数名）。注册表与 typer 统一使用连字符形式，
            末尾多余的连字符会被去除（兼容 ``help_`` → ``help`` 这类函数名）。
        allowed_states: 允许执行该命令的状态集合（iterable[State]）。
        global_: 若为 True，任意状态下可用（辅助/全局命令）。
        help: 命令描述（默认取函数 docstring）。
        writes: 该命令会向小说项目落盘。为 True 时派发前自动获取项目写锁，
            同一项目上与其他写命令互斥（L1-2，根治 Web/CLI 并发写事故）。
        writes_when: 可选的精确判定函数，接收已绑定的关键字参数，返回 True 才加锁。
            适用于「只有加了 --apply 才落盘」这类命令（如 deslop）。

    Returns:
        装饰器。
    """
    from agent.cli._app import app  # 延迟导入，避免与 _app 的循环依赖

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        raw = name or fn.__name__
        # typer / 注册表统一使用连字符约定（兼容 help_ → help）
        display = raw.replace("_", "-").strip("-")
        # 0) 写命令登记：无论该命令名是否已在基线 COMMAND_REGISTRY 中，都记入
        #    WRITE_COMMANDS（基线条目不会被装饰器覆盖，故单独成表，作为 Web
        #    预检与架构红线的唯一真相源）。
        if writes:
            from agent.core.engine.command_router import register_write_command

            register_write_command(display)
        # 1) 向 typer 注册命令。
        #    不传显式 name（name 默认 None）→ typer 从 callback 函数名派生命令名，
        #    并保持 c.name=None，兼容既有测试 ``c.name or c.callback.__name__`` 的取值。
        #    若调用方显式传入 name，则作为 typer 命令名（与注册表 display 一致）。
        target = _with_write_lock(display, fn, writes_when) if writes else fn
        app.command(name, help=help)(target)
        # 2) 登记/补全元数据（命令名唯一键）。
        #    命令模块经 @command 装饰即注册点；但若命令名已存在于基线 COMMAND_REGISTRY
        #    （如 command_router 中 curated 的元数据），则跳过覆盖，保留基线描述与门禁字段，
        #    避免装饰器的 docstring 覆盖既有 curated 描述（既有测试依赖）。
        meta = CommandMeta(
            "/" + display,
            help or (fn.__doc__ or ""),
            allowed_states=tuple(allowed_states) if allowed_states else None,
            is_global=bool(global_),
            writes=bool(writes),
        )
        if any(c.name == meta.name for c in COMMAND_REGISTRY):
            return fn
        COMMAND_REGISTRY.append(meta)
        return fn

    return decorator
