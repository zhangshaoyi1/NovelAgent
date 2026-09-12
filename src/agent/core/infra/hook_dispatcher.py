"""题材包 hook 分发器（T-3）

让 SKILL.md 的 hooks 真实执行。每个 hook 被调用时传入 (project_dir, genre, pack)
（遵循共享约定 4）。解析或执行失败仅 warning，不中断宿主。

hook 规格两种形式（R6 运行时护栏）：
- **具名注册**（bare name，如 ``load_genre_template``）：经 ``register_genre_hook()``
  由上层模块（workflows/agents/cli）在自身 import 时**反向注册**进本表——core 不反向
  import 上层模块，依赖方向由上层指向 core；
- **点分规格**（``module.func``）：运行期 importlib 解析，**仅放行 ``agent.core.*``
  内部模块**与第三方模块；指向上层（``agent.workflows`` / ``agent.agents`` / …）的
  规格一律拒绝并告警，绝不 import——R6「core 不得依赖上层」的运行时执行点。
"""
from __future__ import annotations

import importlib
import warnings
from pathlib import Path
from typing import Any, Callable

_GENRE_HOOKS: dict[str, Callable[..., Any]] = {}

# 点分规格禁止 import 的 agent 顶层子包（= core 之外的全部上层/接入层）
_FORBIDDEN_TOPS = frozenset(
    {"workflows", "agents", "cli", "web", "service", "tasks", "memory", "session", "daemon", "client"}
)


def register_genre_hook(name: str, fn: Callable[..., Any]) -> None:
    """注册具名题材 hook。

    由**上层模块**在 import 时调用（反向注入）：core 只持有可调用对象，
    不 import 上层模块。SKILL.md 的 hooks 用注册名（bare name）引用。
    """
    _GENRE_HOOKS[name] = fn


def _resolve(spec: str) -> tuple[Callable[..., Any] | None, str | None]:
    """把 hook 规格解析为可调用对象。

    Returns:
        (callable, None) 或 (None, 拒绝/未找到原因)。
    """
    if "." not in spec:
        fn = _GENRE_HOOKS.get(spec)
        if fn is None:
            return None, f"未注册的具名 hook：{spec!r}（须先 register_genre_hook 注册）"
        return fn, None
    module_name, func_name = spec.rsplit(".", 1)
    if module_name == "agent" or module_name.startswith("agent."):
        parts = module_name.split(".")
        top = parts[1] if len(parts) > 1 else ""
        if top in _FORBIDDEN_TOPS:
            return None, (
                f"hook 规格 {spec!r} 指向上层模块 agent.{top}——R6 禁止 core 反向 import，"
                f"请改为 register_genre_hook 具名注册"
            )
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        return None, f"模块不可导入：{module_name}（{exc}）"
    fn = getattr(module, func_name, None)
    if fn is None:
        return None, f"模块 {module_name} 中不存在 {func_name!r}"
    return fn, None


def dispatch_genre_hooks(project_dir: Path, genre: str, pack: Any) -> list[str]:
    """执行题材包声明的 hooks

    T-3：让 SKILL.md 的 hooks 真实执行；解析/执行失败仅 warning 不崩溃。

    Args:
        project_dir: 项目目录
        genre: 题材名
        pack: 已加载的 GenrePack（含 manifest.hooks）

    Returns:
        成功执行的 hook 规格列表。
    """
    dispatched: list[str] = []
    manifest = getattr(pack, "manifest", None)
    hook_list = getattr(manifest, "hooks", []) if manifest is not None else []
    for hook_spec in hook_list:
        try:
            if not isinstance(hook_spec, str) or not hook_spec:
                raise ValueError(f"hook 规格应为 'module.func' 或注册名，实际: {hook_spec!r}")
            fn, reason = _resolve(hook_spec)
            if fn is None:
                raise ValueError(reason or f"hook 规格无法解析: {hook_spec!r}")
            fn(project_dir=Path(project_dir), genre=genre, pack=pack)
            dispatched.append(hook_spec)
        except Exception as exc:  # noqa: BLE001 - 分发失败仅 warning，不中断宿主
            warnings.warn(
                f"题材 hook 执行失败，已跳过：{hook_spec}（{exc}）",
                stacklevel=2,
            )  # noqa: SILENT_DEGRADE
    return dispatched
