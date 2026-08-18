"""appeal 命令 —— B1 迷爱看评分（读者吸引力）

对指定章节（或自定义文本）跑真 LLM「迷爱看」6 维评分：钩子强度 / 爽点密度 / 代入感 /
人物弧光 / 世界观新颖度 / 情绪曲线。直接回答「读者会不会爱看」。

用法：
    novel-agent appeal -d <dir> --chapter 12
    novel-agent appeal -d <dir> --chapter 12 --json
    novel-agent appeal -d <dir> --file my_chapter.md

LLM 不可用（未配置 .env）时给出占位报告，不报错中断。
"""

from __future__ import annotations

import os
from pathlib import Path

import frontmatter
from agent.cli._app import app, command, console, typer
from agent.cli._shared import emit_result, make_quiet_console
from agent.core.state_machine import State


@command(allowed_states=(State.WRITING, State.PAUSED, State.COMPLETED))
def appeal(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    chapter: int = typer.Option(
        0, "--chapter", "-c", help="要评分的章节号（1-based）；与 --file 二选一"
    ),
    file: str = typer.Option(
        "", "--file", "-f", help="直接评分一个外部 .md 文件（忽略 --chapter）"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="以 JSON 形式输出评分到 stdout"
    ),
    env_file: str = typer.Option(
        None, "--env", help="指定 .env 文件（透传下游 LLMClient）"
    ),
) -> None:
    """迷爱看评分 - 真 LLM 读者吸引力 6 维（钩子/爽点/代入感/弧光/新颖度/情绪曲线）

    把"不崩"升级为"迷爱看"：直接告诉作者这一章读者会不会爱看、短板在哪、怎么改。
    """
    if env_file:
        os.environ["NOVEL_AGENT_DOTENV"] = env_file

    project_path = Path(project_dir)

    # 取待评文本
    title = ""
    genre = ""
    synopsis = ""
    if file:
        text_path = Path(file)
        if not text_path.exists():
            _fail(json_output, "file_not_found", f"{text_path} 不存在")
            raise typer.Exit(code=1)
        raw = text_path.read_text(encoding="utf-8")
        if raw.startswith("---"):
            post = frontmatter.loads(raw)
            text = post.content
            title = post.metadata.get("title", "")
        else:
            text = raw
    else:
        if chapter <= 0:
            _fail(json_output, "bad_args", "需要 --chapter 或 --file")
            raise typer.Exit(code=2)
        ch_file = project_path / "chapters" / f"ch{chapter:03d}.md"
        if not ch_file.exists():
            _fail(json_output, "chapter_not_found", f"{ch_file} 不存在")
            raise typer.Exit(code=1)
        post = frontmatter.load(ch_file)
        text = post.content
        title = post.metadata.get("title", "")
        # 题材/简介（仅作上下文，缺失不阻断）
        world = project_path / "world.md"
        if world.exists():
            wc = world.read_text(encoding="utf-8")
            genre = _extract(wc, "genre") or ""
            idx = wc.find("## 故事简介")
            if idx >= 0:
                synopsis = wc[idx: idx + 300]

    from agent.core.llm_client import LLMClient
    from agent.core.reader_appeal import ReaderAppealScorer

    workflow_console = make_quiet_console() if json_output else console
    scorer = ReaderAppealScorer(llm_client=LLMClient(), console=workflow_console)
    report = scorer.score_chapter(
        text, title=str(title), genre=str(genre), synopsis=str(synopsis)
    )

    if json_output:
        emit_result({"success": True, "report": report.to_dict()}, json_mode=True)
        return
    console.print(report.to_markdown())


def _fail(json_output: bool, code: str, message: str) -> None:
    if json_output:
        emit_result({"success": False, "error": {"code": code, "message": message}},
                    json_mode=True)
    else:
        console.print(f"[bold red]✗[/bold red] {message}")


def _extract(content: str, field_name: str) -> str:
    import re

    m = re.search(rf"\*\*{re.escape(field_name)}\*\*[：:]\s*(.+)", content)
    return m.group(1).strip() if m else ""
