"""BM25 倒排兜底索引（增量 A / T02）

纯 Python BM25 实现，作为向量召回不可达（embed 失败）时的兜底召回。
对中文采用「按字切分 + ASCII 词切分」的轻量 tokenizer（零依赖，无需 jieba），
足以在长篇小说语境下提供可用的关键词召回。
"""

from __future__ import annotations

import math
import re

from agent.core.rag._types import Chunk, Hit

# ASCII 词 / 中文单字 的轻量切分（避免引入 jieba 等依赖）
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]")


class BM25Index:
    """BM25 倒排索引（纯 Python 兜底）"""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self._chunks: list[Chunk] = []
        self._tokenized: list[list[str]] = []
        self._df: dict[str, int] = {}
        self._idf: dict[str, float] = {}
        self._avgdl: float = 0.0
        self._built = False

    # ============================================================
    # 构建
    # ============================================================
    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return _TOKEN_RE.findall(text.lower())

    def index(self, docs: list[Chunk]) -> None:
        """增量追加并建立/刷新 BM25 统计（idf/avgdl）"""
        for doc in docs:
            toks = self._tokenize(doc.text)
            self._chunks.append(doc)
            self._tokenized.append(toks)
        self._rebuild_stats()

    def _rebuild_stats(self) -> None:
        n = len(self._tokenized)
        df: dict[str, int] = {}
        lengths: list[int] = []
        for toks in self._tokenized:
            lengths.append(len(toks))
            seen: set[str] = set()
            for t in toks:
                if t not in seen:
                    seen.add(t)
                    df[t] = df.get(t, 0) + 1
        self._df = df
        self._idf = {
            t: math.log((n - df_t + 0.5) / (df_t + 0.5) + 1.0)
            for t, df_t in df.items()
        }
        self._avgdl = (sum(lengths) / n) if n else 0.0
        self._built = True

    # ============================================================
    # 查询
    # ============================================================
    def search(self, query: str, top_k: int = 5) -> list[Hit]:
        if not self._built or not self._chunks:
            return []
        q_terms = self._tokenize(query)
        if not q_terms:
            return []
        scored: list[tuple[float, Chunk]] = []
        for idx, toks in enumerate(self._tokenized):
            score = self._score(q_terms, toks)
            if score > 0:
                scored.append((score, self._chunks[idx]))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [Hit(chunk=c, score=s) for s, c in scored[:top_k]]

    def _score(self, q_terms: list[str], doc_toks: list[str]) -> float:
        tf: dict[str, int] = {}
        for t in doc_toks:
            tf[t] = tf.get(t, 0) + 1
        dl = len(doc_toks)
        total = 0.0
        for t in q_terms:
            idf = self._idf.get(t)
            if idf is None:
                continue
            f = tf.get(t, 0)
            if f == 0:
                continue
            denom = f + self.k1 * (1.0 - self.b + self.b * (dl / (self._avgdl or 1.0)))
            total += idf * (f * (self.k1 + 1.0)) / denom
        return total
