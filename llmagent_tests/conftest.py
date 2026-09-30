"""llmagent_tests 分层自动打标（与 tests/conftest.py 的分层策略保持一致）"""

from __future__ import annotations

import os

# 与 tests/conftest.py 同理：在 gateway_adapter 模块级读 env 前关闭测试内重试退避
os.environ.setdefault("LLM_RETRY_BACKOFF_S", "0")

import pytest


def pytest_collection_modifyitems(config, items):
    for item in items:
        path = str(item.fspath).replace("\\", "/")
        if "/architecture/" in path:
            item.add_marker(pytest.mark.architecture)
        elif "test_m1" in path or "test_m2" in path or "test_m3" in path:
            item.add_marker(pytest.mark.milestone)
        elif "test_integration" in path:
            item.add_marker(pytest.mark.slow)
        else:
            item.add_marker(pytest.mark.fast)
