"""import 守卫（运行期 R1 红线）测试"""

from __future__ import annotations

import sys
from pathlib import Path

from llmagent.kernel.import_guard import _norm, violates


class TestViolates:
    def test_business_module_importing_sdk_is_violation(self, tmp_path: Path):
        src = _norm(str(Path(__file__).resolve().parents[2] / "src"))
        msg = violates(str(src + "/agent/workflows/writing/m5_write_chapter.py"), "openai")
        assert msg is not None and "R1" in msg

    def test_client_and_gateway_allowed(self):
        src = _norm(str(Path(__file__).resolve().parents[2] / "src"))
        assert violates(str(src + "/agent/client/provider.py"), "openai") is None
        assert violates(str(src + "/llmagent/gateway/providers/registry.py"), "openai") is None

    def test_non_sdk_module_allowed(self):
        src = _norm(str(Path(__file__).resolve().parents[2] / "src"))
        assert violates(str(src + "/agent/workflows/m1.py"), "json") is None
        assert violates(str(src + "/agent/workflows/m1.py"), "pydantic") is None

    def test_outside_src_tree_allowed(self, tmp_path: Path):
        # 源码树之外（site-packages / 用户脚本）不受守卫影响
        assert violates(str(tmp_path / "somewhere.py"), "openai") is None

    def test_empty_caller_allowed(self):
        assert violates("", "openai") is None


class TestInstallGuard:
    def test_install_is_idempotent(self, monkeypatch):
        from llmagent.kernel import import_guard

        monkeypatch.setattr(import_guard, "_installed", False)
        monkeypatch.setenv("LLMAGENT_IMPORT_GUARD", "1")
        hooks_before = sys.getaudit_hooks() if hasattr(sys, "getaudit_hooks") else []
        assert import_guard.install_import_guard() is True
        assert import_guard.install_import_guard() is False  # 幂等
        if hasattr(sys, "getaudithooks") or hasattr(sys, "getaudit_hooks"):
            # Python 3.12+ 提供枚举；旧版本无法枚举，仅验证不重复安装
            pass
        _ = hooks_before
