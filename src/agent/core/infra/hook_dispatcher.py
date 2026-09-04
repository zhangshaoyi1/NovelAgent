"""题材包 hook 分发器（T-3）

让 SKILL.md 的 hooks 真实执行。每个 hook 形如 ``"module.func"``，被调用时传入
(project_dir, genre, pack)（遵循共享约定 4）。解析或执行失败仅 warning，不中断宿主。
"""
from __future__ import annotations

import importlib
import warnings
from pathlib import Path
from typing import Any


def dispatch_genre_hooks(project_dir: Path, genre: str, pack: Any) -> list[str]:
    """执行题材包声明的 hooks（module.func 形式）

    T-3：让 SKILL.md 的 hooks 真实执行；解析/执行失败仅 warning 不崩溃。

    Args:
        project_dir: 项目目录
        genre: 题材名
        pack: 已加载的 GenrePack（含 manifest.hooks）

    Returns:
        成功执行的 hook 规格列表（如
        ["agent.workflows.planning.m1_config.load_genre_template"]）。
    """
    dispatched: list[str] = []
    manifest = getattr(pack, "manifest", None)
    hook_list = getattr(manifest, "hooks", []) if manifest is not None else []
    for hook_spec in hook_list:
        try:
            if not isinstance(hook_spec, str) or "." not in hook_spec:
                raise ValueError(f"hook 规格应为 'module.func'，实际: {hook_spec!r}")
            module_name, func_name = hook_spec.rsplit(".", 1)
            module = importlib.import_module(module_name)
            func = getattr(module, func_name)
            func(project_dir=Path(project_dir), genre=genre, pack=pack)
            dispatched.append(hook_spec)
        except Exception as exc:  # noqa: BLE001 - 分发失败仅 warning，不中断宿主
            warnings.warn(
                f"题材 hook 执行失败，已跳过：{hook_spec}（{exc}）",
                stacklevel=2,
            )
    return dispatched
