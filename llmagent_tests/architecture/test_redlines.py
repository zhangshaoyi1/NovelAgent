"""架构测试：确保红线常量被引用、未被静默移除"""

import pytest

from llmagent.kernel.redlines import (
    BUDGET_HARD_STOP,
    COMPENSATION_FAIL_ACTION,
    MAX_AGENT_TURNS,
    MAX_REPLAN_DEPTH,
    MAX_RETRY_PER_TRACE,
    POLICY_ERROR_FALLBACK,
)


class TestRedlinesExist:
    """每条红线常量必须在模块中定义且被引用。"""

    def test_max_retry_exists(self):
        assert MAX_RETRY_PER_TRACE == 8

    def test_max_replan_exists(self):
        assert MAX_REPLAN_DEPTH == 3

    def test_compensation_fail_action_exists(self):
        assert COMPENSATION_FAIL_ACTION == "escalate_human"

    def test_budget_hard_stop_exists(self):
        assert BUDGET_HARD_STOP is True

    def test_policy_error_fallback_exists(self):
        assert POLICY_ERROR_FALLBACK == "NeverRetry"

    def test_max_agent_turns_exists(self):
        assert MAX_AGENT_TURNS == 25


class TestRedlinesUsed:
    """确保每个排除文件都引用了 LD 红线常量。"""

    def test_redlines_imported_by_guard(self):
        """RedLineGuard 必须 import redlines 模块。"""
        # 此处仅作架构契约占位，具体实现时补全
        pass