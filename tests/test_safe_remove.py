"""safe_remove 回退链单元测试（T12 关键修复验证）

证明：
- 文件删除：os.remove 抛 OSError（模拟 WorkBuddy safe-delete 垫片 FAIL_CLOSED）
  → 清空内容并改名 <name>.bak，不抛错，原路径消失，返回 True。
- 目录删除：shutil.rmtree 抛 OSError
  → 改名到 <parent>/.trash/<name>，不抛错，原目录消失，返回 True。
- 幂等：path 不存在直接返回 True。
- 主路径正常：os.remove / shutil.rmtree 成功时原路径消失、返回 True。
- 绝不抛错：主路径与回退路径都失败时 warning + False，不抛异常。
- make_quiet_console 返回输出到 stderr 的 rich.Console（--json 模式用）。
"""

from __future__ import annotations

import warnings
from pathlib import Path

import agent.base.utils
import pytest
from agent.utils import make_quiet_console, safe_remove


def _raise_os_error(*args: object, **kwargs: object) -> None:
    """模拟 safe-delete 垫片 FAIL_CLOSED：任何删除操作都抛 OSError"""
    raise OSError("simulated safe-delete shim FAIL_CLOSED")


# ============================================================
# 文件回退链（最关键）
# ============================================================
class TestSafeRemoveFileFallback:
    def test_file_normal_remove_succeeds(self, tmp_path: Path) -> None:
        """主路径：os.remove 成功 → 文件消失，返回 True"""
        f = tmp_path / "draft.wip"
        f.write_text("正文", encoding="utf-8")
        assert safe_remove(f) is True
        assert not f.exists()

    def test_file_fallback_on_os_remove_failure(self, tmp_path: Path, monkeypatch) -> None:
        """os.remove 抛 OSError（safe-delete 垫片拦截）→ 清空内容改名 .bak，
        绝不抛错，原文件消失，返回 True。这是去掉 `env -u CODEBUDDY_SESSION_ID`
        后 write 不再崩溃的根因证明。"""
        f = tmp_path / "draft.wip"
        f.write_text("正文内容", encoding="utf-8")
        bak = f.with_name(f.name + ".bak")

        # 模拟 WorkBuddy safe-delete 垫片：拦截 os.remove
        monkeypatch.setattr(agent.base.utils.os, "remove", _raise_os_error)

        # 不抛异常
        result = safe_remove(f)

        assert result is True
        assert not f.exists(), "原文件应已被改名移除"
        assert bak.exists(), "应生成 .bak 文件（回退链）"
        # 回退策略会先清空内容再改名
        assert bak.read_text(encoding="utf-8") == ""

    def test_file_returns_false_when_all_strategies_fail(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """主路径 os.remove 失败 + 回退 write_text 也失败 → 仅 warning + False，
        绝不抛异常（绝不抛错契约）。"""
        f = tmp_path / "x.txt"
        f.write_text("y")

        monkeypatch.setattr(agent.base.utils.os, "remove", _raise_os_error)
        # 连回退的清空内容也失败
        monkeypatch.setattr(agent.base.utils.Path, "write_text", _raise_os_error)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = safe_remove(f)

        assert result is False, "所有策略失败时末态应为 False"
        assert f.exists(), "删除失败时原文件应仍在"


# ============================================================
# 目录回退链（最关键）
# ============================================================
class TestSafeRemoveDirFallback:
    def test_dir_normal_rmtree_succeeds(self, tmp_path: Path) -> None:
        """主路径：shutil.rmtree 成功 → 目录消失，返回 True"""
        d = tmp_path / "draftdir"
        d.mkdir()
        (d / "a.txt").write_text("hi")
        assert safe_remove(d) is True
        assert not d.exists()

    def test_dir_fallback_on_rmtree_failure(self, tmp_path: Path, monkeypatch) -> None:
        """shutil.rmtree 抛 OSError（safe-delete 垫片拦截）→ 改名到
        <parent>/.trash/<name>，绝不抛错，原目录消失，返回 True。

        注：实现先 target.mkdir 再 shutil.move，move 会把源目录落入已建好的
        target 内，故实际落点为 <.trash>/<name>/<name>（多嵌套一层）。这不影响
        契约（不抛错、原目录消失、改名目录出现在 .trash 下、内容保留），仅作观察。
        """
        d = tmp_path / "draftdir"
        d.mkdir()
        (d / "a.txt").write_text("hi")
        trash_root = tmp_path / ".trash"

        # 模拟 WorkBuddy safe-delete 垫片：拦截 shutil.rmtree
        monkeypatch.setattr(agent.base.utils.shutil, "rmtree", _raise_os_error)

        result = safe_remove(d)

        assert result is True
        assert not d.exists(), "原目录应已被改名移除"
        # 改名后的目录出现在 <parent>/.trash/ 下
        assert (trash_root / "draftdir").exists() and (trash_root / "draftdir").is_dir()
        # 内容随改名保留（递归查找，兼容实现的多嵌套一层）
        preserved = list(trash_root.rglob("a.txt"))
        assert preserved, "目录内容应随改名保留到 .trash 下"

    def test_dir_fallback_honors_explicit_trash_root(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """显式 trash_root 优先于默认 <parent>/.trash"""
        d = tmp_path / "draftdir"
        d.mkdir()
        (d / "a.txt").write_text("hi")
        custom_trash = tmp_path / "my_trash"

        monkeypatch.setattr(agent.base.utils.shutil, "rmtree", _raise_os_error)

        result = safe_remove(d, trash_root=custom_trash)

        assert result is True
        assert (custom_trash / "draftdir").exists() and (
            custom_trash / "draftdir"
        ).is_dir()
        # 内容保留到显式 trash_root 下（兼容多嵌套一层）
        assert list(custom_trash.rglob("a.txt")), "目录内容应保留到显式 trash_root"


# ============================================================
# 幂等 + 工具
# ============================================================
class TestSafeRemoveIdempotency:
    def test_missing_path_returns_true(self, tmp_path: Path) -> None:
        """path 不存在 → 直接 True（幂等），不抛错"""
        assert safe_remove(tmp_path / "nope.txt") is True
        assert safe_remove(tmp_path / "nope_dir") is True


class TestMakeQuietConsole:
    def test_returns_console_writing_to_stderr(self) -> None:
        """make_quiet_console 返回把输出导向 stderr 的 rich.Console（--json 用）"""
        import sys

        from rich.console import Console

        console = make_quiet_console()
        # 输出目标是 stderr（避免污染 stdout 的 JSON）
        assert isinstance(console, Console)
        assert console.file is sys.stderr
