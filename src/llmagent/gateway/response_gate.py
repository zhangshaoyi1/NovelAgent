"""ResponseGate：结构化输出 repair 链

M1 新增模块。当模型输出不符合预期格式时，自动修复。
"""

from __future__ import annotations

import json
import re
from typing import Any

from .models import ChatResponse


class ResponseGate:
    """响应门禁：结构化输出 repair 链

    M1 实现：
    - JSON 修复（提取花括号/方括号内容）
    - 去除多余的前缀/后缀
    - 尾部截断修复
    """

    def __init__(self) -> None:
        self._repair_count = 0

    @property
    def repair_count(self) -> int:
        return self._repair_count

    def admit(self, resp: ChatResponse, expected_format: str = "") -> ChatResponse:
        """对响应做 repair 链"""
        if resp.text:
            resp.text = self._repair_text(resp.text, expected_format)
        return resp

    def _repair_text(self, text: str, expected_format: str) -> str:
        """修复文本"""
        if expected_format == "json":
            return self._repair_json(text)
        return text

    @staticmethod
    def _repair_json(text: str) -> str:
        """修复 JSON 输出：提取花括号/方括号内容"""
        # 尝试直接解析
        text = text.strip()
        try:
            json.loads(text)
            return text
        except json.JSONDecodeError:
            pass

        # 提取花括号内容
        brace_match = re.search(r"\{.*\}", text, re.DOTALL)
        if brace_match:
            candidate = brace_match.group(0)
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                pass

        # 提取方括号内容
        bracket_match = re.search(r"\[.*\]", text, re.DOTALL)
        if bracket_match:
            candidate = bracket_match.group(0)
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                pass

        return text  # 无法修复，原样返回


class MetricsSink:
    """打点沉槽：成本/延迟/路由归因

    将 Gateway 调用的度量写入 Metrics/MetricRegistry。
    """

    def __init__(self) -> None:
        self._records: list[dict[str, Any]] = []

    def record(
        self,
        run_id: str,
        provider: str,
        model: str,
        strategy: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float,
        cost_cents: float = 0.0,
        success: bool = True,
        error: str = "",
    ) -> None:
        """记录一次调用度量"""
        record = {
            "run_id": run_id,
            "provider": provider,
            "model": model,
            "strategy": strategy,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "latency_ms": latency_ms,
            "cost_cents": cost_cents,
            "success": success,
            "error": error,
        }
        self._records.append(record)

    def get_records(self, run_id: str | None = None) -> list[dict[str, Any]]:
        if run_id:
            return [r for r in self._records if r["run_id"] == run_id]
        return list(self._records)

    def summary(self) -> dict[str, Any]:
        if not self._records:
            return {"total_calls": 0}
        total_calls = len(self._records)
        total_cost = sum(r["cost_cents"] for r in self._records)
        total_latency = sum(r["latency_ms"] for r in self._records)
        failed = sum(1 for r in self._records if not r["success"])
        return {
            "total_calls": total_calls,
            "total_cost_cents": round(total_cost, 4),
            "avg_latency_ms": round(total_latency / total_calls, 2) if total_calls else 0,
            "failed": failed,
            "success_rate": round((total_calls - failed) / total_calls * 100, 1) if total_calls else 0,
        }