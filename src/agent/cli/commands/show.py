"""show 命令 —— 章节预览（G7，拍板 3）

对指定章节（默认末章）做**只读**预览：去 frontmatter、按 ``--words`` 截断正文，
rich 渲染章名 + 摘要；``--json`` 输出 ``{success, chapter, title, word_count, preview}``。
全程只 ``read_text``，**不修改书稿、不改状态机**（拍板 3「不修改书稿」）。

用法：
    novel-agent show -d projects/my-novel              # 默认末章，前 300 字
    novel-agent show -d projects/my-novel -c 7 -w 100  # 指定第 7 章，前 100 字
    novel-agent show -d projects/my-novel --json       # JSON 信封
"""

from __future__ import annotations

from pathlib import Path

import frontmatter

from agent.cli._app import app, command, console, typer
from agent.cli._shared import emit_result, enforce_gate
from agent.core.chapters import list_chapter_files, strip_frontmatter
from agent.core.state_machine import State


@command(allowed_states=(State.WRITING, State.PAUSED, State.COMPLETED))
def show(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    chapter: int = typer.Option(
        0, "--chapter", "-c", help="章节号（1-based chNNN.md）；默认末章"
    ),
    words: int = typer.Option(
        300, "--words", "-w", help="预览字数截断（默认 300，≤0 视为 300）"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="以 JSON 形式输出预览到 stdout"
    ),
) -> None:
    """章节预览 - 只读查看指定章/末章开头（默认 300 字截断）

    复用公共章节读取 helper（chapters.py）；全程只读、不修改书稿。
    """
    project_path = Path(project_dir)
    enforce_gate(str(project_path), "show", json_mode=json_output)

    files = list_chapter_files(project_path)
    if not files:
        _fail(json_output, "no_chapters", f"{project_path}/chapters 下无章节文件")
        raise typer.Exit(code=1)

    if chapter > 0:
        ch_file = project_path / "chapters" / f"ch{chapter:03d}.md"
        if not ch_file.exists():
            _fail(json_output, "chapter_not_found", f"{ch_file} 不存在")
            raise typer.Exit(code=1)
        chosen = chapter
    else:
        ch_file = files[-1]
        chosen = int(ch_file.stem.replace("ch", ""))

    try:
        raw = ch_file.read_text(encoding="utf-8")
    except OSError as e:
        _fail(json_output, "chapter_read_failed", f"读取章节失败：{e}")
        raise typer.Exit(code=1)

    body = strip_frontmatter(raw).strip()
    title = _extract_title(raw)
    total_chars = len(body)
    w = words if words and words > 0 else 300
    preview = body[:w]
    truncated = len(body) > w
    if truncated:
        preview += f"…（共 {total_chars} 字，预览前 {w} 字）"

    if json_output:
        emit_result(
            {
                "success": True,
                "chapter": chosen,
                "title": title,
                "word_count": total_chars,
                "preview": preview,
            },
            json_mode=True,
        )
        return

    console.print(
        f"[bold cyan]第 {chosen} 章" + (f" · {title}" if title else "") + "[/bold cyan]"
    )
    console.print(preview)
    if truncated:
        console.print(f"[dim]…（共 {total_chars} 字，预览前 {w} 字）[/dim]")


def _extract_title(raw: str) -> str:
    """从 frontmatter 提取 title；解析失败回退空串（不阻断）。"""
    try:
        if raw.startswith("---"):
            post = frontmatter.loads(raw)
            return str(post.metadata.get("title", "") or "")
    except Exception:  # noqa: BLE001 - 解析失败回退空串
        pass
    return ""


def _fail(json_output: bool, code: str, message: str) -> None:
    """错误信封（JSON error 信封 / 非 JSON 红色提示），与 appeal.py _fail 语义一致。"""
    if json_output:
        emit_result(
            {"success": False, "error": {"code": code, "message": message}},
            json_mode=True,
        )
    else:
        console.print(f"[bold red]✗[/bold red] {message}")
