"""章节文件读取公共 helper（G6 新增，消除根因 B6-3 三处重复：去 frontmatter + ch*.md 排序）。

B4 黄金三章 / B6 防注水确定性指标 / 既有 gate_chapter·_gather_for_eval·_metric_pacing
全部复用本模块，禁止再出现第 4 份重复实现（共享知识 #4）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator, Literal

ChapterSide = Literal["first", "last"]


def strip_frontmatter(text: str) -> str:
    """去 frontmatter：以 '---' 开头则切掉首段（对齐 reader_appeal.py 行 420-423 语义）。"""
    if text.startswith("---"):
        return text.split("---", 2)[-1]
    return text


def list_chapter_files(project_dir: str | Path) -> list[Path]:
    """返回 chapters/ 下排序后的 ch*.md 文件列表（不存在返回 []）。"""
    chapters_dir = Path(project_dir) / "chapters"
    if not chapters_dir.exists():
        return []
    return sorted(chapters_dir.glob("ch*.md"))


def take_chapter_files(
    files: list[Path], *, side: ChapterSide = "last", n: int = 1
) -> list[Path]:
    """按侧取章节文件：side="last" 取末 n 个（对齐 gate_chapter 行 415 语义）；
    side="first" 取前 n 个（B4 黄金三章；为 P1 增强 B「写至第 3 章即时门禁」预留扩展点）。"""
    if not files:
        return []
    if side == "first":
        return files[: max(1, n)]
    return files[-max(1, n):]


def read_chapters_text(
    project_dir: str | Path, *, side: ChapterSide = "last", n: int = 1
) -> list[str]:
    """读章节正文列表（已去 frontmatter、strip）。单文件读失败跳过（不抛异常）。"""
    texts: list[str] = []
    for f in take_chapter_files(list_chapter_files(project_dir), side=side, n=n):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        texts.append(strip_frontmatter(text).strip())
    return texts


def iter_chapter_texts(project_dir: str | Path) -> Iterator[tuple[Path, str]]:
    """迭代全部章节 (文件, 去 frontmatter 正文)。供 B6 全书统计与既有 _metric_pacing 复用。"""
    for f in list_chapter_files(project_dir):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        yield f, strip_frontmatter(text).strip()
