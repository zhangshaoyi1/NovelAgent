"""E2 运行时题材套路注入存储

injected_tropes 是一次性、跨命令（inject-genre → write）的运行期上下文，
不应与持久化工作流状态（state.json）混在一起。此处独立落盘到
``.state/injected_tropes.json``，避免污染 StateMachine，也避免 write 中途
崩溃后脏状态残留在主状态文件里。
"""

from __future__ import annotations

import json
from pathlib import Path

from agent.utils import safe_remove


class InjectedTropeStore:
    """读写 ``.state/injected_tropes.json`` 中的注入套路列表"""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = Path(project_dir)
        self.file = self.project_dir / ".state" / "injected_tropes.json"

    def get(self) -> list[str]:
        """读取当前注入的套路列表（文件缺失返回空）"""
        if not self.file.exists():
            return []
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        return list(data.get("tropes", []) or [])

    def set(self, tropes: list[str]) -> None:
        """覆盖写入套路列表"""
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(
            json.dumps({"tropes": list(tropes)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def add(self, trope: str) -> list[str]:
        """追加一个套路（去重），返回最新列表"""
        cur = self.get()
        if trope not in cur:
            cur.append(trope)
        self.set(cur)
        return cur

    def clear(self) -> None:
        """清除注入套路（生成后调用）"""
        if self.file.exists():
            safe_remove(self.file)
