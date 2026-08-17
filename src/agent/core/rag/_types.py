"""RAG 共享数据类型（增量 A / T02）

- ``Chunk``：被索引/召回的最小语义单元（切片）。
- ``Hit``：召回结果（带分值的 Chunk 引用）。

独立成模块以避免 ``agent.core.rag`` 包内循环导入（``vector_store`` /
``bm25`` / ``indexer`` / ``retriever`` 均引用本模块，而本模块不反向依赖包内其它模块）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Chunk:
    """被索引 / 召回的最小语义单元（段）"""

    text: str
    source: str = ""          # 来源文件（相对项目根），如 chapters/ch003.md
    chapter_num: int = 0       # 来源章节号（非章节内容则为 0）
    kind: str = ""             # 类型：setting / character / subline / relation / foreshadow / chapter
    embedding: Optional[list[float]] = None  # 向量；缺失（embed 失败）时为 None


@dataclass
class Hit:
    """召回结果（带分值的 Chunk 引用）"""

    chunk: Chunk
    score: float
