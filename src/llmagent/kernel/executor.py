"""Kernel Executor + MountResolver

Executor 基类与 MountResolver 映射表。
"""

from __future__ import annotations

from .task import Executor, TaskKind


class MountResolver:
    """kind → Executor 映射表

    M0 仅 LLM/TOOL/WORKFLOW/VALIDATOR 必须；其余占位。
    """

    def __init__(self) -> None:
        self._mapping: dict[TaskKind, type[Executor]] = {}

    def mount(self, kind: TaskKind, executor_cls: type[Executor]) -> None:
        self._mapping[kind] = executor_cls

    def resolve(self, kind: TaskKind) -> type[Executor]:
        executor_cls = self._mapping.get(kind)
        if executor_cls is None:
            raise ValueError(f"未注册的 TaskKind: {kind}")
        return executor_cls