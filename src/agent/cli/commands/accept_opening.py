"""作者接受开篇（金三升级豁免）——accept-opening

实弹背景（凡尘炼废 2026-10-03/04）：金三开篇评分在线附近方差振荡（48→56→54），
重写追分是打地鼠；FIRST_CHAPTERS 升级语义使每个批次末尾必撞同一门。
本命令把「作者读过开篇、决定接受」变成**登记在案的显式豁免**（policy
``golden_three.opening_accepted`` + 备注），而不是静默调阈值：

- 豁免 ≠ 达标：体检 notes 留痕「豁免而非评分达标」，其余维度照常裁决；
- 可审计：谁/何时/为什么记在 policy note 里，Web 质量策略页可见；
- 可逆：`accept-opening --revoke` 撤销。

用法：
    novel-agent accept-opening -d <dir> --note "开篇慢热为有意节奏，接受"
    novel-agent accept-opening -d <dir> --revoke
"""

from __future__ import annotations

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *
from agent.core.engine.state_machine import State


@command(allowed_states=(
    State.WRITING, State.PAUSED, State.AWAITING_CHECKPOINT, State.COMPLETED
), writes=True)
def accept_opening(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    note: str = typer.Option(
        "", "--note", "-n", help="豁免备注（谁/为什么；留痕进策略文件与体检 notes）"
    ),
    revoke: bool = typer.Option(
        False, "--revoke", help="撤销豁免（恢复金三升级）"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="JSON 输出"
    ),
) -> None:
    """接受开篇：豁免金三 FIRST_CHAPTERS 升级语义（可 --revoke 撤销）。"""
    import json
    from pathlib import Path

    from agent.core.infra.degrade import degrade
    from agent.core.quality.policy import POLICY_FILE

    # 直接调用（测试/Web 元数据读取）时 typer 默认值是 OptionInfo 对象——归一化
    revoke = bool(getattr(revoke, "default", revoke))
    json_output = bool(getattr(json_output, "default", json_output))

    project_path = Path(project_dir)
    policy_path = project_path / POLICY_FILE
    try:
        policy = (
            json.loads(policy_path.read_text(encoding="utf-8"))
            if policy_path.exists() else {}
        )
        if not isinstance(policy, dict):
            policy = {}
    except Exception as e:  # noqa: BLE001 - 旧文件损坏按空策略起写（显性留痕）
        degrade("accept_opening.load", "quality_policy.json 不可读，按空策略覆盖", e)
        policy = {}

    section = policy.get("golden_three") or {}
    if not isinstance(section, dict):
        section = {}
    if revoke:
        section["opening_accepted"] = False
        section["opening_accepted_note"] = ""
        action_label = "已撤销开篇豁免（金三升级恢复）"
    else:
        stamp = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M")
        section["opening_accepted"] = True
        section["opening_accepted_note"] = (
            f"{stamp} {note.strip() or '作者接受开篇（未留备注）'}"
        )
        action_label = "已接受开篇：金三 FIRST_CHAPTERS 升级豁免生效（豁免≠达标）"
    policy["golden_three"] = section

    try:
        policy_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = policy_path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(policy, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(policy_path)
    except Exception as e:  # noqa: BLE001 - 落盘失败必须显性失败（豁免未生效）
        degrade("accept_opening.save", "策略落盘失败，豁免未生效", e)
        if json_output:
            emit_result(
                {"success": False,
                 "error": {"code": "policy_write_failed", "message": str(e)}},
                json_mode=True,
            )
        else:
            console.print(f"[red]✗ 策略落盘失败：{e}[/red]")
        raise typer.Exit(code=1)

    if json_output:
        emit_result({"success": True, "message": action_label, "golden_three": section},
                    json_mode=True)
        return
    console.print(f"[green]✓ {action_label}[/green]")
    if section.get("opening_accepted_note"):
        console.print(f"[dim]备注：{section['opening_accepted_note']}[/dim]")
