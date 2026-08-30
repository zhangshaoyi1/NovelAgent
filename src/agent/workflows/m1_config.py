"""M1 启动配置工作流

步骤：
    1. 收集基本信息（标题/体量/题材/故事核心）
    2. 风格配置问卷
    3. 加载修仙题材包模板（境界体系等冻结字段初始值）
    4. 调用 LLM 生成世界观
    5. 渲染 world.md 模板
    6. 保存 world.md（含 frozen_fields）
    7. 显示给用户审阅
    8. 状态转换 INIT → CONFIGURING

状态转换：INIT → CONFIGURING
"""

from __future__ import annotations

from agent.core.engine.workflow_registry import workflow

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from agent import __version__
from agent.client import LLMClient
from agent.core.registry.genre_pack import GenrePackRegistry
from agent.core.registry.genre_merger import GenreMerger, save_conflicts
from agent.core.story.setting_manager import SettingManager
from agent.core.engine.state_machine import Event, State, StateMachine
from agent.prompts import M1_SYSTEM_PROMPT, M1_USER_PROMPT_TEMPLATE
from agent.utils import parse_llm_json
from agent.core.infra.hook_dispatcher import dispatch_genre_hooks
from agent.core.story.volume import (
    MAX_CHAPTER_LENGTH,
    MIN_CHAPTER_LENGTH,
    SCOPE_LABELS,
    VALID_SCOPES,
    describe_scope,
    validate_custom,
)
from agent.workflows.qa_sync import format_qa_constraints

# 模板路径（题材包模板改由 GenrePackRegistry 动态加载，见 T-2）
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


def load_genre_template(project_dir: Path, genre: str, pack: Any) -> Path:
    """题材包 hook：将 world_template 写入 world.md（若不存在）

    供 SKILL.md hooks 调用（签名遵循共享约定 4）。M1 自身渲染流程仍由 run() 负责，
    此方法用于确保已有项目的 world.md 含题材模板（如 /load-genre 路径）。
    """
    project_dir = Path(project_dir)
    world_file = project_dir / "world.md"
    if not world_file.exists() and pack.world_template:
        world_file.write_text(pack.world_template, encoding="utf-8")
    return world_file


@dataclass
class M1Input:
    """M1 用户输入"""

    title: str = ""
    scope: str = "long"  # short | medium | long | mega | custom
    genres: list[str] | None = None  # 题材（可多选混搭）；None 时在 __post_init__ 归一化
    genre: str | None = None  # 向后兼容：显式传 genre 且未给 genres 时，折叠为单元素列表
    style: dict[str, Any] = field(default_factory=dict)
    story_core: str = ""
    total_words: int | None = None  # 自定义体量（scope=='custom'）目标总字数（字）
    chapter_length: int | None = None  # 自定义体量的单章字数（字）

    def __post_init__(self) -> None:
        # 归一化：genres 显式给定则用其值（忽略 genre）；否则由 genre 折叠；都缺省回退 [xiuxian]
        if self.genres is None:
            self.genres = [self.genre] if self.genre else ["xiuxian"]


@dataclass
class M1Result:
    """M1 执行结果"""

    world_file: Path
    metadata: dict[str, Any]
    content: str


@workflow("m1_config")
class M1ConfigWorkflow:
    """M1 启动配置工作流"""

    STYLE_DEFAULTS = {
        "tone": "热血",
        "pov": "第三人称限制",
        "rhythm": "快",
        "chapter_length": 3000,
        "info_density": "中",
        "banned_elements": [],
    }

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
        # Jinja2 环境
        self.jinja_env = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            autoescape=select_autoescape(disabled_extensions=("j2",)),
            keep_trailing_newline=True,
        )

    # ------ 入口 ------
    def run(self, user_input: M1Input | None = None) -> M1Result:
        """运行 M1 工作流

        Args:
            user_input: 预设输入（None 则交互式收集）

        Returns:
            M1Result
        """
        # 加载状态机
        self.state_machine.load()

        # 1. 收集输入
        if user_input is None:
            user_input = self._collect_input_interactive()
        else:
            user_input = self._fill_defaults(user_input)

        self.console.print(
            f"\n[bold green]已收集信息[/bold green]：{user_input.title} "
            f"({SCOPE_LABELS.get(user_input.scope, user_input.scope)})"
        )

        # 2. 加载并合并题材包模板（支持多题材混搭 + 冲突裁决）
        self._genre_registry = GenrePackRegistry()
        self._current_genres = list(user_input.genres)
        realm_system, merge_conflicts = self._load_merged_genre_template(self._current_genres)
        if merge_conflicts:
            self.console.print(
                f"[yellow]⚠ 检测到 {len(merge_conflicts)} 处题材设定冲突，"
                "已以主题材优先合并并记录，可用 /merge-genres 复核裁决。[/yellow]"
            )

        # 3. 调用 LLM 生成世界观
        self.console.print("\n[cyan]正在生成世界观...[/cyan]")
        world_data = self._generate_world(user_input)

        # 4. 渲染并保存 world.md
        metadata, content = self._render_world(user_input, world_data, realm_system)
        world_file = self.sm.save_world(metadata, content)

        # T-3：题材包 hooks 真实执行（注册 world 模板 / 题材层质量规则等）。
        # 在 save_world 之后分发：此时 world.md 已存在，load_genre_template hook
        # 会自动跳过写入，避免与 M1 自身渲染冲突（否则触发冻结字段校验）。
        # 所有选中题材均分发，使其质量规则等都能注册。
        for g in self._current_genres:
            try:
                genre_pack = self._genre_registry.load(g)
            except ValueError:
                continue
            dispatch_genre_hooks(self.project_dir, g, genre_pack)

        # 5. 显示给用户
        self._present(world_file, metadata, content)

        # 6. 状态转换
        if self.state_machine.state == State.INIT:
            self.state_machine.transition(Event.START)
            self.state_machine.transition(Event.DISCUSS)
        self.state_machine.save()

        return M1Result(world_file=world_file, metadata=metadata, content=content)

    # ------ 交互式收集 ------
    def _collect_input_interactive(self) -> M1Input:
        """交互式收集用户输入"""
        self.console.print(
            Panel("[bold]M1 启动配置[/bold]\n开新书，配置基础信息与风格", border_style="green")
        )

        title = Prompt.ask("[bold]小说标题[/bold]", default="我的修仙小说")

        scope = Prompt.ask(
            "[bold]体量[/bold]",
            choices=list(VALID_SCOPES),
            default="long",
        )
        total_words: int | None = None
        chapter_length: int | None = None
        if scope == "custom":
            total_words = int(
                Prompt.ask("[bold]目标总字数（字）[/bold]", default="1000000")
            )
            chapter_length = int(
                Prompt.ask(
                    "[bold]单章字数（字）[/bold]（推荐 2000-2500，范围 "
                    f"{MIN_CHAPTER_LENGTH}-{MAX_CHAPTER_LENGTH}）",
                    default="2500",
                )
            )
            err = validate_custom(total_words, chapter_length)
            if err:
                self.console.print(f"[yellow]提示：{err}（已使用你输入的值）[/yellow]")
        elif scope == "mega":
            chapter_length = int(
                Prompt.ask(
                    "[bold]单章字数（字）[/bold]（推荐 2000-2500，范围 "
                    f"{MIN_CHAPTER_LENGTH}-{MAX_CHAPTER_LENGTH}）",
                    default="2500",
                )
            )
            total_words = None  # mega 用内置区间（100万+）

        # 题材选项由 GenrePackRegistry 动态生成（T-2：去硬编码 xiuxian）
        genre_registry = GenrePackRegistry()
        available_genres = genre_registry.list_genres() or ["xiuxian"]
        genre_input = Prompt.ask(
            "[bold]题材[/bold]（可多选，逗号分隔；如 修仙,武侠）",
            default=available_genres[0],
        )
        genres = [
            g.strip() for g in genre_input.replace("，", ",").split(",") if g.strip()
        ]
        unknown = [g for g in genres if g not in available_genres]
        if unknown:
            self.console.print(f"[yellow]提示：未知题材 {unknown}，已忽略。[/yellow]")
            genres = [g for g in genres if g in available_genres]
        if not genres:
            genres = [available_genres[0]]

        story_core = Prompt.ask(
            "[bold]故事核心（一句话）[/bold]\n  例如：废柴少年偶得神秘传承，踏上逆天修仙路",
            default="废柴少年偶得神秘传承，踏上逆天修仙路",
        )

        # 风格问卷
        self.console.print("\n[bold]风格配置[/bold]")
        style = self._style_questionnaire()

        return M1Input(
            title=title,
            scope=scope,
            genres=genres,
            style=style,
            story_core=story_core,
            total_words=total_words,
            chapter_length=chapter_length,
        )

    def _style_questionnaire(self) -> dict[str, Any]:
        """风格配置问卷"""
        tone = Prompt.ask(
            "  文风",
            choices=["热血", "沉重", "治愈", "暗黑", "诙谐", "史诗"],
            default="热血",
        )
        pov = Prompt.ask(
            "  叙事视角",
            choices=["第一人称", "第三人称限制", "第三人称全知", "多视角"],
            default="第三人称限制",
        )
        rhythm = Prompt.ask(
            "  节奏",
            choices=["快", "中", "慢"],
            default="快",
        )
        chapter_length = int(
            Prompt.ask(
                "  章节字数",
                choices=["2000", "3000", "4000", "5000"],
                default="3000",
            )
        )
        info_density = Prompt.ask(
            "  信息密度",
            choices=["高", "中", "低"],
            default="中",
        )
        banned = Prompt.ask(
            "  禁用元素（逗号分隔，可留空）",
            default="",
        )
        banned_elements = [b.strip() for b in banned.split(",") if b.strip()]

        return {
            "tone": tone,
            "pov": pov,
            "rhythm": rhythm,
            "chapter_length": chapter_length,
            "info_density": info_density,
            "banned_elements": banned_elements,
        }

    def _fill_defaults(self, user_input: M1Input) -> M1Input:
        """为预设输入填充默认风格"""
        if not user_input.style:
            user_input.style = dict(self.STYLE_DEFAULTS)
        else:
            for k, v in self.STYLE_DEFAULTS.items():
                user_input.style.setdefault(k, v)
        # mega/custom 显式给定单章字数时，覆盖 style.chapter_length（驱动每章正文长度）
        if user_input.chapter_length and user_input.scope in ("mega", "custom"):
            user_input.style["chapter_length"] = user_input.chapter_length
        return user_input

    # ------ 题材包模板（多题材合并）------
    def _load_merged_genre_template(self, genres: list[str]) -> tuple[str, list]:
        """加载并合并多个题材包的 world-template（渐进式：仅 load 选中包）。

        Returns:
            (merged_world_template, conflicts) —— conflicts 为 GenreMerger 的
            MergeConflict 列表（已写入 .state/merge_conflicts.json 供复核裁决）。
        """
        registry = getattr(self, "_genre_registry", None) or GenrePackRegistry()
        packs = []
        for g in genres:
            try:
                packs.append(registry.load(g))
            except ValueError:
                self.console.print(f"[yellow]题材包不存在，已跳过：{g}[/yellow]")
                continue
        if not packs:
            return "", []
        if len(packs) == 1:
            return packs[0].world_template, []
        merger = GenreMerger()
        result = merger.merge(packs)
        try:
            save_conflicts(self.project_dir, result)
        except Exception:
            pass
        return result.world_template, result.conflicts

    # ------ LLM 生成 ------
    def _generate_world(self, user_input: M1Input) -> dict[str, Any]:
        """调用 LLM 生成世界观

        解析失败时重试一次（要求只输出纯 JSON）；两次均失败则明确抛错，
        绝不静默写入残缺世界观（否则作者输入的 story_core / 风格偏好会静默丢失）。

        Returns:
            包含 synopsis/worldview/power_system/factions/golden_finger 的 dict
        """
        style = user_input.style
        scope_desc = describe_scope(
            user_input.scope,
            total_words=user_input.total_words,
            chapter_length=user_input.chapter_length
            or style.get("chapter_length"),
        )
        user_prompt = M1_USER_PROMPT_TEMPLATE.format(
            title=user_input.title,
            scope=scope_desc,
            tone=style.get("tone", ""),
            pov=style.get("pov", ""),
            rhythm=style.get("rhythm", ""),
            info_density=style.get("info_density", ""),
            story_core=user_input.story_core,
        )

        qa_text = format_qa_constraints(self.project_dir, "world")
        if qa_text:
            user_prompt += qa_text

        system_prompt = M1_SYSTEM_PROMPT
        # 注意：dots3-note-prev 等模型在紧预算下会把长结构化输出截断/返回空，
        # 故采用「充足预算 + 递增重试 + 纯 JSON 强化」的重试策略。
        _budgets = (16384, 16384, 20480)
        for attempt in range(len(_budgets)):
            if attempt > 0:
                # 重试：强化「纯 JSON」约束，规避截断/多余文本导致的解析失败
                system_prompt = (
                    M1_SYSTEM_PROMPT
                    + "\n\n【重要】请只输出一个合法的 JSON 对象，"
                    "不要包含 ```json 代码块标记，不要输出任何解释性文字。"
                )
            resp = self.llm.chat_creative(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.8,
                max_tokens=_budgets[attempt],
                enable_thinking=False,  # 结构化输出不需要思考，加速生成
            )
            try:
                return parse_llm_json(resp.text)
            except ValueError:
                if attempt == len(_budgets) - 1:
                    raise RuntimeError(
                        "世界观生成结果无法解析为 JSON（可能被截断或格式异常），"
                        f"请重试。原始输出片段：{resp.text[:200]}"
                    )

    # ------ 渲染 ------
    def _render_world(
        self,
        user_input: M1Input,
        world_data: dict[str, Any],
        realm_system: str,
    ) -> tuple[dict[str, Any], str]:
        """渲染 world.md

        Returns:
            (metadata, content)
        """
        from datetime import datetime

        style = user_input.style
        # 中文题材标签（用于元数据与展示）
        try:
            _reg = GenrePackRegistry()
            genre_label = " / ".join(
                _reg.load(g).manifest.display_name for g in user_input.genres
            )
        except Exception:
            genre_label = " / ".join(user_input.genres)
        metadata = {
            "title": user_input.title,
            "scope": user_input.scope,
            "scope_label": SCOPE_LABELS.get(user_input.scope, user_input.scope),
            "scope_total_words": user_input.total_words,
            "scope_chapter_length": user_input.chapter_length
            or style.get("chapter_length"),
            "genres": user_input.genres,
            "genre_label": genre_label,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "format_version": "0.1.0",
            "frozen_fields": ["realm_system", "golden_finger_limits"],
            # 风格配置也存入 metadata 便于程序读取
            "style": style,
        }

        template = self.jinja_env.get_template("world.md.j2")
        content = template.render(
            title=user_input.title,
            scope=user_input.scope,
            genres=json.dumps(user_input.genres, ensure_ascii=False),
            genre_label=genre_label,
            created_at=metadata["created_at"],
            style=style,
            synopsis=world_data.get("synopsis", ""),
            worldview=world_data.get("worldview", ""),
            power_system=world_data.get("power_system", ""),
            realm_system=realm_system,
            factions=world_data.get("factions", ""),
            golden_finger=world_data.get("golden_finger", ""),
        )

        return metadata, content

    # ------ 呈现 ------
    def _present(self, world_file: Path, metadata: dict, content: str) -> None:
        """显示生成的 world.md 给用户"""
        self.console.print(
            Panel(
                f"[green]已生成[/green] {world_file}\n",
                title="[bold]world.md 初稿[/bold]",
                border_style="green",
            )
        )
        # 显示正文预览（截断）
        preview = content[:1000] + ("..." if len(content) > 1000 else "")
        self.console.print(preview)

        self.console.print(
            "\n[dim]境界体系与金手指上限已标记为冻结字段，"
            "修改需显式解冻命令。[/dim]"
        )
        self.console.print(
            "[dim]下一步：使用 /discuss 进入脉络讨论。[/dim]"
        )
