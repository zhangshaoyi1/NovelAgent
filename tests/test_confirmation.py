"""T-4 工作流解耦：check_confirmed 上提 core/confirmation 验收

覆盖：
- 未确认（无 architecture.md）→ False
- architecture.md 写入 confirmed:true → True
- 防回归：m3/m4/m5/m6 源文件不再直接 import m14_architecture（已改依赖 core.confirmation）
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent.core.quality.guardrails import is_architecture_confirmed

# 不应再直接 import m14_architecture 的工作流（T-4 解耦目标）
# 注意：agent 包实际位于 <repo_root>/src/agent（pytest pythonpath=["src"]），
# 故相对路径需加 src/ 前缀，避免双写 agent 目录导致 FileNotFoundError。
_DECOUPLED_WORKFLOWS = [
    "src/agent/workflows/m3_outline.py",
    "src/agent/workflows/m4_character.py",
    "src/agent/workflows/m5_write_chapter.py",
    "src/agent/workflows/m6_adjust.py",
]


def _write_architecture(tmp_path: Path, confirmed: bool) -> None:
    """写入一份合法的 architecture.md（frontmatter 平铺 confirmed 字段）"""
    arch = tmp_path / "architecture.md"
    arch.write_text(
        f"---\nconfirmed: {str(confirmed).lower()}\nversion: 1\n---\n",
        encoding="utf-8",
    )


def test_unconfirmed_when_no_architecture_file(tmp_path: Path) -> None:
    assert is_architecture_confirmed(tmp_path) is False


def test_unconfirmed_when_confirmed_false(tmp_path: Path) -> None:
    _write_architecture(tmp_path, False)
    assert is_architecture_confirmed(tmp_path) is False


def test_confirmed_when_true(tmp_path: Path) -> None:
    _write_architecture(tmp_path, True)
    assert is_architecture_confirmed(tmp_path) is True


def test_no_direct_m14_import_in_decoupled_workflows() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    for rel in _DECOUPLED_WORKFLOWS:
        src = repo_root / rel
        text = src.read_text(encoding="utf-8")
        assert "from agent.workflows.m14_architecture import" not in text, (
            f"{rel} 仍直接 import m14_architecture，未解耦到 core.confirmation"
        )
        # 应改为引用 core.quality.guardrails.is_architecture_confirmed
        assert "agent.core.quality.guardrails import is_architecture_confirmed" in text, (
            f"{rel} 未改用 agent.core.quality.guardrails.is_architecture_confirmed"
        )
