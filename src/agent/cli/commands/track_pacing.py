from __future__ import annotations

import os
import re
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from agent.cli._app import app, console, typer, command
from agent.cli._shared import emit_result

from agent.core.story.pacing_store import PacingStore
from agent.workflows.m16_pacing import PacingTracker


def _resolve_chapter_nums(
    project_path: Path, chapter: Optional[int], range_: Optional[str]
) -> list[int]:
    """解析目标章节号列表

    - ``--chapter N``：单章
    - ``--range A-B``：闭区间 [A, B]
    - 都不给：扫描 ``chapters/`` 下所有 ``chNNN.md``
    """
    chapters_dir = project_path / "chapters"
    if chapter:
        return [int(chapter)]
    if range_:
        m = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", range_)
        if not m:
            raise ValueError(f"--range 格式应为 'A-B'，收到：{range_!r}")
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo > hi:
            lo, hi = hi, lo
        return list(range(lo, hi + 1))
    nums: list[int] = []
    if chapters_dir.exists():
        for f in sorted(chapters_dir.glob("ch*.md")):
            m = re.match(r"ch(\d+)", f.stem)
            if m:
                nums.append(int(m.group(1)))
    return nums


def _read_chapter_text(project_path: Path, n: int) -> Optional[str]:
    """读取指定章节正文（剥离 frontmatter）"""
    f = project_path / "chapters" / f"ch{n:03d}.md"
    if not f.exists():
        return None
    text = f.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            text = parts[2]
    return text.strip()


@command(global_=True)
def track_pacing(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    chapter: Optional[int] = typer.Option(
        None, "--chapter", help="抽取单章（章节号），如 --chapter 3"
    ),
    range_: Optional[str] = typer.Option(
        None, "--range", help="抽取区间，闭区间 'A-B'，如 --range 1-10"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="以 JSON 形式输出追踪结果到 stdout"
    ),
    env_file: Optional[str] = typer.Option(
        None, "--env", help="指定 .env 文件（仅本次命令生效，透传给下游 LLMClient）"
    ),
) -> None:
    """追读力抽取与追踪（增量 C）

    对指定章节（单章 / 区间 / 全部）调用 ``PacingTracker.extract`` 抽取
    Hook / 爽点 / 微 payoff / 债务，逐章对账并写入 ``.state/pacing.json`` 账本。
    LLM 不可用时降级为空抽取，账本落盘失败不阻断追踪。

    --json 输出字段：success / chapters / hooks / cool_points / micro_payoffs / debts
    """
    if env_file:
        os.environ["NOVEL_AGENT_DOTENV"] = env_file

    project_path = Path(project_dir)
    if not (project_path / "world.md").exists():
        if json_output:
            emit_result(
                {
                    "success": False,
                    "error": {
                        "code": "no_world",
                        "message": f"{project_path / 'world.md'} 不存在，请先运行 start",
                    },
                },
                json_mode=True,
            )
        else:
            console.print(
                f"[bold red]✗[/bold red] {project_path / 'world.md'} 不存在，请先运行 start"
            )
        raise typer.Exit(code=1)

    try:
        nums = _resolve_chapter_nums(project_path, chapter, range_)
    except ValueError as e:
        if json_output:
            emit_result(
                {
                    "success": False,
                    "error": {"code": "bad_range", "message": str(e)},
                },
                json_mode=True,
            )
        else:
            console.print(f"[bold red]✗[/bold red] {e}")
        raise typer.Exit(code=1) from e

    # D：--env 透传后构建 tracker（内部 LLMClient 自动读取 .env）
    tracker = PacingTracker(project_path)
    store = PacingStore(project_path)

    all_hooks: list[str] = []
    all_cool: list[str] = []
    all_payoffs: list[str] = []
    processed: list[int] = []

    for n in nums:
        text = _read_chapter_text(project_path, n)
        if text is None:
            continue
        ext = tracker.extract(text)
        all_hooks.extend(ext.hooks)
        all_cool.extend(ext.cool_points)
        all_payoffs.extend(ext.micro_payoffs)
        # 逐章对账并落盘（reconcile 内部 load 最新账本，去重后 append 新债务/爽点密度）
        try:
            ledger = tracker.reconcile([], ext)
            store.save(ledger)
        except Exception:  # noqa: BLE001 - 账本落盘失败不阻断追踪
            console.print(
                f"[yellow]⚠ 第 {n} 章账本落盘失败，已跳过（不影响其他章节）[/yellow]"
            )
        processed.append(n)

    # 债务输出与持久化账本保持一致（已去重）：读取最终 open_debts
    final_debts = [asdict(d) for d in store.load().open_debts]
    result = {
        "success": True,
        "chapters": processed,
        "hooks": all_hooks,
        "cool_points": all_cool,
        "micro_payoffs": all_payoffs,
        "debts": final_debts,
    }

    if json_output:
        emit_result(result, json_mode=True)
        return

    _render(result, processed)


def _render(result: dict, processed: list[int]) -> None:
    """以 rich 表格渲染追踪结果（非 json 模式）"""
    from rich.table import Table

    table = Table(title="追读力追踪（增量 C）")
    table.add_column("维度", style="cyan", no_wrap=True)
    table.add_column("数量", style="white", no_wrap=True)
    table.add_column("内容")

    def _fmt(items: list[str], key: str = "desc") -> str:
        if not items:
            return "（无）"
        return "\n".join(f"- {it}" for it in items)

    table.add_row(
        "章节", str(len(processed)),
        ", ".join(f"ch{n:03d}" for n in processed) or "（无匹配章节）",
    )
    table.add_row("Hook 钩子", str(len(result["hooks"])), _fmt(result["hooks"]))
    table.add_row("爽点", str(len(result["cool_points"])), _fmt(result["cool_points"]))
    table.add_row("微 payoff", str(len(result["micro_payoffs"])), _fmt(result["micro_payoffs"]))
    debts_str = (
        "\n".join(
            f"- [{d.get('kind', '')}] {d.get('id', '')}：{d.get('desc', '')}"
            for d in result["debts"]
        )
        or "（无）"
    )
    table.add_row("债务（钩子债/伏笔债）", str(len(result["debts"])), debts_str)

    console.print(table)
    console.print(
        f"[dim]已写入 {Path('.state/pacing.json').as_posix()}（{len(processed)} 章）[/dim]"
    )
