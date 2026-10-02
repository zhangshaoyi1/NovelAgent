"""规划层语义评审（计划审稿人，登记单 20261001_信任链与叙事上限六项能力·子项 4）

背景
----
批间复规划（``batch_replan.maybe_replan``）此前只有两道检查：四管理者确定性审计
（判结构：弧线衔接/重叠/空洞/越界）与 M5 规划评委（判语义但**恒不阻断**，
见登记单 ``20260919_M5规划评委从未生效``）。计划"写得好不好"——遗留债务接没接住、
戏剧结构成不成立、是否撞账本的语义错误——无人裁决。

定位
----
- **计划审稿人**：审的对象是计划（下一批弧线排布），不是正文——离 replan 足够近，
  不另起独立命令（唯一消费点锁死在 batch_replan 链内）。
- **三态裁决、有真阻断权**：``pass`` / ``revise``（带具体修改指令，打回 replan
  **上限 1 次**，与 plan_managers 语义对齐）/ ``infeasible``（升级检查点挂起，
  把争议摆给作者）。第二次仍不过 → 带分歧进检查点，不形成死循环。
- LLM 不可用 → 降级为现状行为（评审缺席不阻断复规划，degrade 略痕）——
  与 Supervisor 终审同一降级纪律。
- 与 M5 评委职责区分：评委只记录采样观测（恒不阻断），本 Agent 的裁决**进入控制流**。

只审三个问题（老手编辑看大纲真正看的）：
1. 遗留债务/教训有没有被计划接住（批摘要里的问题，弧线排布是否回应）；
2. 戏剧结构是否成立（弧线强度排布、支线开得值不值、高潮落点）；
3. 是否撞设定/账本的语义错误（确定性审计查数字，这里查"逻辑上根本不成立"）。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel

from agent.core.infra.degrade import degrade

#: 评审留痕文件（JSONL，只追加；eval 式回放用）
PLAN_REVIEW_LOG = ".state/plan_review.jsonl"

VALID_VERDICTS = ("pass", "revise", "infeasible")


class PlanReviewOutput(BaseModel):
    """计划审稿人结构化输出契约。"""

    verdict: str  # pass / revise / infeasible（合法性在 review 内校验）
    feedback: str = ""


@dataclass
class PlanReviewResult:
    """一次计划评审的结论。"""

    verdict: str  # pass / revise / infeasible / unavailable
    feedback: str = ""
    source: str = "llm"  # llm / degraded / disabled

    @property
    def ok(self) -> bool:
        return self.verdict == "pass"


def _arcs_text(arcs: Iterable[Any], current_chapter: int, limit: int = 12) -> str:
    """弧线排布压缩为审稿文本（只看未写部分，已写弧线不进审稿视野）。"""
    lines: list[str] = []
    for a in arcs:
        raw_end = getattr(a, "chapter_end", 0)
        end = int(raw_end) if isinstance(raw_end, (int, float)) else 0
        if end < current_chapter:
            continue
        lines.append(
            f"- [{getattr(a, 'id', '?')}] {getattr(a, 'name', '?')}"
            f"（第 {getattr(a, 'chapter_start', '?')}-{end} 章"
            f"{('，支线 ' + str(a.subline_id)) if getattr(a, 'subline_id', '') else ''}）"
            f"：{str(getattr(a, 'goal', '') or '')[:120]}"
        )
        if len(lines) >= limit:
            lines.append(f"-（另有 {max(0, len(list(arcs)) - limit)} 条未列）")
            break
    return "\n".join(lines) or "（无未写弧线）"


def _log(project_dir: Path, result: PlanReviewResult, current_chapter: int) -> None:
    """评审留痕（JSONL 只追加；失败不阻断——留痕不得影响裁决链）。"""
    try:
        p = Path(project_dir) / PLAN_REVIEW_LOG
        p.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "at": time.time(),
            "current_chapter": current_chapter,
            "verdict": result.verdict,
            "source": result.source,
            "feedback": result.feedback[:400],
        }
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001
        degrade("plan_review.log", "计划评审留痕落盘失败", e)


def review_batch_plan(
    project_dir: str | Path,
    plan: Any,
    current_chapter: int,
    summary: str,
    *,
    llm: Any,
    console: Any = None,
    enabled: bool | None = None,
) -> PlanReviewResult:
    """对批间复规划产出做一次语义评审。永不抛异常（异常=unavailable→按 pass 继续）。"""
    if enabled is None:
        enabled = os.getenv("NOVELAGENT_PLAN_REVIEW", "1") != "0"
    result = PlanReviewResult("unavailable", "", source="degraded")
    if not enabled:
        result = PlanReviewResult("pass", "计划评审开关关闭", source="disabled")
        _log(Path(project_dir), result, current_chapter)
        return result
    try:
        from agent.client.gateway_adapter import chat_utility_structured
        from agent.core.infra.prompt_manager import pm

        user = pm.get("agents.plan_review").render_user(
            current_chapter=current_chapter,
            summary=str(summary or "")[:3000],
            arcs_text=_arcs_text(getattr(plan, "episode_tree", []) or [], current_chapter),
            total_chapters=getattr(plan, "total_chapters", "?"),
        )
        verdict = chat_utility_structured(
            llm,
            messages=[
                {"role": "system", "content": pm.get("agents.plan_review").system},
                {"role": "user", "content": user},
            ],
            schema=PlanReviewOutput,
            max_tokens=1024,
            enable_thinking=False,
            name="plan_review",
        )
        raw = str(verdict.verdict).strip().lower()
        if raw not in VALID_VERDICTS:
            raise ValueError(f"评审输出非法 verdict={raw!r}")
        result = PlanReviewResult(raw, str(verdict.feedback or "").strip(), source="llm")
    except Exception as e:  # noqa: BLE001 - 评审缺席不阻断复规划（degrade 留痕）
        degrade(
            "plan_review.review",
            "计划语义评审调用/解析失败，按 pass 继续（评审缺席不阻断复规划）",
            e,
        )
        result = PlanReviewResult("unavailable", f"评审不可用：{e}", source="degraded")
    _log(Path(project_dir), result, current_chapter)
    if result.verdict in ("revise", "infeasible") and console is not None:
        try:
            console.print(
                f"[yellow]✎ 计划审稿人：{_review_label(result.verdict)}——{result.feedback[:200]}[/yellow]"
            )
        except Exception:  # noqa: SILENT_DEGRADE reason=logging-only - 打印失败不影响裁决
            pass
    return result


def _review_label(verdict: str) -> str:
    return {"pass": "通过", "revise": "需修订", "infeasible": "不可行"}.get(verdict, verdict)


class PlanReviewEscalation(Exception):
    """计划评审两次不过 / 不可行 → 升级检查点挂起（带分歧交作者裁决）。"""

    def __init__(self, feedback: str) -> None:
        super().__init__(feedback)
        self.feedback = feedback


def apply_plan_review(
    first: PlanReviewResult,
    second: PlanReviewResult | None,
) -> tuple[str, str]:
    """纯决策函数：两次评审结果 → (proceed | replan | escalate, 反馈文本)。

    - first=pass / unavailable / disabled → proceed（评审缺席不阻断）；
    - first=revise → 打回 replan 一次（上限 1 次）；
    - 打回后仍 revise，或任一次 infeasible → escalate（升级检查点挂起）。
    """
    if first.verdict == "infeasible":
        return "escalate", first.feedback
    if first.verdict in ("pass", "unavailable"):
        return "proceed", first.feedback
    # first == revise：打回一次
    if second is None:
        return "replan", first.feedback
    if second.verdict in ("pass", "unavailable"):
        return "proceed", second.feedback
    return "escalate", f"{first.feedback}；修订后仍不过：{second.feedback}"


def escalate_to_checkpoint(
    project_dir: str | Path,
    feedback: str,
    console: Any = None,
) -> None:
    """评审分歧升级：写检查点卡 + 状态迁移 WRITING→AWAITING_CHECKPOINT（可穿越重启）。"""
    from agent.core.engine.state_machine import Event, State, StateMachine

    project_path = Path(project_dir)
    card = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "batch_range": [],
        "chapters_written": 0,
        "low_confidence_delivery": False,
        "paused": True,
        "pause_reason": "计划审稿人裁决不可行/两次修订仍不过",
        "risks": [f"计划评审分歧：{feedback[:300]}"],
        "next_plan": {},
        "actions": [
            "checkpoint-continue -d <项目>   # 认可现有计划，放行继续",
            "adjust-route -d <项目> --intent <修改方向>  # 按审稿意见调整路线后放行",
        ],
    }
    try:
        p = project_path / ".state" / "checkpoint.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(card, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)
    except Exception as e:  # noqa: BLE001
        degrade("plan_review.escalate", "升级检查点卡片落盘失败", e)
    try:
        sm = StateMachine(project_path)
        sm.load()
        if sm.state is State.WRITING:
            sm.transition(Event.ENTER_CHECKPOINT)
    except Exception as e:  # noqa: BLE001
        degrade("plan_review.escalate", "升级检查点状态迁移失败", e)
    if console is not None:
        try:
            console.print(
                "[red]✗ 计划审稿人两次裁决未通过，已挂起等作者裁决"
                f"：{feedback[:200]}[/red]"
            )
        except Exception:  # noqa: SILENT_DEGRADE reason=logging-only
            pass
