from __future__ import annotations

import os
import re
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from agent.cli._app import app, console, typer, command
from agent.cli._shared import emit_result
from agent.core.learning_store import Learning, LearningStore
from agent.core.llm_client import LLMClient
from agent.workflows.m17_learn import LearningMiner


def _resolve_chapter_nums(range_: Optional[str]) -> list[int]:
    """解析 --range 'A-B' 为闭区间章节号列表"""
    if not range_:
        return []
    m = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", range_)
    if not m:
        raise ValueError(f"--range 格式应为 'A-B'，收到：{range_!r}")
    lo, hi = int(m.group(1)), int(m.group(2))
    if lo > hi:
        lo, hi = hi, lo
    return list(range(lo, hi + 1))


_ACTIONS = ("add", "extract", "list", "clear")


@command(global_=True)
def learn(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    action: str = typer.Option(
        "list", "--action",
        help="动作：add（手动沉淀一条）/ extract（基于区间 LLM 提炼）/ list（列出）/ clear（清空）",
    ),
    category: str = typer.Option(
        "general", "--category",
        help="技法类别：hook / pacing / character / style / general",
    ),
    text: Optional[str] = typer.Option(
        None, "--text", help="add 动作的正文（写法描述）"
    ),
    range_: Optional[str] = typer.Option(
        None, "--range", help="extract 动作的章节区间，如 '1-5'"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="以 JSON 形式输出结果到 stdout"
    ),
    env_file: Optional[str] = typer.Option(
        None, "--env", help="指定 .env 文件（仅本次命令生效，透传给下游 LLMClient）"
    ),
) -> None:
    """项目学习闭环（增量 E）

    写后沉淀「好用的写法 / 钩子设计 / 节奏模板」到项目长期记忆
    （.state/learnings/learnings.json），写前由 M5 注入上下文，形成技法学习闭环。

    动作：
      - add：    learn add --category hook --text "..." 手动沉淀一条
      - extract：learn extract --range 1-5 基于章节 LLM 提炼并批量沉淀
      - list：   learn list 列出全部沉淀（含来源章节）
      - clear：  learn clear 清空全部沉淀

    --json 输出字段依动作不同：list→{count, learnings}；add→{learning}；
    extract→{extracted, learnings}；clear→{cleared}。
    """
    if env_file:
        os.environ["NOVEL_AGENT_DOTENV"] = env_file

    project_path = Path(project_dir)
    store = LearningStore(project_path)

    if action not in _ACTIONS:
        msg = f"未知动作 {action!r}，可选：{', '.join(_ACTIONS)}"
        if json_output:
            emit_result(
                {"success": False, "error": {"code": "bad_action", "message": msg}},
                json_mode=True,
            )
        else:
            console.print(f"[bold red]✗[/bold red] {msg}")
        raise typer.Exit(code=1) from None

    # ---- list ----
    if action == "list":
        items = store.list()
        if json_output:
            emit_result(
                {
                    "success": True,
                    "count": len(items),
                    "learnings": [asdict(x) for x in items],
                },
                json_mode=True,
            )
            return
        _render_list(items)
        return

    # ---- clear ----
    if action == "clear":
        cleared = store.clear()
        if json_output:
            emit_result({"success": True, "cleared": cleared}, json_mode=True)
            return
        console.print(f"[green]✓[/green] 已清空 {cleared} 条学习沉淀")
        return

    # ---- add ----
    if action == "add":
        if not text or not text.strip():
            msg = "add 动作需要 --text 参数"
            if json_output:
                emit_result(
                    {"success": False, "error": {"code": "missing_text", "message": msg}},
                    json_mode=True,
                )
            else:
                console.print(f"[bold red]✗[/bold red] {msg}")
            raise typer.Exit(code=1) from None
        item = store.add(category=category, text=text.strip())
        if json_output:
            emit_result({"success": True, "learning": asdict(item)}, json_mode=True)
            return
        console.print(
            f"[green]✓[/green] 已沉淀 [{item.category}] {item.id}：{item.text}"
        )
        return

    # ---- extract ----
    if action == "extract":
        try:
            nums = _resolve_chapter_nums(range_)
        except ValueError as e:
            if json_output:
                emit_result(
                    {"success": False, "error": {"code": "bad_range", "message": str(e)}},
                    json_mode=True,
                )
            else:
                console.print(f"[bold red]✗[/bold red] {e}")
            raise typer.Exit(code=1) from e
        if not nums:
            msg = "extract 动作需要 --range A-B 指定章节区间"
            if json_output:
                emit_result(
                    {"success": False, "error": {"code": "missing_range", "message": msg}},
                    json_mode=True,
                )
            else:
                console.print(f"[bold red]✗[/bold red] {msg}")
            raise typer.Exit(code=1) from None
        # D：--env 透传后构建 miner（内部 LLMClient 自动读取 .env）
        miner = LearningMiner(project_path, llm=LLMClient())
        try:
            items = miner.extract_and_save(nums)
        except Exception as e:  # noqa: BLE001 - 提炼失败统一兜为错误信封
            if json_output:
                emit_result(
                    {"success": False, "error": {"code": "extract_failed", "message": str(e)}},
                    json_mode=True,
                )
            else:
                console.print(f"[bold red]✗[/bold red] 提炼失败：{e}")
            raise typer.Exit(code=1) from e

        if json_output:
            emit_result(
                {
                    "success": True,
                    "extracted": len(items),
                    "chapters": nums,
                    "learnings": [asdict(x) for x in store.load()],
                },
                json_mode=True,
            )
            return
        _render_list(store.load())
        console.print(
            f"[dim]从 {len(nums)} 章提炼并沉淀 {len(items)} 条技法[/dim]"
        )


def _render_list(items: list[Learning]) -> None:
    """以 rich 表格渲染学习沉淀列表（非 json 模式）"""
    from rich.table import Table

    if not items:
        console.print("（暂无学习沉淀，使用 learn add / learn extract 沉淀写法）")
        return

    table = Table(title="项目学习沉淀（E）")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("类别", style="magenta", no_wrap=True)
    table.add_column("写法", style="white")
    table.add_column("来源章节", style="dim")
    for x in items:
        src = ",".join(f"ch{n:03d}" for n in x.source_chapters) or "—"
        table.add_row(x.id, x.category, x.text, src)
    console.print(table)
