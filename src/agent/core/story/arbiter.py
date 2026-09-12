"""LLM 裁决者接线（长线一致性设计稿第二期·ChangeGate 语义裁决）

``ChangeGate`` 的 ``arbiter`` 注入位的生产实现：把提案（性格/关系变更等）
交给 LLM 判断是否与既有事实自洽。裁决纪律（§7.0 否决权分级写进 prompt）：

- 一致性矛盾（与账本事实/事件链冲突）→ ``rejected``，必须给出具体矛盾点；
- 创作合理性存疑 → ``approved_with_warnings``（附理由建议，不得否决）；
- 自洽且合理 → ``approved``。

裁决失败由 Gate 统一显性降级（``approved_with_warnings`` + degrade 留痕），
本模块不吞异常。``chat_fn`` 可注入（离线测试）。
"""

from __future__ import annotations

from typing import Any, Callable

from pydantic import BaseModel, Field

from agent.core.story.change_gate import ProposedChange


class ArbiterOutput(BaseModel):
    """裁决结构化输出。"""

    verdict: str = Field(description="approved | approved_with_warnings | rejected")
    reason: str = Field(default="", description="裁决理由（驳回时必须具体指出矛盾点）")
    warnings: list[str] = Field(default_factory=list, description="附意见（不否决，但留痕）")


ArbiterChatFn = Callable[[list[dict[str, str]]], dict[str, Any]]

_SYSTEM = (
    "你是小说连续性裁决者，对叙事变更提案做终审。裁决纪律（必须严格遵守）：\n"
    "1. 提案与既有事实/账本事件链存在矛盾 → verdict=rejected，reason 必须具体指出"
    "矛盾在哪条事实/哪次事件上；\n"
    "2. 提案自洽但你认为转变突兀、缺少铺垫等创作层面疑虑 → "
    "verdict=approved_with_warnings，warnings 写明建议；无权否决创作判断；\n"
    "3. 自洽且合理 → verdict=approved。\n"
    "输出 JSON：verdict / reason / warnings。"
)


def _default_chat(llm) -> ArbiterChatFn:
    from agent.client.gateway_adapter import chat_structured

    def chat(messages: list[dict[str, str]]) -> dict[str, Any]:
        return chat_structured(
            llm,
            messages,
            ArbiterOutput,
            use="utility",
            temperature=0.3,
            max_tokens=1024,
            enable_thinking=False,
        )

    return chat


def make_llm_arbiter(llm_client=None, chat_fn: ArbiterChatFn | None = None) -> ArbiterChatFn:
    """构造裁决函数（``chat_fn`` 可注入离线测试；缺省惰性创建 Gateway）。"""
    if chat_fn is not None:
        return chat_fn
    if llm_client is None:
        from agent.client.gateway_adapter import create_gateway

        llm_client = create_gateway()
    return _default_chat(llm_client)


def render_arbiter_messages(change: ProposedChange) -> list[dict[str, str]]:
    """组装裁决 prompt（与裁决纪律同发，便于审计与测试断言）。"""
    user = (
        f"【变更类别】{change.kind}\n"
        f"【变更主体】{change.subject}\n"
        f"【变更前】{change.before}\n"
        f"【变更后】{change.after}\n"
        f"【提出方理由】{change.reason}\n"
        f"【证据】{change.evidence or '（未提供）'}\n"
        f"【章节】第 {change.chapter} 章\n\n"
        "请依据裁决纪律给出 verdict / reason / warnings。"
    )
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": user},
    ]


def llm_arbiter(change: ProposedChange) -> dict[str, Any]:
    """默认裁决入口（惰性创建 Gateway；供批间反思层直接引用）。"""
    return make_llm_arbiter()(render_arbiter_messages(change))


__all__ = [
    "ArbiterOutput",
    "make_llm_arbiter",
    "render_arbiter_messages",
    "llm_arbiter",
]
