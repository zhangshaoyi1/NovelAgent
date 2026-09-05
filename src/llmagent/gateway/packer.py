"""Packer：真实 token 计算 + 压缩 + 指纹（§5.8.4 / §5.13.12）"""

from __future__ import annotations

import hashlib
import json

from .models import ChatRequest, PackedRequest, RouteDecision


class Packer:
    """打包器：真实 token 计数 + 压缩 + 指纹"""

    def __init__(self) -> None:
        # M0：简单字符计数估算 token（~4 chars/token），后续替换为真实 tokenizer
        self._tokenizer_estimate = 4

    def pack(self, req: ChatRequest, route: RouteDecision) -> PackedRequest:
        """用 route 的目标 tokenizer 真实计数；

        超预算先压缩 compressible 项 → 仍超且含 required → 抛 BUDGET；
        落 context_fingerprint（内容寻址）。
        """
        # ① 估算 token 数
        total_chars = sum(len(m.get("content", "")) for m in req.messages)
        estimated_tokens = total_chars // self._tokenizer_estimate

        # ② 生成 context_fingerprint
        fingerprint = self._compute_fingerprint(req.messages, route)

        # ③ 预算检查（M0 简化：不做压缩，仅检测是否超预算）
        budget = route.budget
        if budget and budget.remaining_ratio < 0.05:
            from .models import ErrorClass

            raise BudgetError("预算不足，无法发起调用")

        return PackedRequest(
            messages=req.messages,
            estimated_input_tokens=estimated_tokens,
            context_fingerprint=fingerprint,
            route=route,
            temperature=req.hint.temperature,
            enable_thinking=(req.extra or {}).get("enable_thinking"),
        )

    @staticmethod
    def _compute_fingerprint(
        messages: list[dict[str, str]], route: RouteDecision
    ) -> str:
        """内容寻址指纹：hash(route.provider + route.model + 最后 3 条消息)"""
        raw = (
            route.provider
            + "::"
            + route.model
            + "::"
            + "|".join(
                json.dumps(m, sort_keys=True, ensure_ascii=False)
                for m in messages[-3:]
            )
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:32]


class BudgetError(Exception):
    """预算不足"""