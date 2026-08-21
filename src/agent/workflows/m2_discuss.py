"""M2 脉络讨论工作流（发散）

职责：多轮对谈，追问、质疑、补充灵感，禁止直接跳到生成。
产出：discussion.md
状态转换：DISCUSSING → ARCHITECTING

交互流程：
    1. 读取 world.md 获取基本信息
    2. LLM 提出第一个问题
    3. 用户回答
    4. LLM 追问/补充灵感
    5. 循环，直到用户输入 /next 或达到 max_rounds
    6. LLM 整理讨论纪要，生成 discussion.md
    7. 状态转换 DISCUSSING → ARCHITECTING
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from agent.core.llm_client import LLMClient
from agent.core.setting_manager import SettingManager
from agent.core.state_machine import Event, State, StateMachine
from agent.core.genre_pack import first_genre
from agent.prompts import M2_SYSTEM_PROMPT, M2_USER_PROMPT_TEMPLATE


# 退出命令
EXIT_COMMANDS = {"/next", "/done", "/exit", "/quit"}
# 最大默认轮次
DEFAULT_MAX_ROUNDS = 10


@dataclass
class ChatTurn:
    """一轮对话"""

    role: str  # "assistant" | "user"
    content: str


@dataclass
class M2Input:
    """M2 输入"""

    max_rounds: int = DEFAULT_MAX_ROUNDS
    # 非交互模式：预设的用户回答列表
    preset_answers: list[str] = field(default_factory=list)


@dataclass
class M2Result:
    """M2 执行结果"""

    discussion_file: Path
    rounds: int
    summary: str
    history: list[ChatTurn] = field(default_factory=list)


class M2DiscussWorkflow:
    """M2 脉络讨论工作流"""

    def __init__(
        self,
        project_dir: Path,
        llm_client: LLMClient | None = None,
        setting_manager: SettingManager | None = None,
        state_machine: StateMachine | None = None,
        console: Console | None = None,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.llm = llm_client or LLMClient()
        self.sm = setting_manager or SettingManager(self.project_dir)
        self.state_machine = state_machine or StateMachine(self.project_dir)
        self.console = console or Console()
        self.discussion_file = self.project_dir / "discussion.md"

    def run(self, user_input: M2Input | None = None) -> M2Result:
        """运行 M2 工作流

        Args:
            user_input: 预设输入（preset_answers 非空时为非交互模式）

        Returns:
            M2Result
        """
        user_input = user_input or M2Input()
        self.state_machine.load()

        # 状态校验
        if self.state_machine.state not in (State.DISCUSSING, State.ARCHITECTING):
            raise RuntimeError(
                f"当前状态 {self.state_machine.state.value} 不允许讨论，"
                f"需处于 DISCUSSING 状态"
            )

        # 读取 world.md
        world_data = self.sm.load_world()
        if not world_data["exists"]:
            raise RuntimeError("world.md 不存在，请先运行 M1 配置")

        world_info = self._extract_world_info(world_data)
        self.console.print(
            Panel(
                f"[bold]M2 脉络讨论[/bold]\n标题：{world_info['title']}\n"
                f"故事核心：{world_info['story_core']}\n\n"
                f"[dim]输入 {EXIT_COMMANDS} 之一结束讨论[/dim]",
                border_style="cyan",
            )
        )

        # 多轮对话
        history: list[ChatTurn] = []
        interactive = not user_input.preset_answers
        answer_idx = 0

        for round_num in range(1, user_input.max_rounds + 1):
            # LLM 生成回应/提问
            assistant_text = self._llm_respond(world_info, history)
            history.append(ChatTurn(role="assistant", content=assistant_text))
            self.console.print(f"\n[bold cyan]Agent[/bold cyan]：{assistant_text}")

            # 获取用户输入
            if interactive:
                user_text = Prompt.ask("\n[bold green]你[/bold green]")
            else:
                if answer_idx >= len(user_input.preset_answers):
                    break
                user_text = user_input.preset_answers[answer_idx]
                answer_idx += 1
                self.console.print(f"\n[bold green]你[/bold green]：{user_text}")

            # 检查退出命令
            if user_text.strip().lower() in EXIT_COMMANDS:
                self.console.print("\n[yellow]结束讨论，整理纪要...[/yellow]")
                break

            history.append(ChatTurn(role="user", content=user_text))

        # 生成讨论纪要
        self.console.print("\n[cyan]正在整理讨论纪要...[/cyan]")
        summary = self._summarize_discussion(world_info, history)
        # 计轮：每条 assistant 消息计 1 轮（用户可能未回复即退出）
        rounds = sum(1 for t in history if t.role == "assistant")
        content = self._render_discussion_md(world_info, history, summary, rounds)
        self.discussion_file.write_text(content, encoding="utf-8")

        # 状态转换
        if self.state_machine.state == State.DISCUSSING:
            self.state_machine.transition(Event.GENERATE_ARCHITECTURE)
            self.state_machine.save()

        self.console.print(
            f"\n[bold green]✓ 讨论纪要已生成[/bold green]：{self.discussion_file}"
        )
        self.console.print(
            f"[dim]共 {rounds} 轮对话。下一步：使用 /confirm-architecture 生成并确认故事架构。[/dim]"
        )

        return M2Result(
            discussion_file=self.discussion_file,
            rounds=rounds,
            summary=summary,
            history=history,
        )

    def _extract_world_info(self, world_data: dict[str, Any]) -> dict[str, str]:
        """从 world.md 提取关键信息"""
        metadata = world_data.get("metadata", {})
        content = world_data.get("content", "")
        # 从正文提取故事简介（## 故事简介 之后的段落）
        story_synopsis = ""
        if "## 故事简介" in content:
            parts = content.split("## 故事简介", 1)
            if len(parts) > 1:
                # 取下一个 ## 之前的内容
                synopsis = parts[1].split("##", 1)[0].strip()
                story_synopsis = synopsis[:300]

        return {
            "title": metadata.get("title", ""),
            "scope": metadata.get("scope", ""),
            "genre": first_genre(metadata),
            "story_core": story_synopsis or metadata.get("title", ""),
            "style": str(metadata.get("style", {})),
        }

    def _llm_respond(
        self, world_info: dict[str, str], history: list[ChatTurn]
    ) -> str:
        """LLM 根据上下文生成回应/提问"""
        user_prompt = M2_USER_PROMPT_TEMPLATE.format(
            title=world_info["title"],
            story_core=world_info["story_core"],
            user_input=self._format_history_for_prompt(history),
        )

        messages = [
            {"role": "system", "content": M2_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        # 把对话历史作为多轮消息传入
        for turn in history[-10:]:  # 最多传最近 10 轮，控制上下文
            messages.append({"role": turn.role, "content": turn.content})

        resp = self.llm.chat_creative(
            messages=messages,
            temperature=0.7,
            max_tokens=500,
            enable_thinking=False,
        )
        return resp.text.strip()

    @staticmethod
    def _format_history_for_prompt(history: list[ChatTurn]) -> str:
        """把对话历史格式化为文本"""
        if not history:
            return "（讨论刚开始，请提出第一个关键问题）"
        lines = []
        for turn in history[-10:]:
            speaker = "Agent" if turn.role == "assistant" else "作者"
            lines.append(f"【{speaker}】{turn.content}")
        return "\n".join(lines)

    def _summarize_discussion(
        self, world_info: dict[str, str], history: list[ChatTurn]
    ) -> str:
        """LLM 整理讨论纪要的关键结论"""
        if not history:
            return "（无讨论内容）"

        summary_prompt = (
            f"以下是关于小说《{world_info['title']}》的讨论纪要，"
            f"请整理出 3-5 条关键结论，用 markdown 列表形式输出：\n\n"
            + self._format_history_for_prompt(history)
        )
        resp = self.llm.chat_utility(
            messages=[{"role": "user", "content": summary_prompt}],
            max_tokens=500,
            enable_thinking=False,
        )
        return resp.text.strip()

    def _render_discussion_md(
        self,
        world_info: dict[str, str],
        history: list[ChatTurn],
        summary: str,
        rounds: int,
    ) -> str:
        """渲染 discussion.md"""
        lines = [
            "---",
            f'title: "{world_info["title"]}"',
            f'created_at: "{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}"',
            f"rounds: {rounds}",
            "---",
            "",
            f"# 脉络讨论纪要 · {world_info['title']}",
            "",
            "## 基本信息",
            "",
            f"- 标题：{world_info['title']}",
            f"- 故事核心：{world_info['story_core']}",
            f"- 体量：{world_info['scope']}",
            "",
            "## 讨论过程",
            "",
        ]

        round_num = 0
        for turn in history:
            if turn.role == "assistant":
                round_num += 1
                lines.append(f"### 第 {round_num} 轮")
                lines.append("")
                lines.append(f"**Agent**：{turn.content}")
            else:
                lines.append(f"**作者**：{turn.content}")
            lines.append("")

        lines.append("## 讨论总结")
        lines.append("")
        lines.append(summary)
        lines.append("")

        return "\n".join(lines)
