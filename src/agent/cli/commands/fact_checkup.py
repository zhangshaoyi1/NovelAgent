"""fact-checkup 命令（长线一致性二期 T1 · 离线标定入口）

对存量书 chapters/ch*.md 全量跑事实卡对账（日期账/倒计时/死亡不可逆/持有物账，
纯规则零 LLM），产出标定报告供与 golden cases 清单比对。

只读：绝不修改任何项目文件。对应登记：项目文档/优化/20260930_长线一致性二期_事实对账与真相SSOT.md。
"""

from __future__ import annotations

from pathlib import Path

import typer

from agent.cli._app import app, command


@command(global_=True)
def fact_checkup(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录（含 chapters/）"
    ),
    json_output: bool = typer.Option(False, "--json", help="以 JSON 输出发现清单"),
    report: str = typer.Option(
        "", "--report", help="标定报告输出路径（JSON lines）；缺省不写文件"
    ),
) -> None:
    """事实卡对账（离线）：日期账单调 / 同章倒计时自撞 / 死亡不可逆 / 持有物账平。

    纯规则、零 LLM、只读。确定性冲突（error）即标定命中；
    逐条发现可用于标定召回率与误报率（对照《回归基线_*连续性golden_cases.md》）。
    """
    from rich.console import Console

    from agent.core.continuity.fact_card import (
        _ReconcileState,
        extract_fact_card,
        load_chapters_dir,
        reconcile_card,
        write_calibration_report,
    )

    con = Console()
    chapters = load_chapters_dir(Path(project_dir) / "chapters")
    if not chapters:
        con.print(f"[red]未在 {project_dir}/chapters 找到 ch*.md[/red]")
        raise typer.Exit(1)

    state = _ReconcileState()
    issues: list = []
    for ch in sorted(chapters):
        issues.extend(reconcile_card(extract_fact_card(ch, chapters[ch]), state))

    if report:
        write_calibration_report(issues, report)

    errors = [i for i in issues if i.severity == "error"]
    warns = [i for i in issues if i.severity != "error"]
    if json_output:
        from agent.cli._shared import emit_result

        emit_result(
            {
                "chapters": len(chapters),
                "errors": [i.to_dict() for i in errors],
                "warnings": [i.to_dict() for i in warns],
            }
        )
        return
    con.print(f"[bold]事实卡对账[/bold]：{len(chapters)} 章，error {len(errors)} / warn {len(warns)}")
    for i in errors:
        con.print(f"  ch{i.chapter:03d} [red][{i.rule_id}][/red] {i.message}")
    for i in warns:
        con.print(f"  ch{i.chapter:03d} [yellow][{i.rule_id}][/yellow] {i.message}")
    if report:
        con.print(f"标定报告已写入 {report}")
