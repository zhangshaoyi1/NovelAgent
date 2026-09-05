"""KV 缓存感知的上下文段排序（竞品差距改进计划 P0-1）。

对齐竞品实践（AI-Novel-Writer generate-draft）：把 prompt 中的各上下文段按
**稳定性分层**重排为 stable → semi → volatile，使跨章/跨次生成时 provider 端的
prompt cache 能命中尽可能长的稳定前缀，降本提速。只改段序、不改任何段内容；
段内文本与分隔符均须确定性（禁止时间戳/随机数进入段文本）。

分层约定：
- ``stable``   全书级稳定：系统规则、文风指引、冻结设定（境界体系/金手指）。
- ``semi``     卷/支线级缓变：角色硬约束、项目写法记忆、路线节点。
- ``volatile`` 章级变化：账本投影、前情提要、RAG 召回、爽点剧本、运行时提醒。

用户消息模板（prompts/m5/generate.md 的 ``# user`` 段）的段落顺序同样遵循
stable → semi → volatile，与本模块共同构成完整的缓存前缀链路。
"""

from __future__ import annotations

from dataclasses import dataclass

STABLE = "stable"
SEMI = "semi"
VOLATILE = "volatile"

_TIER_ORDER = {STABLE: 0, SEMI: 1, VOLATILE: 2}

SEPARATOR = "\n\n"


@dataclass(frozen=True)
class PromptSection:
    """一段待拼入 prompt 的上下文。

    key 仅用于调试/测试定位；text 为实际渲染内容（空串段会被丢弃）；
    stability 必须是 STABLE/SEMI/VOLATILE 之一。
    """

    key: str
    text: str
    stability: str = VOLATILE


def order_sections(sections: list[PromptSection]) -> str:
    """按 stable → semi → volatile 稳定排序拼接段落。

    同层内保持插入顺序（稳定排序，结果确定性）；空段跳过。
    """
    tiers = [(s, _TIER_ORDER[s.stability]) for s in sections if s.text]
    tiers.sort(key=lambda pair: pair[1])  # 稳定排序：同层保序
    return SEPARATOR.join(s.text for s, _ in tiers)
