"""guardrail-scan 命令 —— G14 成书质量护栏扫描

对已有小说的全部章节运行三条护栏规则（英文残留 / 占位标题 / 跨章重复），
输出逐章体检报告。这是 NovelAgent 的**内置校验功能**，由 CLI 直接调用，
也可在 autowrite（写时 BLOCK 门禁）/ compose（完本体检）中自动触发。

用法：
    novel-agent guardrail-scan -d <dir>
    novel-agent guardrail-scan -d <dir> --json
    novel-agent guardrail-scan -d <dir> --scope junk,title,dup
    novel-agent guardrail-scan -d <dir> --no-report
"""
from __future__ import annotations

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *


@command(global_=True)
def guardrail_scan(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    scope: str = typer.Option(
        "junk,title,dup", "--scope",
        help="检查项：junk(英文残留) / title(占位标题) / dup(跨章重复)，逗号分隔",
    ),
    json_output: bool = typer.Option(
        False, "--json", help="以 JSON 形式输出结果到 stdout"
    ),
    out_report: bool = typer.Option(
        True, "--report/--no-report", help="是否写 .state/guardrail_scan_report.md"
    ),
) -> None:
    """G14 成书质量护栏扫描 —— 英文残留 / 占位标题 / 跨章重复 体检报告"""
    import json
    from pathlib import Path

    from agent.core.guardrails import Guardrails

    project_path = Path(project_dir)
    enforce_gate(str(project_path), "guardrail_scan")

    chapters_dir = project_path / "chapters"
    if not chapters_dir.exists():
        console.print(f"[bold red]✗[/bold red] {chapters_dir} 不存在")
        raise typer.Exit(code=1)

    scopes = {s.strip() for s in scope.split(",") if s.strip()}
    check_junk = "junk" in scopes
    check_title = "title" in scopes
    check_dup = "dup" in scopes

    gr = Guardrails(
        check_junk=check_junk,
        check_title=check_title,
        check_dup=check_dup,
    )

    files = sorted(chapters_dir.glob("ch*.md"))
    report: dict[str, list[dict]] = {"junk": [], "title": [], "dup": []}

    for f in files:
        txt = f.read_text(encoding="utf-8")
        r = gr.check_text(txt)
        ch = f.stem
        for v in r.violations:
            if v.rule_id == "non_chinese_junk":
                report["junk"].append({"chapter": ch, "message": v.message})
            elif v.rule_id == "title_placeholder":
                report["title"].append({"chapter": ch, "message": v.message})
            elif v.rule_id == "paragraph_dup":
                report["dup"].append({"chapter": ch, "message": v.message})
        # 跨章去重：本章检查完再注册指纹，避免与自身比对
        if check_dup:
            gr.register_fingerprints(ch[2:], txt)

    total = len(report["junk"]) + len(report["title"]) + len(report["dup"])

    if json_output:
        console.print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        console.print(f"\n[bold]成书质量护栏扫描：{project_path.name}[/bold]  共 {len(files)} 章")
        if check_junk:
            console.print(f"  [red]英文残留[/red]：{len(report['junk'])} 处")
            for it in report["junk"][:60]:
                console.print(f"    - {it['chapter']}: {it['message'][:90]}")
        if check_title:
            console.print(f"  [yellow]占位标题[/yellow]：{len(report['title'])} 处")
            for it in report["title"][:60]:
                console.print(f"    - {it['chapter']}: {it['message'][:90]}")
        if check_dup:
            console.print(f"  [cyan]跨章重复[/cyan]：{len(report['dup'])} 处")
            for it in report["dup"][:60]:
                console.print(f"    - {it['chapter']}: {it['message'][:90]}")
        console.print(f"\n合计 {total} 处问题")

    if out_report:
        rp = project_path / ".state" / "guardrail_scan_report.md"
        rp.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            f"# 成书质量护栏扫描报告：{project_path.name}",
            "",
            f"- 扫描章节：{len(files)}",
            f"- 检查项：{scope}",
            f"- 合计问题：{total}",
            "",
        ]
        labels = {"junk": "## 英文残留", "title": "## 占位标题", "dup": "## 跨章重复"}
        enabled = {"junk": check_junk, "title": check_title, "dup": check_dup}
        for key, label in labels.items():
            if not enabled[key]:
                continue
            lines.append(label)
            lines.append("")
            if report[key]:
                for it in report[key]:
                    lines.append(f"- {it['chapter']}: {it['message']}")
            else:
                lines.append("- 无")
            lines.append("")
        rp.write_text("\n".join(lines), encoding="utf-8")
        console.print(f"\n[green]报告已写：{rp}[/green]")
