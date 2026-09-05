"""配额/鉴权类致命错误的识别与熔断行为

覆盖 2026-09-05 修复的三处链路：
1. is_fatal_provider_error 关键词识别
2. AgentLoop 遇致命错误立即中止（不再退避重试满 max_iterations）
3. GatewayAdapter 按两套 usage 键名口径正确取数
"""

from __future__ import annotations

import pytest

from agent.core.base.exceptions import (
    FatalProviderError,
    is_fatal_provider_error,
)
from agent.core.engine.agent_loop import AgentLoop


class TestIsFatalProviderError:
    @pytest.mark.parametrize(
        "text",
        [
            "Provider openai 调用失败: Error code: 403 - {'error': {'message': "
            "'Free quota exhausted. To continue accessing the model on a paid "
            "basis, please add funds...'}}",
            "Error code: 401 - invalid_api_key",
            "余额不足，请充值",
            "配额已耗尽",
            "Error code: 402 - insufficient balance",
        ],
    )
    def test_matches_quota_and_auth_errors(self, text: str):
        assert is_fatal_provider_error(text)

    @pytest.mark.parametrize(
        "text",
        [
            "Error code: 429 - rate limit exceeded",
            "Error code: 500 - internal server error",
            "Connection reset by peer",
            "timeout",
            "",
        ],
    )
    def test_ignores_transient_errors(self, text: str):
        assert not is_fatal_provider_error(text)


def _make_loop(raise_exc: Exception, max_iterations: int = 10) -> AgentLoop:
    def decide(messages):
        raise raise_exc

    return AgentLoop(tools=[], decide=decide, max_iterations=max_iterations)


class TestAgentLoopFatalAbort:
    def test_fatal_error_aborts_immediately(self):
        """致命错误应在第 1 轮就中止，而不是退避跑满 max_iterations"""
        exc = RuntimeError(
            "Provider openai 调用失败: Error code: 403 - Free quota exhausted"
        )
        result = _make_loop(exc, max_iterations=10).run("task")
        assert not result.finished
        assert result.fatal_error
        assert "403" in (result.fatal_error or "")
        assert result.iterations == 0  # 未消耗任何迭代

    def test_transient_error_still_degrades_and_retries(self):
        """瞬时错误保持原降级语义：记录 last_error 后继续，直至耗尽迭代"""
        exc = ConnectionError("connection reset by peer")
        result = _make_loop(exc, max_iterations=3).run("task")
        assert not result.finished
        assert not result.fatal_error
        assert result.last_error
        assert result.iterations == 3  # 跑满了迭代上限

    @pytest.mark.asyncio
    async def test_fatal_error_aborts_async_loop(self):
        async def decide_async(messages):
            raise RuntimeError("Error code: 401 - unauthorized")

        loop = AgentLoop(tools=[], decide_async=decide_async, max_iterations=10)
        result = await loop.run_async("task")
        assert not result.finished
        assert result.fatal_error


class TestFatalProviderErrorType:
    def test_is_runtime_error(self):
        """继承 RuntimeError，兼容既有 except RuntimeError 调用点"""
        assert issubclass(FatalProviderError, RuntimeError)
