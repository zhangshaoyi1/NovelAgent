"""M14 故事架构生成与确认门禁工作流 ★门禁

职责：把 M2 发散讨论的成果收敛为可执行的故事架构，并经用户显式确认后才解锁下游。

流程：
    1. generate()：读 world.md + discussion.md → LLM 生成八维度架构
       → 渲染 architecture.md（confirmed: false, version: 1）
    2. iterate(feedback)：基于用户修改意见迭代架构（version +1, confirmed 重置 false）
    3. confirm()：写 confirmed: true + confirmed_at → 状态转换 ARCHITECTING → ARCH_CONFIRMED

门禁：confirmed == true 是 M3/M5 执行的必要条件，由 check_confirmed() 提供。

状态转换：ARCHITECTING → ARCH_CONFIRMED
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import frontmatter
from jinja2 import Environment, FileSystemLoader, select_autoescape
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from agent.core.llm_client import LLMClient
from agent.core.setting_manager import SettingManager
from agent.core.state_machine import Event, State, StateMachine
from agent.core.genre_pack import first_genre
from agent.prompts import (
    M14_ITERATE_SYSTEM_PROMPT,
    M14_ITERATE_USER_PROMPT_TEMPLATE,
    M14_SYSTEM_PROMPT,
    M14_USER_PROMPT_TEMPLATE,
)
from agent.utils import parse_llm_json

# 模板目录
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

# 架构八维度（与 prompt JSON 字段对齐）
ARCHITECTURE_DIMENSIONS = [
    "story_core",          # 故事内核（一句话）
    "protagonist_triple",  # 主角三要素（是谁/要什么/阻碍）
    "main_plot",           # 主线脉络（起承转合）
    "sublines_preview",    # 主要支线预判
    "conflict_nodes",      # 关键冲突节点
    "theme",               # 主题思想
    "ending",              # 预期结局走向
    "emotional_tone",      # 情感基调
]


@dataclass
class M14GenerateResult:
    """generate/iterate 的执行结果"""

    architecture_file: Path
    version: int
    confirmed: bool
    architecture: dict[str, Any]  # 解析后的八维度 dict


@dataclass
class M14ConfirmResult:
    """confirm 的执行结果"""

    architecture_file: Path
    confirmed: bool
    confirmed_at: str
    version: int
    unlocked_stages: list[str] = field(default_factory=list)


class M14ArchitectureWorkflow:
    """M14 故事架构生成与确认门禁工作流"""

    # 确认后解锁的下游阶段（用于确认前预览，轻量防误）
    UNLOCKED_STAGES = ["M3 大纲生成", "M4 角色设计", "M5 章节写作"]

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
        self.architecture_file = self.project_dir / "architecture.md"
        self.jinja_env = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            autoescape=select_autoescape(disabled_extensions=("j2",)),
            keep_trailing_newline=True,
        )

    # ============================================================
    # 生成初稿
    # ============================================================
    def generate(self) -> M14GenerateResult:
        """生成故事架构初稿

        读取 world.md + discussion.md，调用 LLM 生成八维度架构，
        渲染 architecture.md（confirmed: false, version: 1）。

        Raises:
            RuntimeError: 状态不符 / world.md 不存在
        """
        self.state_machine.load()
        if self.state_machine.state != State.ARCHITECTING:
            raise RuntimeError(
                f"当前状态 {self.state_machine.state.value} 不允许生成架构，"
                f"需处于 ARCHITECTING 状态"
            )

        world_data = self.sm.load_world()
        if not world_data["exists"]:
            raise RuntimeError("world.md 不存在，请先运行 M1 配置")

        discussion_text = self._load_discussion()
        world_info = self._extract_world_info(world_data)

        self.console.print("\n[cyan]正在生成故事架构...[/cyan]")
        architecture = self._llm_generate_architecture(world_info, discussion_text)

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._save_architecture(
            title=world_info["title"],
            architecture=architecture,
            confirmed=False,
            confirmed_at="",
            version=1,
            created_at=now,
            updated_at=now,
        )

        self._present_architecture(architecture, version=1, confirmed=False)

        self.console.print(
            f"\n[bold green]✓ 架构初稿已生成[/bold green]：{self.architecture_file}"
        )
        self.console.print(
            "[dim]下一步：审阅后使用 /confirm-architecture 确认，"
            "或再次 /architecture --feedback \"...\" 迭代修订。[/dim]"
        )

        return M14GenerateResult(
            architecture_file=self.architecture_file,
            version=1,
            confirmed=False,
            architecture=architecture,
        )

    # ============================================================
    # 迭代修订
    # ============================================================
    def iterate(self, feedback: str) -> M14GenerateResult:
        """基于用户反馈迭代架构

        Args:
            feedback: 作者修改意见（自然语言）

        Raises:
            RuntimeError: architecture.md 不存在 / 状态不符
        """
        self.state_machine.load()
        if self.state_machine.state != State.ARCHITECTING:
            raise RuntimeError(
                f"当前状态 {self.state_machine.state.value} 不允许迭代架构，"
                f"需处于 ARCHITECTING 状态"
            )

        if not self.architecture_file.exists():
            raise RuntimeError(
                "architecture.md 不存在，请先运行 /architecture 生成初稿"
            )

        post = frontmatter.load(self.architecture_file)
        current_version = int(post.metadata.get("version", 1))
        title = post.metadata.get("title", "")
        # 从 frontmatter 读取完整架构 JSON（迭代的基础）
        current_arch = post.metadata.get("architecture", {}) or {}
        if not current_arch:
            # 兼容旧文件：无 architecture 字段时降级为空结构
            current_arch = self._empty_architecture()

        self.console.print("\n[cyan]正在根据反馈修订架构...[/cyan]")
        new_arch = self._llm_iterate_architecture(title, current_arch, feedback)

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        created_at = post.metadata.get("created_at", now)
        new_version = current_version + 1
        self._save_architecture(
            title=title,
            architecture=new_arch,
            confirmed=False,
            confirmed_at="",
            version=new_version,
            created_at=created_at,
            updated_at=now,
        )

        self._present_architecture(new_arch, version=new_version, confirmed=False)
        self.console.print(
            f"\n[bold green]✓ 架构已迭代到 v{new_version}[/bold green]："
            f"{self.architecture_file}"
        )
        self.console.print(
            "[dim]下一步：审阅后使用 /confirm-architecture 确认，"
            "或继续 /architecture --feedback \"...\" 迭代。[/dim]"
        )

        return M14GenerateResult(
            architecture_file=self.architecture_file,
            version=new_version,
            confirmed=False,
            architecture=new_arch,
        )

    # ============================================================
    # 确认门禁
    # ============================================================
    def confirm(self) -> M14ConfirmResult:
        """确认故事架构

        写入 confirmed: true + confirmed_at，状态转换 ARCHITECTING → ARCH_CONFIRMED。
        确认前显示"将解锁下游 X 个阶段"预览作为轻量防误。

        Raises:
            RuntimeError: architecture.md 不存在 / 已确认 / 状态不符
        """
        self.state_machine.load()
        if self.state_machine.state != State.ARCHITECTING:
            raise RuntimeError(
                f"当前状态 {self.state_machine.state.value} 不允许确认架构，"
                f"需处于 ARCHITECTING 状态"
            )

        if not self.architecture_file.exists():
            raise RuntimeError(
                "architecture.md 不存在，请先运行 /architecture 生成初稿"
            )

        post = frontmatter.load(self.architecture_file)
        if post.metadata.get("confirmed") is True:
            raise RuntimeError("架构已确认，如需修改请先 /revise-architecture")

        # 防误预览
        self.console.print(
            Panel(
                "确认后将解锁以下下游阶段：\n"
                + "\n".join(f"  • {s}" for s in self.UNLOCKED_STAGES),
                title="[bold]确认架构[/bold]",
                border_style="yellow",
            )
        )
        # 交互式二次确认（confirm_yes=True 时跳过，用于测试/批处理）
        if not self._confirm_yes:
            yes = Confirm.ask("[bold]确认锁定当前架构？[/bold]", default=False)
            if not yes:
                self.console.print("[yellow]已取消确认，可继续迭代。[/yellow]")
                return M14ConfirmResult(
                    architecture_file=self.architecture_file,
                    confirmed=False,
                    confirmed_at="",
                    version=int(post.metadata.get("version", 1)),
                    unlocked_stages=[],
                )

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        post.metadata["confirmed"] = True
        post.metadata["confirmed_at"] = now
        self.architecture_file.write_text(frontmatter.dumps(post), encoding="utf-8")

        # 状态转换
        self.state_machine.transition(Event.CONFIRM_ARCHITECTURE)
        self.state_machine.save()

        version = int(post.metadata.get("version", 1))
        self.console.print(
            f"\n[bold green]✓ 架构已确认[/bold green]（v{version}，{now}）"
        )
        self.console.print(
            f"[dim]已解锁：{', '.join(self.UNLOCKED_STAGES)}[/dim]"
        )
        self.console.print("[dim]下一步：使用 /outline 生成大纲。[/dim]")

        return M14ConfirmResult(
            architecture_file=self.architecture_file,
            confirmed=True,
            confirmed_at=now,
            version=version,
            unlocked_stages=list(self.UNLOCKED_STAGES),
        )

    # ============================================================
    # 门禁检查（供 M3/M5 调用）
    # ============================================================
    @staticmethod
    def check_confirmed(project_dir: Path | str) -> bool:
        """门禁检查：architecture.md 的 confirmed 字段（向后兼容包装）

        T-4 起实际逻辑已上提至 ``agent.core.confirmation.is_architecture_confirmed``，
        本静态方法保留以兼容既有调用方；新代码请直接使用 ``is_architecture_confirmed``。
        """
        from agent.core.confirmation import is_architecture_confirmed

        return is_architecture_confirmed(project_dir)

    # ============================================================
    # 内部工具
    # ============================================================
    def _load_discussion(self) -> str:
        """加载 discussion.md 全文（不存在则返回空串）"""
        discussion_file = self.project_dir / "discussion.md"
        if not discussion_file.exists():
            return ""
        return discussion_file.read_text(encoding="utf-8")

    @staticmethod
    def _extract_world_info(world_data: dict[str, Any]) -> dict[str, str]:
        """从 world.md 提取关键信息供 prompt 使用"""
        metadata = world_data.get("metadata", {})
        content = world_data.get("content", "")
        synopsis = ""
        if "## 故事简介" in content:
            parts = content.split("## 故事简介", 1)
            if len(parts) > 1:
                synopsis = parts[1].split("##", 1)[0].strip()[:500]
        style = metadata.get("style", {}) or {}
        return {
            "title": metadata.get("title", ""),
            "scope": metadata.get("scope", ""),
            "genre": first_genre(metadata),
            "tone": style.get("tone", "") if isinstance(style, dict) else str(style),
            "synopsis": synopsis,
        }

    def _llm_generate_architecture(
        self, world_info: dict[str, str], discussion: str
    ) -> dict[str, Any]:
        """调用 LLM 生成八维度架构"""
        user_prompt = M14_USER_PROMPT_TEMPLATE.format(
            title=world_info["title"],
            scope=world_info["scope"],
            tone=world_info["tone"],
            discussion=discussion or world_info["synopsis"] or "（无讨论纪要）",
        )
        resp = self.llm.chat_creative(
            messages=[
                {"role": "system", "content": M14_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.7,
            max_tokens=2500,
            enable_thinking=False,
        )
        try:
            return parse_llm_json(resp.text)
        except ValueError:
            # JSON 解析失败，降级为纯文本填充 story_core
            return {
                "story_core": resp.text[:200],
                "protagonist_triple": {"who": "", "want": "", "obstacle": ""},
                "main_plot": {"beginning": "", "development": "", "twist": "", "resolution": ""},
                "sublines_preview": "",
                "conflict_nodes": "",
                "theme": "",
                "ending": "",
                "emotional_tone": "",
                "synopsis": resp.text[:200],
            }

    def _llm_iterate_architecture(
        self, title: str, current_arch: dict[str, Any], feedback: str
    ) -> dict[str, Any]:
        """调用 LLM 基于反馈迭代架构"""
        user_prompt = M14_ITERATE_USER_PROMPT_TEMPLATE.format(
            title=title,
            current_architecture=json.dumps(current_arch, ensure_ascii=False, indent=2),
            feedback=feedback,
        )
        resp = self.llm.chat_creative(
            messages=[
                {"role": "system", "content": M14_ITERATE_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.7,
            max_tokens=2500,
            enable_thinking=False,
        )
        try:
            new_arch = parse_llm_json(resp.text)
            # 合并：迭代结果可能只返回修改字段，用旧值兜底
            merged = dict(current_arch)
            merged.update(new_arch)
            return merged
        except ValueError:
            return current_arch

    @staticmethod
    def _empty_architecture() -> dict[str, Any]:
        """空架构结构（兼容旧文件降级用）"""
        return {
            "story_core": "",
            "protagonist_triple": {"who": "", "want": "", "obstacle": ""},
            "main_plot": {"beginning": "", "development": "", "twist": "", "resolution": ""},
            "sublines_preview": "",
            "conflict_nodes": "",
            "theme": "",
            "ending": "",
            "emotional_tone": "",
            "synopsis": "",
        }

    def _save_architecture(
        self,
        title: str,
        architecture: dict[str, Any],
        confirmed: bool,
        confirmed_at: str,
        version: int,
        created_at: str,
        updated_at: str,
    ) -> None:
        """渲染正文 + 构造 frontmatter（含架构 JSON）+ 写入 architecture.md

        架构 JSON 存入 frontmatter 的 architecture 字段，
        供 iterate() 读取作为迭代基础，避免 LLM 从零重新生成。
        """
        template = self.jinja_env.get_template("architecture.md.j2")
        content = template.render(
            title=title,
            story_core=architecture.get("story_core", ""),
            protagonist_triple=architecture.get("protagonist_triple", {}) or {},
            main_plot=architecture.get("main_plot", {}) or {},
            sublines_preview=architecture.get("sublines_preview", ""),
            conflict_nodes=architecture.get("conflict_nodes", ""),
            theme=architecture.get("theme", ""),
            ending=architecture.get("ending", ""),
            emotional_tone=architecture.get("emotional_tone", ""),
            synopsis=architecture.get("synopsis", ""),
        )
        post = frontmatter.Post(
            content,
            title=title,
            confirmed=confirmed,
            confirmed_at=confirmed_at,
            version=version,
            created_at=created_at,
            updated_at=updated_at,
            # 完整架构 JSON，供迭代读取
            architecture=architecture,
        )
        self.architecture_file.write_text(frontmatter.dumps(post), encoding="utf-8")

    def _present_architecture(
        self, architecture: dict[str, Any], version: int, confirmed: bool
    ) -> None:
        """显示架构预览给用户"""
        status = "[green]已确认[/green]" if confirmed else "[yellow]待确认[/yellow]"
        self.console.print(
            Panel(
                f"[bold]故事架构 v{version}[/bold] · {status}\n\n"
                f"[bold]故事内核[/bold]：{architecture.get('story_core', '')}\n\n"
                f"[bold]主角三要素[/bold]：\n"
                f"  是谁：{(architecture.get('protagonist_triple', {}) or {}).get('who', '')}\n"
                f"  想要什么：{(architecture.get('protagonist_triple', {}) or {}).get('want', '')}\n"
                f"  阻碍：{(architecture.get('protagonist_triple', {}) or {}).get('obstacle', '')}\n\n"
                f"[bold]主题[/bold]：{architecture.get('theme', '')}\n"
                f"[bold]结局[/bold]：{architecture.get('ending', '')}",
                title="[bold]architecture.md 预览[/bold]",
                border_style="cyan",
            )
        )

    # 二次确认开关（测试/批处理场景可置 True 跳过交互）
    _confirm_yes: bool = False

    def with_confirm_yes(self, yes: bool = True) -> "M14ArchitectureWorkflow":
        """链式设置：是否跳过交互式二次确认"""
        self._confirm_yes = yes
        return self
