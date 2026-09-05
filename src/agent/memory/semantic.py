"""语义记忆（SemanticMemory，Phase 2 → Phase 5 向量后端）

长期事实记忆：世界观设定、角色事实、已确立的剧情节点、伏笔登记等"应该长期记得"
的结构化事实。

检索后端（Phase 5 升级）：
- **默认**：离线 char-bigram 余弦（``base.default_scorer``），零依赖、零网络，保证
  测试/离线可用（与项目"降级不阻断"哲学一致）。
- **可选向量后端**：注入 ``embed_fn`` 后，写入时存向量、检索时优先向量余弦；
  ``embed_fn`` 缺失 / 抛异常 / 返回空则**自动回退**到 char-bigram，绝不阻断写作主路径。

持久化：``<project>/.state/memory/semantic.jsonl``（每行一个 MemoryEntry；向量存于
条目的 ``meta.embedding``，随条目落盘，中等规模下可接受）。
"""

from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

from agent.memory.base import MemoryEntry, default_scorer, _cosine


def build_default_embed_fn(provider: Any = None) -> Callable[[list[str]], list[list[float]]]:
    """构造默认向量函数（懒加载，避免硬依赖 embeddings 模块）。

    复用项目既有 ``agent.client.embeddings`` 能力；配置不可用时抛出的异常
    由调用方（SemanticMemory._safe_embed）捕获并回退离线打分。
    """
    def fn(texts: list[str]) -> list[list[float]]:
        from agent.client.embeddings import (
            OllamaEmbedding,
            OpenAICompatibleEmbedding,
        )
        from agent.client.gateway_adapter import _load_config_from_env

        config = _load_config_from_env()
        embedding_model = getattr(config, "embedding_model", "") or config.model
        embedding_base_url = getattr(config, "embedding_base_url", "") or config.base_url
        embedding_api_key = getattr(config, "embedding_api_key", "") or config.api_key
        impl = (
            OllamaEmbedding(model=embedding_model, base_url=embedding_base_url)
            if (config.provider or "").lower() == "ollama"
            else OpenAICompatibleEmbedding(
                model=embedding_model,
                base_url=embedding_base_url,
                api_key=embedding_api_key,
            )
        )
        return impl.embed(texts)

    return fn


def _cosine_vec(a: list[float], b: list[float]) -> float:
    """余弦相似度（零依赖）。任一为空向量返回 0.0。"""
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class SemanticMemory:
    """语义记忆（长期事实 + 可选向量检索）"""

    def __init__(
        self,
        project_dir: Path | str | None = None,
        scorer: Callable[[str, str], float] = default_scorer,
        embed_fn: Callable[[list[str]], list[list[float]]] | None = None,
    ) -> None:
        self.project_dir = Path(project_dir) if project_dir else None
        self.scorer = scorer
        self.embed_fn = embed_fn
        self._lock = threading.RLock()
        self._entries: list[MemoryEntry] = []
        self._vectors: dict[str, list[float]] = {}
        self._file = (
            self.project_dir / ".state" / "memory" / "semantic.jsonl"
            if self.project_dir
            else None
        )
        self._load()
        self._rebuild_vectors()

    @property
    def vector_enabled(self) -> bool:
        """是否存在可用向量（embed_fn 注入且已有条目向量化）。"""
        return self.embed_fn is not None and bool(self._vectors)

    def _rebuild_vectors(self) -> None:
        self._vectors = {
            e.id: e.meta["embedding"]
            for e in self._entries
            if e.meta.get("embedding")
        }

    def _safe_embed(self, texts: list[str]) -> list[list[float]]:
        """调用 embed_fn，异常或返回空则回退为 []（交由 bigram 兜底）。"""
        if not self.embed_fn:
            return []
        try:
            vecs = self.embed_fn(texts)
            if vecs and len(vecs) == len(texts):
                return vecs
        except Exception:  # noqa: BLE001 - 向量不可达时回退离线打分
            pass
        return []

    def _load(self) -> None:
        if not self._file or not self._file.exists():
            return
        try:
            for line in self._file.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self._entries.append(MemoryEntry.from_dict(json.loads(line)))
        except (json.JSONDecodeError, OSError):
            pass

    def _persist(self) -> None:
        if not self._file:
            return
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._file.with_suffix(".jsonl.tmp")
            tmp.write_text(
                "\n".join(json.dumps(e.to_dict(), ensure_ascii=False) for e in self._entries),
                encoding="utf-8",
            )
            tmp.replace(self._file)
        except OSError:
            pass

    def add(
        self,
        text: str,
        type: str = "fact",
        tags: list[str] | None = None,
        source: str = "",
        meta: dict[str, Any] | None = None,
        entry_id: str | None = None,
    ) -> MemoryEntry:
        """新增一条语义记忆，返回该条目。

        若启用向量后端（``embed_fn`` 注入），同时写入该条文本的向量（存于
        ``meta.embedding``，随条目落盘）。
        """
        entry = MemoryEntry(
            id=entry_id or uuid.uuid4().hex,
            type=type,
            text=text,
            tags=list(tags or []),
            source=source,
            meta=dict(meta or {}),
        )
        with self._lock:
            if self.embed_fn:
                vecs = self._safe_embed([text])
                if vecs:
                    entry.meta["embedding"] = vecs[0]
                    self._vectors[entry.id] = vecs[0]
            self._entries.append(entry)
            self._persist()
        return entry

    def add_many(
        self,
        entries: list[dict[str, Any]],
    ) -> list[MemoryEntry]:
        """批量新增（每项为 add() 的关键字参数字典）。"""
        added: list[MemoryEntry] = []
        vecs = self._safe_embed([e.get("text", "") for e in entries]) if self.embed_fn else []
        with self._lock:
            for i, e in enumerate(entries):
                entry = MemoryEntry(
                    id=e.get("entry_id") or uuid.uuid4().hex,
                    type=e.get("type", "fact"),
                    text=e.get("text", ""),
                    tags=list(e.get("tags") or []),
                    source=e.get("source", ""),
                    meta=dict(e.get("meta") or {}),
                )
                if vecs:
                    entry.meta["embedding"] = vecs[i]
                    self._vectors[entry.id] = vecs[i]
                self._entries.append(entry)
                added.append(entry)
            self._persist()
        return added

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        min_score: float = 0.0,
        types: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> list[tuple[MemoryEntry, float]]:
        """语义检索，按相似度降序返回 (条目, 分数)。

        优先向量余弦（若 ``embed_fn`` 注入且已有向量）；否则 / 向量不可达时回退
        离线 char-bigram 打分。两种情况都尊重 ``types`` / ``tags`` 过滤。
        """
        qvecs = self._safe_embed([query]) if self.embed_fn else []
        qv = qvecs[0] if qvecs else None
        use_vector = bool(qv) and bool(self._vectors)
        tset = set(types) if types else None
        tags_set = set(tags) if tags else None
        scored: list[tuple[MemoryEntry, float]] = []
        with self._lock:
            for e in self._entries:
                if tset is not None and e.type not in tset:
                    continue
                if tags_set is not None and not tags_set.intersection(e.tags):
                    continue
                if use_vector:
                    vec = self._vectors.get(e.id)
                    score = _cosine_vec(qv, vec) if vec else 0.0
                else:
                    score = self.scorer(query, e.text)
                if score >= min_score:
                    scored.append((e, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    def get(self, entry_id: str) -> MemoryEntry | None:
        with self._lock:
            return next((e for e in self._entries if e.id == entry_id), None)

    def all(self) -> list[MemoryEntry]:
        with self._lock:
            return list(self._entries)

    def count(self) -> int:
        with self._lock:
            return len(self._entries)
