"""M18 错误恢复工作流

基于 PRD F18.1-F18.4，实现四类错误恢复机制：

F18.1 LLM 调用失败自动重试
    - 已在 LLMClient 实现（指数退避 max_retries 次）
    - 本模块提供 handle_llm_failure 便利函数（重试耗尽后的兜底）

F18.2 质量校验连续 M 次不通过
    - 停止自动修订
    - 输出失败报告（Markdown，含失败规则/建议/最终正文/可选决策）
    - 交用户决策：手动改 / 调整规则 / 跳过本次校验项

F18.3 状态机卡死
    - /reset-state 重置到上一稳定点
    - 稳定点定义：CONFIGURING / ARCH_CONFIRMED / CHARACTER_DESIGN / WRITING / COMPLETED
    - 回滚 state.json 到上一稳定状态，并记录历史

F18.4 写作中断恢复
    - 草稿存 .state/draft.wip（生成后、持久化前）
    - 下次进入 WRITING 自动检测草稿，提示续写或丢弃
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from agent.core.state_machine import Event, State, StateMachine
from agent.core.workflow_registry import workflow
from agent.utils import safe_remove


# ============================================================
# 稳定状态定义（F18.3）
# ============================================================
STABLE_STATES: list[State] = [
    State.CONFIGURING,
    State.ARCH_CONFIRMED,
    State.CHARACTER_DESIGN,
    State.WRITING,
    State.COMPLETED,
]


# ============================================================
# F18.2 质量校验失败报告
# ============================================================
@dataclass
class QualityFailureReport:
    """质量校验失败报告"""

    chapter_num: int
    subline_id: str
    attempts: int  # 已修订次数
    max_revisions: int
    failing_rules: list[dict[str, Any]]  # 未通过的规则列表
    suggestions: str
    final_text: str
    decisions: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.decisions:
            self.decisions = ["手动改", "调整规则", "跳过本次校验项"]

    def to_markdown(self) -> str:
        lines: list[str] = []
        lines.append("# 质量校验失败报告")
        lines.append("")
        lines.append(f"- **章节**：第 {self.chapter_num} 章")
        lines.append(f"- **支线**：{self.subline_id}")
        lines.append(f"- **修订次数**：{self.attempts}/{self.max_revisions}")
        lines.append("")
        lines.append("## 未通过规则")
        lines.append("")
        if self.failing_rules:
            lines.append("| 规则 | 问题 |")
            lines.append("|---|---|")
            for rule in self.failing_rules:
                name = rule.get("rule", "")
                issue = rule.get("issue", "")
                lines.append(f"| {name} | {issue} |")
        else:
            lines.append("（无具体规则信息）")
        lines.append("")
        if self.suggestions:
            lines.append("## 修订建议")
            lines.append("")
            lines.append(self.suggestions)
            lines.append("")
        lines.append("## 用户决策")
        lines.append("")
        for i, d in enumerate(self.decisions, 1):
            lines.append(f"{i}. {d}")
        lines.append("")
        lines.append("## 最终正文（草稿）")
        lines.append("")
        lines.append("```")
        lines.append(self.final_text)
        lines.append("```")
        return "\n".join(lines)


class FailureReportBuilder:
    """构建质量校验失败报告（F18.2）"""

    @staticmethod
    def build(
        chapter_num: int,
        subline_id: str,
        attempts: int,
        max_revisions: int,
        quality_report: dict[str, Any],
        final_text: str,
    ) -> QualityFailureReport:
        """从 M5 质量校验结果构建失败报告"""
        rules = quality_report.get("rules", []) or []
        failing = [r for r in rules if not r.get("pass", False)]
        suggestions = str(quality_report.get("suggestions", ""))
        return QualityFailureReport(
            chapter_num=chapter_num,
            subline_id=subline_id,
            attempts=attempts,
            max_revisions=max_revisions,
            failing_rules=failing,
            suggestions=suggestions,
            final_text=final_text,
        )


# ============================================================
# F18.4 草稿管理
# ============================================================
@dataclass
class Draft:
    """未完成章节草稿"""

    chapter_num: int
    subline_id: str
    text: str
    ctx_snapshot: dict[str, Any] = field(default_factory=dict)
    saved_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "chapter_num": self.chapter_num,
            "subline_id": self.subline_id,
            "text": self.text,
            "ctx_snapshot": self.ctx_snapshot,
            "saved_at": self.saved_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Draft":
        return cls(
            chapter_num=int(data.get("chapter_num", 0)),
            subline_id=str(data.get("subline_id", "")),
            text=str(data.get("text", "")),
            ctx_snapshot=data.get("ctx_snapshot", {}) or {},
            saved_at=str(data.get("saved_at", "")),
        )


@workflow("m18_recovery")
class DraftManager:
    """管理 .state/draft.wip 草稿（F18.4）

    生命周期：
        M5 生成章节正文 → save_draft（写入 draft.wip）
        → 质量校验通过 + 持久化 chapters/chXXX.md → clear_draft
        → 若中断，下次进入 WRITING 时 load_draft 检测到草稿，提示续写/丢弃
    """

    DRAFT_FILENAME = "draft.wip"

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = Path(project_dir)
        self.draft_file = self.project_dir / ".state" / self.DRAFT_FILENAME

    def save_draft(
        self,
        chapter_num: int,
        subline_id: str,
        text: str,
        ctx_snapshot: dict[str, Any] | None = None,
    ) -> Path:
        """保存草稿

        Args:
            chapter_num: 章节号
            subline_id: 支线 ID
            text: 章节正文
            ctx_snapshot: 上下文快照（用于续写时恢复）

        Returns:
            草稿文件路径
        """
        self.draft_file.parent.mkdir(parents=True, exist_ok=True)
        draft = Draft(
            chapter_num=chapter_num,
            subline_id=subline_id,
            text=text,
            ctx_snapshot=ctx_snapshot or {},
            saved_at=datetime.now().isoformat(),
        )
        self.draft_file.write_text(
            json.dumps(draft.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return self.draft_file

    def load_draft(self) -> Draft | None:
        """加载草稿（不存在返回 None）"""
        if not self.draft_file.exists():
            return None
        try:
            data = json.loads(self.draft_file.read_text(encoding="utf-8"))
            return Draft.from_dict(data)
        except (json.JSONDecodeError, KeyError, ValueError):
            return None

    def has_draft(self) -> bool:
        """是否存在未完成草稿"""
        return self.draft_file.exists()

    def clear_draft(self) -> bool:
        """清除草稿（持久化成功后调用）

        Returns:
            是否成功清除（草稿原本存在）
        """
        if self.draft_file.exists():
            safe_remove(self.draft_file)
            return True
        return False


# ============================================================
# F18.3 状态机卡死恢复
# ============================================================
@dataclass
class ResetResult:
    """状态重置结果"""

    success: bool
    old_state: str
    new_state: str
    message: str
    history_file: Path | None = None


class StateRecovery:
    """状态机卡死恢复（F18.3）

    /reset-state 行为：
        1. 读取当前 state.json
        2. 备份当前状态到 .state/state_history.json
        3. 找到"上一稳定状态"：
           - 若当前状态在 STABLE_STATES 中，回退到上一个稳定状态
           - 若当前状态不在稳定状态中（如 ARCHITECTING/ARCH_REVISION/OUTLINING/DISCUSSING），
             回退到最近的稳定状态
        4. 写入新的 state.json
    """

    HISTORY_FILENAME = "state_history.json"
    MAX_HISTORY = 20  # 最多保留 20 条历史

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = Path(project_dir)
        self.state_machine = StateMachine(project_dir=self.project_dir)
        self.history_file = self.project_dir / ".state" / self.HISTORY_FILENAME

    def list_stable_states(self) -> list[str]:
        """返回稳定状态列表"""
        return [s.value for s in STABLE_STATES]

    def _previous_stable_state(self, current: State) -> State | None:
        """找到当前状态的上一稳定状态

        逻辑：
            - 若当前状态是稳定状态，返回稳定状态列表中的前一个
            - 若当前状态不是稳定状态，返回最近的稳定状态（按状态机流程倒推）
        """
        # 非稳定状态 → 映射到上一稳定状态
        unstable_to_stable: dict[State, State] = {
            State.INIT: State.INIT,  # INIT 是初始，无处可退
            State.DISCUSSING: State.CONFIGURING,
            State.ARCHITECTING: State.CONFIGURING,
            State.ARCH_REVISION: State.ARCH_CONFIRMED,
            State.OUTLINING: State.ARCH_CONFIRMED,
            State.PAUSED: State.WRITING,
        }
        if current in unstable_to_stable:
            return unstable_to_stable[current]

        # 当前是稳定状态 → 返回前一个稳定状态
        stable_order = [State.INIT, State.CONFIGURING, State.ARCH_CONFIRMED,
                        State.CHARACTER_DESIGN, State.WRITING, State.COMPLETED]
        try:
            idx = stable_order.index(current)
        except ValueError:
            return State.INIT
        if idx == 0:
            return State.INIT  # 已是初始
        return stable_order[idx - 1]

    def _save_history(self, snapshot: dict[str, Any]) -> Path:
        """保存状态历史"""
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        history: list[dict[str, Any]] = []
        if self.history_file.exists():
            try:
                history = json.loads(self.history_file.read_text(encoding="utf-8"))
                if not isinstance(history, list):
                    history = []
            except json.JSONDecodeError:
                history = []
        history.append(snapshot)
        # 保留最近 MAX_HISTORY 条
        if len(history) > self.MAX_HISTORY:
            history = history[-self.MAX_HISTORY:]
        self.history_file.write_text(
            json.dumps(history, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return self.history_file

    def list_history(self) -> list[dict[str, Any]]:
        """列出状态历史"""
        if not self.history_file.exists():
            return []
        try:
            history = json.loads(self.history_file.read_text(encoding="utf-8"))
            return history if isinstance(history, list) else []
        except json.JSONDecodeError:
            return []

    def reset_to_last_stable(self) -> ResetResult:
        """重置到上一稳定状态（F18.3）

        Returns:
            ResetResult
        """
        self.state_machine.load()
        old_state = self.state_machine.state

        # 备份当前状态
        snapshot = {
            "state": old_state.value,
            "mode": self.state_machine.mode,
            "progress": dict(self.state_machine.progress),
            "reset_at": datetime.now().isoformat(),
            "reason": "manual_reset",
        }
        history_file = self._save_history(snapshot)

        # 计算目标状态
        new_state = self._previous_stable_state(old_state)
        if new_state is None:
            return ResetResult(
                success=False,
                old_state=old_state.value,
                new_state=old_state.value,
                message="无可回退的稳定状态",
                history_file=history_file,
            )

        if new_state == old_state:
            return ResetResult(
                success=True,
                old_state=old_state.value,
                new_state=new_state.value,
                message=f"当前状态 {old_state.value} 已是初始稳定状态，未回退",
                history_file=history_file,
            )

        # 写入新状态
        self.state_machine.state = new_state
        self.state_machine.save()

        return ResetResult(
            success=True,
            old_state=old_state.value,
            new_state=new_state.value,
            message=f"已从 {old_state.value} 回退到 {new_state.value}",
            history_file=history_file,
        )

    def reset_to_state(self, target: State) -> ResetResult:
        """重置到指定状态

        Args:
            target: 目标状态

        Raises:
            ValueError: 目标状态非稳定状态
        """
        if target not in STABLE_STATES and target != State.INIT:
            raise ValueError(
                f"目标状态 {target.value} 不是稳定状态，"
                f"可选: {[s.value for s in [State.INIT] + STABLE_STATES]}"
            )

        self.state_machine.load()
        old_state = self.state_machine.state
        snapshot = {
            "state": old_state.value,
            "mode": self.state_machine.mode,
            "progress": dict(self.state_machine.progress),
            "reset_at": datetime.now().isoformat(),
            "reason": f"manual_reset_to_{target.value}",
        }
        history_file = self._save_history(snapshot)

        self.state_machine.state = target
        self.state_machine.save()

        return ResetResult(
            success=True,
            old_state=old_state.value,
            new_state=target.value,
            message=f"已从 {old_state.value} 重置到 {target.value}",
            history_file=history_file,
        )


# ============================================================
# F18.4 续写检测
# ============================================================
@dataclass
class DraftResumeDecision:
    """草稿续写决策"""

    has_draft: bool
    draft: Draft | None = None
    action: str = ""  # "resume" | "discard" | "none"


def check_draft_on_startup(
    project_dir: Path,
    console: Console | None = None,
    interactive: bool = True,
) -> DraftResumeDecision:
    """进入 WRITING 时检测草稿（F18.4）

    Args:
        project_dir: 项目目录
        console: rich Console
        interactive: 是否交互式询问（False 则仅返回草稿信息，不询问）

    Returns:
        DraftResumeDecision
    """
    cons = console or Console()
    dm = DraftManager(project_dir)
    draft = dm.load_draft()

    if draft is None:
        return DraftResumeDecision(has_draft=False, action="none")

    cons.print(
        Panel(
            f"检测到未完成草稿：第 {draft.chapter_num} 章（{draft.subline_id}）\n"
            f"保存时间：{draft.saved_at}\n"
            f"正文长度：{len(draft.text)} 字",
            title="写作中断恢复（F18.4）",
            border_style="yellow",
        )
    )

    if not interactive:
        return DraftResumeDecision(has_draft=True, draft=draft, action="resume")

    # 交互式询问
    try:
        choice = Prompt.ask(
            "如何处理草稿？",
            choices=["resume", "discard", "skip"],
            default="resume",
            console=cons,
        )
    except (EOFError, OSError):
        # 非交互环境默认 resume
        return DraftResumeDecision(has_draft=True, draft=draft, action="resume")

    if choice == "discard":
        dm.clear_draft()
        return DraftResumeDecision(has_draft=True, draft=draft, action="discard")
    elif choice == "skip":
        return DraftResumeDecision(has_draft=True, draft=draft, action="none")
    else:
        return DraftResumeDecision(has_draft=True, draft=draft, action="resume")


# ============================================================
# F18.1 LLM 失败兜底
# ============================================================
def handle_llm_failure(
    error: Exception,
    context: str,
    project_dir: Path,
    console: Console | None = None,
) -> str:
    """LLM 调用重试耗尽后的兜底处理（F18.1）

    Args:
        error: LLM 错误
        context: 调用上下文描述（如 "M5 章节生成"）
        project_dir: 项目目录（用于保存进度）
        console: rich Console

    Returns:
        用户决策："retry" | "abort" | "skip"
    """
    cons = console or Console()
    cons.print(
        Panel(
            f"[bold red]LLM 调用失败[/bold red]\n\n"
            f"上下文：{context}\n"
            f"错误：{error}\n\n"
            f"进度已保存，可选择：\n"
            f"  - retry: 重试（可能仍失败）\n"
            f"  - abort: 终止当前操作\n"
            f"  - skip: 跳过本次调用",
            title="错误恢复（F18.1）",
            border_style="red",
        )
    )
    try:
        choice = Prompt.ask(
            "请选择",
            choices=["retry", "abort", "skip"],
            default="retry",
            console=cons,
        )
    except (EOFError, OSError):
        return "abort"
    return choice
