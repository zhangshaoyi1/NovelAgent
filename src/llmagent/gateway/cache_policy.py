"""缓存策略：Gateway 缓存裁决的唯一入口（HA-Eval L1）

背景（2026-09-08《五灵破归档》事故）
-----------------------------------
``SemanticCache._make_key`` 原先把指纹采样在 **messages 末 200 字符**，而
``ReaderAppealScorer.score()`` 的 user prompt 结构是「维度标签（**唯一差异，
在开头**）+ 8000 字正文（五维完全相同）」→ 五个评估维度算出同一个 key，
第一次真实调用后写入缓存，后四次全部命中，共用同一份 JSON → 五维取值恒等
（都是 3.0）→ 计数维（阈值 0）与 0-100 评分维（阈值 85）量纲串用 →
required 硬门禁必然不达标 → 误触发回滚重写，烧掉约 45.7 万 token 且质量净回退。

修复思路
--------
不是「把采样窗口调大」，而是给缓存加**语义分类**：

- 判定类（``JUDGMENT``，即评分/审查/一致性判断）**默认不进缓存**；
- 只有调用方显式声明为确定性转换（``DETERMINISTIC``）才允许复用；
- 创作类（``CREATIVE``）永不缓存。

这样即使将来有人又写出采样窗口错配的键算法，判定类也根本进不了缓存——
堵的是**一整类事故**，不是这一次表现。

依赖方向
--------
本模块属 ``llmagent.gateway``（基础设施层），**不得** import ``agent.*``。
业务层（``agent.client.gateway_adapter``）只通过 ``ChatRequest.extra["cache_class"]``
声明意图。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum

from .models import ChatRequest


class CacheClass(str, Enum):
    """LLM 调用的语义分类，决定其结果是否允许被复用。

    - ``DETERMINISTIC``：确定性转换（格式转换、确定性抽取、摘要），同输入必得同输出，可缓存。
    - ``JUDGMENT``：评分 / 判定 / 审查。同输入也可能因采样产生不同结论，**默认不缓存**。
    - ``CREATIVE``：创作。永不缓存。
    """

    DETERMINISTIC = "deterministic"
    JUDGMENT = "judgment"
    CREATIVE = "creative"


#: 缓存存活时间（秒）。仅对显式 opted-in 的 DETERMINISTIC 调用生效。
DEFAULT_TTL_S: float = 300.0

#: ``ChatRequest.extra`` 中声明语义分类的键名。
EXTRA_KEY = "cache_class"


@dataclass(frozen=True)
class CacheDecision:
    """缓存裁决结果（``enabled=False`` 时 ``key`` 为空串）。"""

    enabled: bool
    key: str = ""
    ttl_s: float = 0.0
    reason: str = ""


def decide(req: ChatRequest, *, ttl_s: float = DEFAULT_TTL_S) -> CacheDecision:
    """裁决一次请求是否允许读写缓存。

    裁决顺序（**默认拒绝**）：

    1. ``quality_critical`` 为真 → 不缓存（既有语义，保持不变）；
    2. 未显式声明 ``cache_class=DETERMINISTIC`` → 不缓存（本次新增的默认拒绝）；
    3. 其余 → 允许缓存，键由 :func:`_full_key` 生成。
    """
    if req.hint.quality_critical:
        return CacheDecision(False, reason="quality_critical")

    extra = req.extra or {}
    declared = str(extra.get(EXTRA_KEY, "") or "")
    if declared != CacheClass.DETERMINISTIC.value:
        label = declared or "unset"
        return CacheDecision(False, reason=f"not_opted_in(cache_class={label})")

    return CacheDecision(
        True, key=_full_key(req), ttl_s=ttl_s, reason="deterministic_opt_in"
    )


def _full_key(req: ChatRequest) -> str:
    """全量指纹：role + 完整 content + 模型/思考开关 + 采样参数。

    与旧实现（``末 200 字符 + complexity``）的关键差异：**完整 content 进哈希**。
    使「差异在 prompt 开头」的请求必然算出不同 key，从根上消除采样窗口错配。
    """
    h = hashlib.sha256()
    messages = req.messages or []
    # 先写条数，避免 [a][bc] 与 [ab][c] 拼接出相同字节流
    h.update(f"n={len(messages)}".encode("utf-8"))
    for m in messages:
        h.update(str(m.get("role", "")).encode("utf-8", "replace"))
        h.update(b"\x00")
        h.update(str(m.get("content", "")).encode("utf-8", "replace"))
        h.update(b"\x1f")
    extra = req.extra or {}
    for field in ("model", "enable_thinking"):
        h.update(f"{field}={extra.get(field, '')}".encode("utf-8", "replace"))
        h.update(b"\x00")
    h.update(f"T={req.hint.temperature}".encode("utf-8"))
    h.update(f"MT={req.hint.max_tokens}".encode("utf-8"))
    h.update(f"C={req.hint.complexity.value}".encode("utf-8"))
    return h.hexdigest()
