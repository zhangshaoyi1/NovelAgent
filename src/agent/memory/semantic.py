"""语义记忆（SemanticMemory，Phase 2 → Phase 5 向量后端）

长期事实记忆：世界观设定、角色事实、已确立的剧情节点、伏笔登记等"应该长期记得"
的结构化事实。

检索后端（Phase 5 升级）：
- **默认**：离线 char-bigram 余弦（``base.default_scorer``），零依赖、零网络，保证
  测试/离线可用（与项目"降级不阻断"哲学一致）。
- **可选向量后端**：注入 ``embed_fn``（默认用项目已有的 ``rag.embeddings`` 能力，
  OpenAI 兼容 / Ollama 本地）后，写入时存向量、检索时优先向量余弦；``embed_fn``
  缺失 / 抛异常 / 返回空则**自动回退**到 char-bigram，绝不阻断写作主路径。
- 这是规模化（千章级长书）的关键：长程事实检索从"字符串相似度"升级为"语义向量"，
  避免大模型撑爆上下文时丢失远端设定/人设。

持久化：``<project>/.state/memory/semantic.jsonl``（每行一个 MemoryEntry；向量存于
条目的 ``meta.embedding``，随条目落盘，中等规模下可接受）。
"""

from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

from agent.memory.base import MemoryEntry, default_scorer


def _cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度（零依赖）。任一为空向量返回 0.0。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def build_default_embed_fn(provider: str = "openai") -> Callable[[list[str]], list[list[float]]]:
    """构造默认向量函数（懒加载，避免硬依赖 embeddings 模块）。

    复用项目既有 ``agent.client.embeddings``（原 ``agent.core.llm.embeddings``，
    2026-08-29 下沉至 client 层）：openai 走 OpenAI 兼容端点，
    ollama 走本地。调用方缺配置时 ``embed`` 会抛错，由 SemanticMemory 兜底回退。

    Args:
        provider: "openai" | "ollama"。
    """
    def fn(texts: list[str]) -> list[list[float]]:
        from agent.client.embeddings import (
            OllamaEmbedding,
            OpenAICompatibleEmbedding,
        )
        from agent.client.gateway_adapter import _load_config_from_env

        cfg = _load_config_from_env()
        if provider == "ollama":
            ep = OllamaEmbedding(
                model=cfg.embedding_model or cfg.model,
                base_url=cfg.base_url or "http://localhost:11434",
            )
        else:
            ep = OpenAICompatibleEmbedding(
                model=cfg.embedding_model or cfg.model,
                base_url=cfg.embedding_base_url or cfg.base_url,
                api_key=cfg.embedding_api_key or cfg.api_key,
            )
        return ep.embed(texts)

    return fn


class SemanticMemory:
    """语义（长期事实）记忆。

    Args:
        project_dir: 小说项目目录；None 表示纯内存（测试用）。
        scorer: 离线检索打分器（向量不可用时的回退），默认 char-bigram 余弦。
        embed_fn: 向量函数 ``(list[str]) -> list[list[float]]``；提供后启用向量检索。
            可用 ``build_default_embed_fn()`` 构造。None 表示纯离线模式。
    """

    def __init__(
        self,
        project_dir: str | Path | None = None,
        scorer: Callable[[str, str], float] | None = None,
        embed_fn: Callable[[list[str]], list[list[float]]] | None = None,
    ) -> None:
        self.project_dir = Path(project_dir) if project_dir else None
        self.scorer = scorer or default_scorer
        self.embed_fn = embed_fn
        self._lock = threading.RLock()
        self._entries: list[MemoryEntry] = []
        self._vectors: dict[str, list[float]] = {}  # entry.id -> 向量
        self._file = None
        if self.project_dir is not None:
            self._file = self.project_dir / ".state" / "memory" / "semantic.jsonl"
            self._load()
            self._rebuild_vectors()

    # ---------------------------------------------------------------- 向量
    @property
    def vector_enabled(self) -> bool:
        """是否存在可用向量（embed_fn 注入且已有条目向量化）。"""
        return self.embed_fn is not None and bool(self._vectors)

    def _rebuild_vectors(self) -> None:
        self._vectors = {
            e.id: e.meta["embedding"]
            for e in self._entries
            if e.meta and e.meta.get("embedding")
        }

    def _safe_embed(self, texts: list[str]) -> list[list[float]]:
        """调用 embed_fn，异常或返回空则回退为 []（交由 bigram 兜底）。"""
        if self.embed_fn is None:
            return []
        try:
            vecs = self.embed_fn(texts)
            if not vecs or len(vecs) != len(texts):
                return []
            return vecs
        except Exception:  # noqa: BLE001 - 嵌入不可达 → 降级，不阻断
            return []

    # ---------------------------------------------------------------- 持久化
    def _load(self) -> None:
        if self._file is None or not self._file.exists():
            return
        try:
            for line in self._file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                self._entries.append(MemoryEntry.from_dict(json.loads(line)))
        except (json.JSONDecodeError, OSError):
            # 损坏的文件不阻断，视为空
            self._entries = []

    def _persist(self) -> None:
        if self._file is None:
            return
        self._file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._file.with_suffix(".tmp")
        lines = [json.dumps(e.to_dict(), ensure_ascii=False) for e in self._entries]
        tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        tmp.replace(self._file)

    # ---------------------------------------------------------------- 写入
    def add(
        self,
        text: str,
        *,
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
            type=type,
            text=text,
            id=entry_id or uuid.uuid4().hex[:12],
            tags=tags or [],
            source=source,
            meta=meta or {},
        )
        with self._lock:
            if self.embed_fn is not None:
                vecs = self._safe_embed([text])
                if vecs:
                    entry.meta["embedding"] = vecs[0]
                    self._vectors[entry.id] = vecs[0]
            self._entries.append(entry)
            self._persist()
        return entry

    def add_many(self, entries: list[MemoryEntry]) -> None:
        with self._lock:
            if self.embed_fn is not None:
                texts = [e.text for e in entries]
                vecs = self._safe_embed(texts)
                for e, v in zip(entries, vecs):
                    e.meta["embedding"] = v
                    self._vectors[e.id] = v
            self._entries.extend(entries)
            self._persist()

    # ---------------------------------------------------------------- 检索
    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        min_score: float = 0.0,
        types: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> list[tuple[MemoryEntry, float]]:
        """语义检索，按相似度降序返回 (条目, 分数)。

        优先向量余弦（若 ``embed_fn`` 注入且已有向量）；否则 / 向量不可达时回退
        离线 char-bigram 打分。两种情况都尊重 ``types`` / ``tags`` / ``min_score``。

        - ``types``：仅限定类型（None 表示不限）。
        - ``tags``：要求命中至少一个标签（None 表示不限）。
        - ``min_score``：低于该分数的不返回。
        """
        # 向量模式：仅当存在可用向量且至少有一个候选带向量
        if self.embed_fn is not None:
            qvecs = self._safe_embed([query])
            if qvecs:
                qvec = qvecs[0]
                vector_cands = [
                    (e, self._vectors.get(e.id))
                    for e in self._entries
                    if (e.id in self._vectors)
                    and (types is None or e.type in types)
                    and (tags is None or (set(e.tags) & set(tags)))
                ]
                if vector_cands:
                    scored = [
                        (e, _cosine(qvec, v))
                        for e, v in vector_cands
                        if v is not None
                    ]
                    scored.sort(key=lambda x: x[1], reverse=True)
                    return [(e, s) for e, s in scored if s >= min_score][:top_k]

        # 回退：离线 char-bigram（或无向量的纯文本候选）
        scored: list[tuple[MemoryEntry, float]] = []
        for e in self._entries:
            if types and e.type not in types:
                continue
            if tags and not (set(e.tags) & set(tags)):
                continue
            s = self.scorer(query, e.text)
            if s >= min_score:
                scored.append((e, s))
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
