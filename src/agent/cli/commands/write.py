from __future__ import annotations

import os
from pathlib import Path

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

from agent.core.engine.state_machine import State


def _rescue_disk_chapter(project_path: Path) -> dict | None:
    """写章 run() 抛异常时，以磁盘落盘好章为权威判定是否实际成功。

    根治：某次 run 已在落盘步骤写出好章、但因收尾/瞬时异常（如 LLM 超时、
    writer 结构化校验重试耗尽、Windows 清理步骤异常）而在推进 state 前抛异常
    → 被 CLI 误判 rc=1。本函数检查「计划写入的下一章」是否已在磁盘成为有效好章，
    若是则视为成功返回，避免上层 daemon 因 rc=1 反复重写同一章
    （历史 ch22/ch29 死循环根因）。仅当磁盘确有有效好章时才救活；
    生成阶段即失败（磁盘无章）则正确返回 None，交由原失败逻辑处理。
    """
    import re as _re

    try:
        import frontmatter as _fm
        from agent.core.engine.state_machine import StateMachine

        _sm = StateMachine(project_path)
        _sm.load()
        _cur = int(_sm.progress.get("current_chapter") or 0)
        _tgt = _cur + 1
        _cf = Path(project_path) / "chapters" / f"ch{_tgt:03d}.md"
        if not _cf.exists():
            return None
        _post = _fm.load(_cf)
        _qp = _post.metadata.get("quality_passed")
        _wc = _post.metadata.get("word_count") or 0
        try:
            _wc = int(_wc)
        except (TypeError, ValueError):
            _wc = 0
        _cjk = len(_re.findall(r"[一-鿿]", _post.content or ""))
        if _qp is not True or (_wc < 600 and _cjk < 600):
            return None
        return {
            "success": True,
            "chapter": _tgt,
            "title": _post.metadata.get("title", ""),
            "word_count": _wc,
            "quality_passed": True,
            "revision_attempts": _post.metadata.get("revision_attempts", 0),
            "rag_context_len": 0,
            "d_issues": [],
            "subline": _post.metadata.get("subline", ""),
            "route_node": _post.metadata.get("route_node", ""),
        }
    except Exception:  # noqa: BLE001 - rescue 自身异常视为未救活
        return None


@command(allowed_states=(State.CHARACTER_DESIGN, State.WRITING,))
def write(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    json_output: bool = typer.Option(
        False, "--json",
        help="以 JSON 形式输出结果到 stdout（工作流内部 rich 输出转 stderr）",
    ),
    env_file: str = typer.Option(
        None, "--env",
        help="指定 .env 文件（仅本次命令生效，透传给下游 LLMClient）",
    ),
    strict_review: bool = typer.Option(
        True, "--strict-review", "--no-strict-review",
        help="开启 D 多维 LLM 质量审查（爽点/OOC/连贯性/追读力），"
             "维度 blocking 会触发重写；默认开启（每章默认审查）。"
             "如需跳过审查可加 --no-strict-review",
    ),
    mode: str = typer.Option(
        "auto", "--mode",
        help="写章引擎模式：auto(默认,自主 Agentic Loop) / heavy(更严) / "
             "light(更轻)。",
    ),
) -> None:
    """章节创作 - 生成下一章正文

    默认走 **自主 Agentic Loop**（--mode auto）：Writer Agent 在工具驱动的循环中
    自主调工具、自评、提交，外环 Critic（九项 LLM 审稿）门禁 + 修订，质量不低于旧 M5。

    可用 --mode 切换引擎：
      - auto（默认）：自主 Agentic Loop，全自主写章
      - heavy：更严（更多修订轮次）
      - light：更轻（仅首稿 + 单次自检，不修订）

    状态转换：CHARACTER_DESIGN → WRITING（首次）/ WRITING → WRITING（后续）

    Args:
        project_dir: 小说项目目录
    """
    from agent.core.base.exceptions import PreValidationBlocked

    # D：--env 透传（命令级设置环境变量，下游所有 LLMClient() 自动读取）
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

    # 统一门禁：当前阶段是否允许 /write（来自 command_router / StateMachine）
    enforce_gate(str(project_path), "write", json_mode=json_output)

    # 接线：LLM 调用事件 → <project>/.events/events.jsonl（复用公共接线，避免复制）
    from agent.core.event_sourcing.llm_wiring import wire_llm_event_hook

    wire_llm_event_hook(project_path)

    # --mode 引擎选择：pipeline 已移除，统一走 Agentic Loop
    workflow_console = make_quiet_console() if json_output else console
    if mode in ("auto", "heavy", "light"):
        from agent.workflows.agentic_write import AgenticWriteWorkflow

        workflow = AgenticWriteWorkflow(
            project_dir=project_path,
            console=workflow_console,
            tier=mode,
        )
    else:
        if json_output:
            emit_result(
                {
                    "success": False,
                    "error": {
                        "code": "bad_mode",
                        "message": f"非法 --mode: {mode}，可选 auto/heavy/light",
                    },
                },
                json_mode=True,
            )
        else:
            console.print(f"[bold red]✗[/bold red] 非法 --mode: {mode}，可选 auto/heavy/light")
        raise typer.Exit(code=2)
    try:
        result = workflow.run()
        # Phase 5（巡检自愈）：写章成功 → 清除历史 last_error（区分系统异常 vs 正常）
        # 注意：StateMachine 构造后必须 load() 才能 save()，否则会覆盖 progress 全部字段
        try:
            from agent.core.engine.state_machine import StateMachine

            _sm = StateMachine(project_path)
            _sm.load()
            _sm.clear_write_error()
        except Exception:  # noqa: BLE001 - 清错失败不阻断
            pass
        if json_output:
            # subline / route_node 取自章节 frontmatter（_save_chapter 必写这两个字段）
            import frontmatter

            post = frontmatter.load(result.chapter_file)
            emit_result(
                {
                    "success": True,
                    "chapter": result.chapter_num,
                    "title": result.chapter_title,
                    "word_count": result.word_count,
                    "quality_passed": result.quality_passed,
                    "revision_attempts": result.revision_attempts,
                    "rag_context_len": result.rag_context_len,
                    "d_issues": result.d_issues,
                    "subline": post.metadata.get("subline", ""),
                    "route_node": post.metadata.get("route_node", ""),
                },
                json_mode=True,
            )
            return
        status = "通过" if result.quality_passed else "未完全通过"
        engine_label = "Agentic"
        console.print(
            f"\n[bold green]✓ {engine_label} 写章完成[/bold green] "
            f"第 {result.chapter_num} 章 · {result.word_count} 字 · "
            f"质量{status} · 修订 {result.revision_attempts} 次"
        )
        # E4 证据链小结
        chain = result.evidence_chain
        if chain.total() > 0:
            console.print(
                f"[dim]证据链：角色 {len(chain.characters)} · "
                f"伏笔 {len(chain.foreshadows)} · 设定 {len(chain.settings)} "
                f"（缺失源 {len(chain.missing_sources)}）[/dim]"
            )
    except PreValidationBlocked as blocked:
        # E3：高严重度冲突，生成被中断，需用户仲裁
        # Phase 5（巡检自愈）：记录 last_error + 累加连续失败计数，供自动化区分「等待用户决策」并触发告警
        # 注意：StateMachine 构造后必须 load() 才能 save()，否则会覆盖 progress 全部字段
        try:
            from agent.core.engine.state_machine import StateMachine

            _sm = StateMachine(project_path)
            _sm.load()
            _sm.record_write_error(
                "pre_validation_blocked", blocked.report.summary
            )
            _sm.bump_write_failure()
        except Exception:  # noqa: BLE001 - 记录失败不阻断
            pass
        if json_output:
            emit_result(
                {
                    "success": False,
                    "error": {
                        "code": "pre_validation_blocked",
                        "message": blocked.report.summary,
                    },
                },
                json_mode=True,
            )
        else:
            console.print(
                f"\n[bold red]⚠ 前置冲突检测未通过，已暂停生成[/bold red]"
            )
            conflict_arbiter.show_report(blocked.report)
            console.print(
                "\n[yellow]请先解决上述高严重度冲突（修改 world.md / "
                "subline / 角色档案，或使用 /adjust-route 调整路线），"
                "再重新运行 write。[/yellow]"
            )
        raise typer.Exit(code=2) from blocked
    except Exception as e:
        # ★根治：好章已落盘但 run() 因收尾/瞬时异常失败 → 以磁盘为权威判成功
        # 消除「好章落盘却 rc=1」导致的 daemon 反复重写死循环（历史 ch22/ch29）
        _rescued = _rescue_disk_chapter(project_path)
        if _rescued is not None:
            try:
                from agent.core.engine.state_machine import StateMachine as _SM2

                _s2 = _SM2(project_path)
                _s2.load()
                _s2.clear_write_error()
            except Exception:  # noqa: BLE001 - 清错失败不阻断
                pass
            if json_output:
                emit_result(_rescued, json_mode=True)
            else:
                console.print(
                    f"\n[bold green]✓ 写章完成（磁盘落盘好章已确认）[/bold green] "
                    f"第 {_rescued['chapter']} 章 · {_rescued['word_count']} 字"
                )
            return
        # —— 以下为原失败逻辑：记录 last_error + 累加失败 + exit 1 ——
        # Phase 5（巡检自愈）：记录 last_error + 累加连续失败计数，供自动化区分「系统异常」并触发告警
        # 注意：StateMachine 构造后必须 load() 才能 save()，否则会覆盖 progress 全部字段
        try:
            from agent.core.engine.state_machine import StateMachine

            _sm = StateMachine(project_path)
            _sm.load()
            _sm.record_write_error("write_failed", str(e))
            _sm.bump_write_failure()
        except Exception:  # noqa: BLE001 - 记录失败不阻断
            pass
        if json_output:
            emit_result(
                {
                    "success": False,
                    "error": {"code": "write_failed", "message": str(e)},
                },
                json_mode=True,
            )
        else:
            console.print(f"\n[bold red]✗ 写章失败[/bold red] {e}")
        raise typer.Exit(code=1) from e
