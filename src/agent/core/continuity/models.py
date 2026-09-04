"""连续性账本 · 数据模型（G15 P0-1）

对标 DeepWrite `long-ledger/record.ts`，用 pydantic 表达领域不变式：

- ``ContinuityFact``：语义事实，按 ``(domain, subject_id, field)`` 唯一，携带
  ``source_commit_id`` + ``evidence`` 证据链（可追溯 / 可回滚）。
- ``ContinuityKnowledge``：信息差——「谁（reader/character/faction）在什么程度上
  知道某个事实」，level 取 unknown/suspects/believes/knows/misled。
- ``ContinuityOpenLoop``：未闭环的剧情线（悬念 / 伏笔 / 线索），自抗状态机。
- ``ContinuityHandoff``：单章交接（summary + must_carry + next_chapter_constraints）。
- ``ContinuityLedger`` / ``ContinuityProj``：账本与有界的物化投影（写手章前输入）。

不变式通过 ``@model_validator`` 在边界强制（配合 core.base.validation 统一入口）：
- reader 知识必须用 audience_id="*"；character/faction 必须有实际 id。
- committed/受证据约束的条目必须携带非空 source_commit_id。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator, model_validator

FactDomain = Literal["character", "relationship", "world", "plot", "foreshadowing"]
KnowledgeAudience = Literal["reader", "character", "faction"]
KnowledgeLevel = Literal["unknown", "suspects", "believes", "knows", "misled"]
LoopKind = Literal["plot", "foreshadowing", "clue"]
LoopStatus = Literal["open", "progressing", "resolved", "abandoned"]


class ContinuityFact(BaseModel):
    """一条语义事实（证据链条目）。"""

    domain: FactDomain
    subject_id: str     # 人物/势力/地点/物品 id
    field: str          # 字段名，如 "status"/"alive"/"location"
    value: str
    source_commit_id: str   # 证据链锚（章节 commit id）
    evidence: str           # 原文/摘要证据

    @field_validator("subject_id", "field", "value", "source_commit_id")
    @classmethod
    def _nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("不可为空")
        return v.strip()


class ContinuityKnowledge(BaseModel):
    """信息差：某个主体对某事实的知情度。"""

    subject_id: str                 # 事实主体（引用 ContinuityFact.subject_id）
    audience: KnowledgeAudience
    audience_id: str                # reader 用 "*"；character/faction 用对应 id
    level: KnowledgeLevel
    source_commit_id: str

    @model_validator(mode="after")
    def _audience_ref(self) -> "ContinuityKnowledge":
        if self.audience == "reader" and self.audience_id != "*":
            raise ValueError("reader 知识必须用 audience_id='*'")
        if self.audience != "reader" and not self.audience_id.strip():
            raise ValueError("character/faction 知识必须提供 audience_id")
        if not self.source_commit_id.strip():
            raise ValueError("source_commit_id 不可为空")
        return self


class ContinuityOpenLoop(BaseModel):
    """未闭环的剧情线，自抗状态机。"""

    loop_id: str
    kind: LoopKind
    status: LoopStatus = "open"
    detail: str
    source_commit_id: str
    resolved_in: str | None = None   # 闭环位置（章节/事件）

    @field_validator("loop_id", "detail")
    @classmethod
    def _nonempty_basic(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("不可为空")
        return v.strip()


class ContinuityHandoff(BaseModel):
    """单章交接（替代「携上一章结尾」为有界交接）。"""

    chapter: int
    summary: str
    must_carry: list[str] = []
    next_chapter_constraints: list[str] = []
    open_loops: list[str] = []       # 引用 ContinuityOpenLoop.loop_id
    source_commit_id: str

    @model_validator(mode="after")
    def _valid(self) -> "ContinuityHandoff":
        if self.chapter <= 0:
            raise ValueError("chapter 必须为正整数")
        if not self.source_commit_id.strip():
            raise ValueError("source_commit_id 不可为空")
        return self


class ContinuityLedger(BaseModel):
    """账本：按章封存的语义增量 + 各集合的当前视图。

    facts 按 ``(domain, subject_id, field)`` 唯一（覆盖更新，不做字典重复）；
    knowledge / open_loops 各自以 id 唯一。
    """

    schema_version: int = 1
    facts: list[ContinuityFact] = []
    knowledge: list[ContinuityKnowledge] = []
    open_loops: list[ContinuityOpenLoop] = []
    handoffs: list[ContinuityHandoff] = []     # 按 chapter 有序（用于 latest_handoff）

    @model_validator(mode="after")
    def _uniqueness(self) -> "ContinuityLedger":
        seen = set()
        for f in self.facts:
            key = (f.domain, f.subject_id, f.field)
            if key in seen:
                raise ValueError(f"fact 重复: {key}")
            seen.add(key)
        seen_loop = set()
        for lo in self.open_loops:
            if lo.loop_id in seen_loop:
                raise ValueError(f"open_loop 重复: {lo.loop_id}")
            seen_loop.add(lo.loop_id)
        chapters = sorted(h.chapter for h in self.handoffs)
        if len(chapters) != len(set(chapters)):
            raise ValueError("handoff 章号重复")
        return self

    def latest_handoff(self) -> ContinuityHandoff | None:
        if not self.handoffs:
            return None
        return max(self.handoffs, key=lambda h: h.chapter)


class ContinuityProj(BaseModel):
    """物化投影（写手章前输入）：去重且**有界**的当前事实视图，不回溯全量原文。"""

    facts: list[ContinuityFact]
    knowledge: list[ContinuityKnowledge]
    open_loops: list[ContinuityOpenLoop]
    latest_handoff: ContinuityHandoff | None = None


__all__ = [
    "FactDomain",
    "KnowledgeAudience",
    "KnowledgeLevel",
    "LoopKind",
    "LoopStatus",
    "ContinuityFact",
    "ContinuityKnowledge",
    "ContinuityOpenLoop",
    "ContinuityHandoff",
    "ContinuityLedger",
    "ContinuityProj",
]