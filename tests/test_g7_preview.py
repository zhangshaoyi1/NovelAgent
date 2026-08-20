"""G7 show 章节预览命令测试（T7 验收，纯离线）

覆盖（对齐 PRD §8 / 设计 §8 T7）：
- 指定章 / 末章（--chapter 0）均能预览。
- --words 截断生效（含「…（共 N 字，预览前 W 字）」标注）；≤0 回退 300。
- 不存在的章 → `chapter_not_found` 错误信封 + 退出码 1；无章节 → `no_chapters` + 退出码 1。
- --json 信封 `{success, chapter, title, word_count, preview}` 完整。
- 非 JSON（rich）渲染章名 + 正文摘要。
- 正常章节零回归：书稿文件 mtime/内容不变（只读断言）。

零网络：show 全程只读文件，不触碰 LLM。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import typer

from agent.cli.commands.show import show


# ============================================================
# 辅助：造章节项目（frontmatter title + 正文）
# ============================================================
def _body(n: int) -> str:
    return "".join(f"第{n}章正文第{i}句。" for i in range(1, 100))


def _make_show_project(tmp_path: Path, n_chapters: int = 3) -> Path:
    ch = tmp_path / "chapters"
    ch.mkdir(parents=True, exist_ok=True)
    for n in range(1, n_chapters + 1):
        (ch / f"ch{n:03d}.md").write_text(
            f"---\ntitle: 第{n}回\n---\n{_body(n)}", encoding="utf-8"
        )
    return tmp_path


def _last_json(capsys) -> dict:
    captured = capsys.readouterr()
    lines = [l for l in captured.out.strip().split("\n") if l.strip()]
    return json.loads(lines[-1])


# ============================================================
# 1. 指定章 / 末章
# ============================================================
def test_show_specific_chapter_json(tmp_path: Path, capsys) -> None:
    d = _make_show_project(tmp_path)
    # words=2000 > 正文长度 → 不截断，preview 为全文正文
    show(project_dir=str(d), chapter=2, words=2000, json_output=True)
    env = _last_json(capsys)
    assert env["success"] is True
    assert env["chapter"] == 2
    assert env["title"] == "第2回"
    assert env["word_count"] == len(_body(2))
    assert env["preview"] == _body(2)


def test_show_last_chapter_default(tmp_path: Path, capsys) -> None:
    d = _make_show_project(tmp_path)
    show(project_dir=str(d), chapter=0, words=300, json_output=True)
    env = _last_json(capsys)
    assert env["success"] is True
    assert env["chapter"] == 3, "默认末章应为最后一个 ch*.md"
    assert env["title"] == "第3回"


# ============================================================
# 2. --words 截断
# ============================================================
def test_show_words_truncation(tmp_path: Path, capsys) -> None:
    d = _make_show_project(tmp_path)
    body = _body(1)
    show(project_dir=str(d), chapter=1, words=50, json_output=True)
    env = _last_json(capsys)
    assert env["preview"] == body[:50] + f"…（共 {len(body)} 字，预览前 50 字）"
    # word_count 不受 --words 影响（全文正文字符数）
    assert env["word_count"] == len(body)


def test_show_words_non_positive_falls_back_to_300(tmp_path: Path, capsys) -> None:
    d = _make_show_project(tmp_path)
    body = _body(1)
    show(project_dir=str(d), chapter=1, words=0, json_output=True)
    env = _last_json(capsys)
    assert env["preview"] == body[:300] + f"…（共 {len(body)} 字，预览前 300 字）"


# ============================================================
# 3. 错误信封 + 退出码
# ============================================================
def test_show_chapter_not_found(tmp_path: Path, capsys) -> None:
    d = _make_show_project(tmp_path)
    with pytest.raises(typer.Exit) as exc_info:
        show(project_dir=str(d), chapter=99, words=300, json_output=True)
    assert exc_info.value.exit_code == 1, "缺失章应退出码 1"
    env = _last_json(capsys)
    assert env["success"] is False
    assert env["error"]["code"] == "chapter_not_found"


def test_show_no_chapters(tmp_path: Path, capsys) -> None:
    with pytest.raises(typer.Exit) as exc_info:
        show(project_dir=str(tmp_path), chapter=0, words=300, json_output=True)
    assert exc_info.value.exit_code == 1, "无章节应退出码 1"
    env = _last_json(capsys)
    assert env["success"] is False
    assert env["error"]["code"] == "no_chapters"


# ============================================================
# 4. 非 JSON（rich）渲染
# ============================================================
def test_show_non_json_rich(tmp_path: Path, capsys) -> None:
    d = _make_show_project(tmp_path)
    show(project_dir=str(d), chapter=1, words=30, json_output=False)
    captured = capsys.readouterr()
    out = captured.out
    assert "第 1 章" in out
    assert "第1回" in out
    assert _body(1)[:30] in out
    assert "预览前 30 字" in out


# ============================================================
# 5. 只读断言：不修改书稿
# ============================================================
def test_show_does_not_modify_book(tmp_path: Path, capsys) -> None:
    d = _make_show_project(tmp_path)
    f = d / "chapters" / "ch002.md"
    before_content = f.read_text(encoding="utf-8")
    before_mtime = f.stat().st_mtime_ns
    show(project_dir=str(d), chapter=2, words=50, json_output=True)
    assert f.read_text(encoding="utf-8") == before_content, "show 不得修改书稿内容"
    assert f.stat().st_mtime_ns == before_mtime, "show 不得触碰书稿文件 mtime"
