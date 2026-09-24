"""rewrite 命令 —— A3 反馈→定向改写（用户好用闭环）

把用户对某一章的反馈（"太拖" / "主角太蠢" / "感情戏不够"）变成重写，
而不是只能整章回退或整本重跑。

用法：
    novel-agent rewrite -d <dir> --chapter 12 --feedback "节奏太慢，删水"
    novel-agent rewrite -d <dir> --chapter 12 --feedback "..." --gate block
    novel-agent rewrite -d <dir> --chapter 12 --mode full --feedback "与第13章雷同，整章重写"
    novel-agent rewrite -d <dir> --chapter 12 --interactive   # 反复反馈直到满意

两种模式：
    patch（默认）局部定向修补、最小改动；full 整章重写（注入本章逐章契约与字数区间，
    要求不复述原文、不复述邻章），用于消除雷同/注水/同质化。

默认 advisory 门禁（违规仅告警不阻断）；--gate block 时命中违规拒绝落盘。
每次重写自动备份原章到 .state/rewrite_backups/。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from agent.cli._app import app, command, console, typer
from agent.cli._shared import enforce_gate, emit_result, make_quiet_console
from agent.core.engine.state_machine import State


@command(allowed_states=(State.WRITING, State.PAUSED, State.COMPLETED), writes=True)
def rewrite(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    chapter: int = typer.Option(
        0, "--chapter", "-c", help="要重写的章节号（1-based）；--interactive 时可省略"
    ),
    feedback: str = typer.Option(
        "", "--feedback", "-f", help="用户反馈文本（自由语言）"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="以 JSON 形式输出结果到 stdout"
    ),
    env_file: str = typer.Option(
        None, "--env", help="指定 .env 文件（透传下游 GatewayAdapter）"
    ),
    no_backup: bool = typer.Option(
        False, "--no-backup", help="不备份原章（默认会备份到 .state/rewrite_backups/）"
    ),
    gate: str = typer.Option(
        "advisory", "--gate",
        help="护栏门禁：advisory（默认，违规告警不阻断）/ block（违规拒绝落盘）"
    ),
    mode: str = typer.Option(
        "patch", "--mode",
        help="改写模式：patch（默认，局部定向修补、最小改动）/ "
             "full（整章重写：注入本章逐章契约 + 字数区间，要求不复述原文与邻章）"
    ),
    interactive: bool = typer.Option(
        False, "--interactive", "-i",
        help="交互式反馈循环：反复输入反馈直到输入空行/accept/done 退出"
    ),
    skip_confirm: bool = typer.Option(
        False, "--yes", help="跳过修改指令确认（自主度 <50 时默认会先展示结构化指令并等确认）"
    ),
) -> None:
    """反馈→定向重写 - 把用户反馈变成局部定向重写（而非整章回退/重跑）

    把"枪手"变成"听话的枪手"：用户读完某章说"太拖/主角太蠢"，系统据此精准改写该章，
    自动备份原章、保留上下文衔接、并把偏好沉淀进长期记忆（下次自动吸收）。
    """
    if env_file:
        os.environ["NOVEL_AGENT_DOTENV"] = env_file

    project_path = Path(project_dir)
    if not (project_path / "world.md").exists():
        if json_output:
            emit_result(
                {"success": False, "error": {"code": "no_world",
                                             "message": f"{project_path / 'world.md'} 不存在"}},
                json_mode=True,
            )
        else:
            console.print(f"[bold red]✗[/bold red] {project_path / 'world.md'} 不存在")
        raise typer.Exit(code=1)

    enforce_gate(str(project_path), "rewrite", json_mode=json_output)

    if mode not in ("patch", "full"):
        _msg = f"非法 --mode: {mode}，可选 patch/full"
        if json_output:
            emit_result(
                {"success": False, "error": {"code": "bad_mode", "message": _msg}},
                json_mode=True,
            )
        else:
            console.print(f"[bold red]✗[/bold red] {_msg}")
        raise typer.Exit(code=2)

    # 接线：LLM 调用事件 → <project>/.events/events.jsonl（复用公共接线，避免复制）
    from agent.core.event_sourcing.llm_wiring import wire_llm_event_hook

    wire_llm_event_hook(project_path)

    from agent.core.quality.rewrite.feedback_rewriter import FeedbackRewriter
    from agent.core.quality.guardrails import build_guardrails
    from agent.client.gateway_adapter import create_gateway

    workflow_console = make_quiet_console() if json_output else console
    rewriter = FeedbackRewriter(
        project_path,
        llm_client=create_gateway(),
        guardrails=build_guardrails(),
        console=workflow_console,
    )

    # P1-6：低自主度时，结构化修改指令需用户确认后才进入改写 prompt
    #（"原始评审文本不得直接成为模型指令"）；--yes 或 JSON 模式跳过。
    from agent.core.engine.state_machine import StateMachine as _SM

    _sm = _SM(project_path)
    _sm.load()
    autonomy = int(_sm.autonomy_level)
    confirm_fn = None
    if autonomy < 50 and not skip_confirm and not json_output:
        from agent.core.quality.rewrite.instruction import render_instruction

        def confirm_fn(inst):  # noqa: ANN001, ANN202
            workflow_console.print(
                f"[yellow]修改指令已结构化（当前自主度 {autonomy} < 50，需确认）：[/yellow]"
            )
            workflow_console.print(render_instruction(inst))
            try:
                ans = input("确认按以上指令改写？[y/N]: ").strip().lower()
            except EOFError:
                return False
            return ans in ("y", "yes")

    def run_once(ch_num: int, fb: str) -> dict:
        try:
            return rewriter.rewrite(
                ch_num, fb, backup=not no_backup, gate_mode=gate,
                confirm_fn=confirm_fn, mode=mode,
            ).to_dict()
        except FileNotFoundError as e:
            return {"success": False, "error": {"code": "chapter_not_found", "message": str(e)}}

    if interactive:
        if chapter <= 0:
            console.print("[bold red]✗[/bold red] --interactive 需要 --chapter")
            raise typer.Exit(code=2)
        results: list[dict] = []
        while True:
            try:
                fb = input(f"\n[第 {chapter} 章] 输入反馈（空行/accept/done 退出）：").strip()
            except EOFError:
                break  # noqa: SILENT_DEGRADE
            if fb.lower() in ("", "accept", "done", "q"):
                break
            res = run_once(chapter, fb)
            results.append(res)
            if json_output:
                emit_result({"success": True, "result": res}, json_mode=True)
            else:
                _print_result(console, res)
        if json_output:
            emit_result({"success": True, "results": results}, json_mode=True)
        else:
            console.print(f"[green]✓ 交互改写结束，共 {len(results)} 次[/green]")
        return

    if chapter <= 0 or not feedback.strip():
        if json_output:
            emit_result(
                {"success": False, "error": {"code": "bad_args",
                                             "message": "需要 --chapter 与 --feedback（或 --interactive）"}},
                json_mode=True,
            )
        else:
            console.print("[bold red]✗[/bold red] 需要 --chapter 与 --feedback（或加 --interactive）")
        raise typer.Exit(code=2)

    res = run_once(chapter, feedback.strip())
    if json_output:
        # ``error`` 有两种形态：结构性失败（dict，如章节不存在）与降级原因
        # （str，如 LLM 不可达 / 确认被拒）。``RewriteResult.to_dict()`` **恒带**
        # ``"error"`` 键（无错时为空串），故不能用 ``"error" in res`` 判成败——
        # 旧写法会把成功也报成失败。
        err = res.get("error")
        if isinstance(err, dict):
            emit_result({"success": False, "error": err}, json_mode=True)
        else:
            emit_result(
                {"success": bool(res.get("rewritten")), "result": res}, json_mode=True
            )
        return
    _print_result(console, res)


def _print_result(c, res: dict) -> None:
    err = res.get("error")
    if isinstance(err, dict):  # 结构性失败（章节不存在等）：无结果可展示
        c.print(f"[bold red]✗ 重写失败[/bold red] {err.get('message', '')}")
        return
    ch = res["chapter"]
    mark = "✓ 已落盘" if res.get("rewritten") else (
        "⛔ 被门禁拦截" if res.get("blocked") else "△ 未改写，保留原章")
    color = "green" if res.get("rewritten") else ("red" if res.get("blocked") else "yellow")
    c.print(
        f"\n[bold {color}]{mark}[/bold {color}] 第 {ch} 章 · "
        f"字数 {res['old_word_count']}→{res['new_word_count']}"
    )
    c.print(f"[dim]{res['changed_summary']}[/dim]")
    if isinstance(err, str) and err:  # 降级原因（LLM 不可达/确认被拒）随结果透出
        c.print(f"[yellow]原因：{err}[/yellow]")
    if res.get("backup_file"):
        c.print(f"[dim]原章备份：{res['backup_file']}[/dim]")
    if not res.get("guardrail_passed"):
        # `GuardrailResult.to_dict()` 的键是 ``violations``（含 error/warn），
        # 没有 ``errors`` 子键——旧写法取 ``.get("errors")`` 恒得空列表，
        # 违规则被静默吞掉。
        _errs = [
            v.get("rule_id")
            for v in (res.get("guardrail_report") or {}).get("violations", [])
            if v.get("severity") == "error"
        ]
        c.print(f"[yellow]护栏告警：{_errs}[/yellow]")
