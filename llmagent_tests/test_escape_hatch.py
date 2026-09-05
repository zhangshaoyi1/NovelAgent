"""escape_hatch（逃生舱）单元测试：T3 落地验收"""

from __future__ import annotations

import pytest

from llmagent.escape_hatch import (
    ESCAPE_HATCH_LIMIT,
    declare,
    escape_hatch,
    declared as _declared_map,
    reset,
    usage_log,
    within_limit,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    reset()
    yield
    reset()


class TestDeclare:
    def test_declare_and_query(self):
        declare("mod_a", "理由 A")
        assert _declared_map() == {"mod_a": "理由 A"}
        assert within_limit() is True

    def test_redeclare_updates_reason(self):
        declare("mod_a", "旧理由")
        declare("mod_a", "新理由")
        assert _declared_map() == {"mod_a": "新理由"}

    def test_limit_enforced(self):
        for i in range(ESCAPE_HATCH_LIMIT):
            declare(f"mod_{i}", f"理由 {i}")
        with pytest.raises(RuntimeError, match="上限"):
            declare("mod_over", "超额")


class TestEscapeHatchScope:
    def test_undeclared_module_raises(self):
        with pytest.raises(RuntimeError, match="未登记"):
            with escape_hatch("nope", "原因"):
                pass

    def test_usage_logged(self):
        declare("mod_a", "理由 A")
        with escape_hatch("mod_a", "本次绕过原因"):
            pass
        log = usage_log()
        assert len(log) == 1
        assert log[0] == {"module": "mod_a", "reason": "本次绕过原因"}

    def test_exception_still_logs(self):
        declare("mod_a", "理由 A")
        with pytest.raises(ValueError):
            with escape_hatch("mod_a", "原因"):
                raise ValueError("boom")
        assert len(usage_log()) == 1
