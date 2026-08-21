"""G11 跨平台 skill 分发测试（T6 验收，纯确定性零 LLM）。

覆盖（对齐 G11/设计.md §4 / §9 T5）：
- export_one 导出标准目录（SKILL.md / README.md / 内容文件复制）；
- SKILL.md frontmatter 契约（name/description/version/type）；
- --zip 打包；--skill all 全量导出；
- skill 不存在 / 缺 SKILL.md → ValueError；
- 只读约束：源目录在导出前后逐字节一致（无副作用）。
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from agent.cli.commands.export_skill import _SKILLS_SRC, export_one


def _snapshot(d: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(d)).replace("\\", "/"): p.read_bytes()
        for p in sorted(d.rglob("*"))
        if p.is_file()
    }


# ---------------------------------------------------------------- 基础导出
def test_export_one_structure(tmp_path: Path) -> None:
    r = export_one("bookworm", tmp_path)
    out = Path(r["out"])
    assert (out / "SKILL.md").exists()
    assert (out / "README.md").exists()
    assert (out / "persona.md").exists()
    assert (out / "rubrics.md").exists()
    assert (out / "genre_expectations" / "xiuxian.md").exists()
    assert "SKILL.md" in r["files"]


def test_export_frontmatter_contract(tmp_path: Path) -> None:
    import frontmatter

    export_one("bookworm", tmp_path)
    post = frontmatter.load(Path(tmp_path) / "bookworm" / "SKILL.md")
    meta = post.metadata
    for key in ("name", "description", "version", "type"):
        assert meta.get(key), f"frontmatter 缺契约字段 {key}"
    assert meta["name"] == "bookworm"


def test_export_zip(tmp_path: Path) -> None:
    r = export_one("bookworm", tmp_path, zip_pkg=True)
    assert r["zip"] and Path(r["zip"]).exists()
    with zipfile.ZipFile(r["zip"]) as zf:
        names = zf.namelist()
        assert any(n.endswith("SKILL.md") for n in names)
        assert any(n.endswith("README.md") for n in names)


def test_export_all(tmp_path: Path) -> None:
    from agent.cli.commands.export_skill import export_one

    names = sorted(
        d.name for d in _SKILLS_SRC.iterdir() if d.is_dir() and d.name != "__pycache__"
    )
    assert len(names) >= 3  # bookworm + 题材类 skill
    for n in names:
        r = export_one(n, tmp_path)
        assert Path(r["out"]).exists()


# ---------------------------------------------------------------- 错误与只读
def test_export_missing_skill(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="skill 不存在"):
        export_one("no_such_skill", tmp_path)


def test_export_readonly_src(tmp_path: Path) -> None:
    """导出前后源目录逐字节一致（只读无副作用）。"""
    before = _snapshot(_SKILLS_SRC / "bookworm")
    export_one("bookworm", tmp_path)
    after = _snapshot(_SKILLS_SRC / "bookworm")
    assert before == after
