"""LLMOps · 提示版本管理（Phase 3）

登记每条提示（系统提示 / 模板）的文本与哈希，检测**漂移**（同一 key 的文本变了）
并分配版本号。评测回归时可比对"提示版本 X 对应的体检结果"，定位是否因改提示导致退化。

持久化：``<project>/.state/llmops/prompts.json``（key -> {version, hash, text, updated_at}）。
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any


class PromptRegistry:
    """提示版本登记表。"""

    def __init__(self, project_dir: str | Path | None = None) -> None:
        self.project_dir = Path(project_dir) if project_dir else None
        self._data: dict[str, dict[str, Any]] = {}
        if self.project_dir is not None:
            self._file = self.project_dir / ".state" / "llmops" / "prompts.json"
            self._load()
        else:
            self._file = None

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    def _load(self) -> None:
        if self._file is None or not self._file.exists():
            return
        try:
            self._data = json.loads(self._file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            self._data = {}

    def _persist(self) -> None:
        if self._file is None:
            return
        self._file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._file)

    def register(self, key: str, text: str) -> dict[str, Any]:
        """登记/更新一条提示，返回 {version, hash, drifted}。"""
        h = self._hash(text)
        existing = self._data.get(key)
        if existing is None:
            version = 1
            drifted = False
        elif existing["hash"] == h:
            # 未变化
            return {"version": existing["version"], "hash": h, "drifted": False,
                    "updated": False}
        else:
            version = existing["version"] + 1
            drifted = True
        self._data[key] = {
            "version": version,
            "hash": h,
            "text": text,
            "updated_at": time.time(),
        }
        self._persist()
        return {"version": version, "hash": h, "drifted": drifted, "updated": True}

    def version(self, key: str) -> int:
        return int(self._data.get(key, {}).get("version", 0))

    def hash(self, key: str) -> str:
        return str(self._data.get(key, {}).get("hash", ""))

    def all(self) -> dict[str, dict[str, Any]]:
        return {k: {"version": v["version"], "hash": v["hash"]} for k, v in self._data.items()}
