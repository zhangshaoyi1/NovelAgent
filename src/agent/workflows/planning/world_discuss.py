"""世界观讨论工作流（M1.5）

职责：world.md 生成后、进入脉络讨论前的讨论环节。作者就世界观设定与 Agent
对谈（追问 / 质疑 / 补充），讨论记录追加到 world_discussion.md；可把讨论结论
合并回 world.md（--apply，覆盖正文、保留 frontmatter）。

与 M2 脉络讨论的职责边界：
- M2 讨论的是「故事脉络」，产出 discussion.md，并驱动 DISCUSSING → ARCHITECTING。
- 本工作流只围绕「世界观设定」对谈，不驱动状态机；应用结论由作者显式触发。

用法：
    novel-agent world-discuss --dir <project> --message "金手指再强一点"
    novel-agent world-discuss --dir <project> --apply          # 把讨论结论合并进 world.md
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console

from agent.client.gateway_adapter import create_gateway, chat_creative, chat_utility
from llmagent.gateway import Gateway
from agent.core.story.setting_manager import SettingManager
from agent.core.infra.prompt_manager import pm

# 提示词里引用的讨论记录上限（字符），控制上下文长度
_MAX_LOG_CHARS = 6000
_MAX_WORLD_CHARS = 6000


@dataclass
class WorldDiscussResult:
    """世界观讨论单次执行结果"""

    agent_reply: str
    applied: bool
    discussion_file: Path
    world_file: Path | None = None
    history: list[tuple[str, str]] = field(default_factory=list)


class WorldDiscussWorkflow:
    """世界观讨论工作流（不接状态机，world.md 存在即可用）"""

    def __init__(
        self,
        project_dir: Path,
        llm_client: Gateway | None = None,
        setting_manager: SettingManager | None = None,
        console: Console | None = None,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.llm = llm_client or create_gateway()
        self.sm = setting_manager or SettingManager(self.project_dir)
        self.console = console or Console()
        self.discussion_file = self.project_dir / "world_discussion.md"

    def run(self, message: str = "", apply: bool = False) -> WorldDiscussResult:
        """执行一次世界观讨论

        Args:
            message: 作者本轮发言；为空且 apply=True 时仅按既有记录合并结论
            apply: 是否把讨论结论合并进 world.md

        Returns:
            WorldDiscussResult
        """
        world_data = self.sm.load_world()
        if not world_data["exists"]:
            raise RuntimeError("world.md 不存在，请先运行 /start 生成世界观")
        world_info = self._world_info(world_data)
        log = self._read_log()

        agent_reply = ""
        if message.strip():
            agent_reply = self._agent_respond(world_info, log, message.strip())
            self._append_round(message.strip(), agent_reply)
            log = self._read_log()
            self.console.print(f"\n[bold cyan]Agent[/bold cyan]：{agent_reply}")

        world_file = None
        if apply:
            world_file = self._apply_to_world(world_data, world_info, log)
            self.console.print(
                f"\n[bold green]✓ 讨论结论已合并进世界观[/bold green]：{world_file}"
            )

        return WorldDiscussResult(
            agent_reply=agent_reply,
            applied=apply,
            discussion_file=self.discussion_file,
            world_file=world_file,
        )

    # ------ 内部实现 ------
    def _world_info(self, world_data: dict[str, Any]) -> dict[str, str]:
        metadata = world_data.get("metadata", {})
        return {
            "title": str(metadata.get("title", "")),
            "genre_label": str(metadata.get("genre_label", "")),
            "content": world_data.get("content", "")[:_MAX_WORLD_CHARS],
        }

    def _read_log(self) -> str:
        if not self.discussion_file.exists():
            return ""
        return self.discussion_file.read_text(encoding="utf-8", errors="replace")[
            -_MAX_LOG_CHARS:
        ]

    def _agent_respond(
        self, world_info: dict[str, str], log: str, message: str
    ) -> str:
        user_prompt = pm.get("m2.world_discuss").render_user(
            title=world_info["title"],
            world_content=world_info["content"],
            discussion_log=log or "（讨论刚开始，尚无历史记录）",
            author_message=message,
        )
        messages = [
            {
                "role": "system",
                "content": pm.get("m2.world_discuss").render_system(
                    genre=world_info.get("genre_label", "")
                ),
            },
            {"role": "user", "content": user_prompt},
        ]
        resp = chat_creative(
            self.llm,
            messages=messages,
            temperature=0.7,
            max_tokens=800,
            enable_thinking=False,
        )
        return resp.strip()

    def _append_round(self, author_message: str, agent_reply: str) -> None:
        header = ""
        if not self.discussion_file.exists():
            header = (
                "# 世界观讨论记录\n\n"
                f"- 开始时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                "## 讨论过程\n\n"
            )
        round_block = (
            f"### {datetime.now().strftime('%m-%d %H:%M')}\n\n"
            f"**作者**：{author_message}\n\n"
            f"**Agent**：{agent_reply}\n\n"
        )
        with self.discussion_file.open("a", encoding="utf-8") as f:
            if header:
                f.write(header)
            f.write(round_block)

    def _apply_to_world(
        self, world_data: dict[str, Any], world_info: dict[str, str], log: str
    ) -> Path:
        """按讨论结论重写 world.md 正文（保留 frontmatter 元数据）"""
        if not log.strip():
            raise RuntimeError("尚无讨论记录，请先发送至少一条讨论内容再应用")
        user_prompt = pm.get("m2.world_apply").render_user(
            title=world_info["title"],
            world_content=world_info["content"],
            discussion_log=log,
        )
        messages = [
            {
                "role": "system",
                "content": pm.get("m2.world_apply").render_system(
                    genre=world_info.get("genre_label", "")
                ),
            },
            {"role": "user", "content": user_prompt},
        ]
        resp = chat_utility(
            self.llm,
            messages=messages,
            temperature=0.3,
            max_tokens=4000,
            enable_thinking=False,
        )
        new_content = resp.strip()
        if not new_content:
            raise RuntimeError("模型未返回有效的世界观正文，本次未修改 world.md")
        metadata = dict(world_data.get("metadata", {}))
        path = self.sm.save_world(metadata, new_content)
        self.sm.append_revision_log("世界观讨论：按讨论结论合并更新 world.md 正文")
        return path
