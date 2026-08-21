"""M3 大纲与简介生成工作流

基于已确认的 architecture.md（confirmed: true，由 M14 门禁保证），
生成 outline.md（故事简介 + 顶层支线任务列表表格 + 每条支线详情段），
并为每条支线创建占位 subline.md（剧集树分支的 subline 目录）。

流程：
    1. 门禁校验：check_confirmed() == true（否则拒绝）
    2. 读 world.md + architecture.md → LLM 生成 synopsis + sublines[]
    3. 渲染 outline.md（jinja）
    4. 为每条支线渲染 subline.md（jinja）→ sublines/S01_<name>/subline.md
    5. 状态转换 ARCH_CONFIRMED → OUTLINING

状态转换：ARCH_CONFIRMED → OUTLINING
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import frontmatter
from jinja2 import Environment, FileSystemLoader, select_autoescape
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agent.core.llm_client import LLMClient
from agent.core.setting_manager import SettingManager
from agent.core.state_machine import Event, State, StateMachine
from agent.core.confirmation import is_architecture_confirmed
from agent.core.genre_pack import first_genre
from agent.prompts import M3_SYSTEM_PROMPT, M3_USER_PROMPT_TEMPLATE
from agent.utils import parse_llm_json

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


@dataclass
class M3Result:
    """M3 执行结果"""

    outline_file: Path
    synopsis: str
    sublines: list[dict[str, Any]] = field(default_factory=list)
    subline_files: list[Path] = field(default_factory=list)


class M3OutlineWorkflow:
    """M3 大纲与简介生成工作流"""

    def __init__(
        self,
        project_dir: Path,
        llm_client: LLMClient | None = None,
        setting_manager: SettingManager | None = None,
        state_machine: StateMachine | None = None,
        console: Console | None = None,
        # G11：写作方法模板开关（默认开：project/method.md 存在即注入）
        method_enabled: bool = True,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.llm = llm_client or LLMClient()
        self.sm = setting_manager or SettingManager(self.project_dir)
        self.state_machine = state_machine or StateMachine(self.project_dir)
        self.console = console or Console()
        # G11：方法模板开关
        self.method_enabled = method_enabled
        self.outline_file = self.project_dir / "outline.md"
        self.jinja_env = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            autoescape=select_autoescape(disabled_extensions=("j2",)),
            keep_trailing_newline=True,
        )

    # ============================================================
    # 入口
    # ============================================================
    def run(self) -> M3Result:
        """运行 M3 工作流

        Raises:
            RuntimeError: 状态不符 / world.md 不存在 / 架构未确认
        """
        self.state_machine.load()
        if self.state_machine.state not in (State.ARCH_CONFIRMED, State.OUTLINING):
            raise RuntimeError(
                f"当前状态 {self.state_machine.state.value} 不允许生成大纲，"
                f"需处于 ARCH_CONFIRMED 状态"
            )

        world_data = self.sm.load_world()
        if not world_data["exists"]:
            raise RuntimeError("world.md 不存在，请先运行 M1 配置")

        # ★门禁：架构必须已确认
        if not is_architecture_confirmed(self.project_dir):
            raise RuntimeError(
                "故事架构尚未确认，请先运行 /confirm-architecture 后再生成大纲"
            )

        arch_data = self._load_architecture()
        world_info = self._extract_world_info(world_data)
        title = arch_data["title"]

        self.console.print("\n[cyan]正在生成大纲与支线任务...[/cyan]")
        outline = self._llm_generate_outline(world_info, arch_data)
        synopsis = outline.get("synopsis", "")
        sublines = outline.get("sublines", []) or []

        # 1) 渲染 outline.md
        self._render_and_save_outline(title, synopsis, sublines)

        # 2) 渲染每条支线的 subline.md
        subline_files = self._render_and_save_sublines(sublines)

        # 3) 呈现
        self._present(title, synopsis, sublines, subline_files)

        # 4) 状态转换
        if self.state_machine.state == State.ARCH_CONFIRMED:
            self.state_machine.transition(Event.GENERATE_OUTLINE)
            self.state_machine.save()

        return M3Result(
            outline_file=self.outline_file,
            synopsis=synopsis,
            sublines=sublines,
            subline_files=subline_files,
        )

    # ============================================================
    # 内部：读取数据
    # ============================================================
    def _load_architecture(self) -> dict[str, Any]:
        """从 architecture.md 读取元数据与架构 JSON

        Returns:
            {
                "title": ...,
                "confirmed": True,
                "architecture": {...八维度...},
            }
        """
        arch_file = self.project_dir / "architecture.md"
        if not arch_file.exists():
            raise RuntimeError("architecture.md 不存在，请先完成 M14")
        post = frontmatter.load(arch_file)
        return {
            "title": post.metadata.get("title", ""),
            "confirmed": bool(post.metadata.get("confirmed", False)),
            "architecture": post.metadata.get("architecture", {}) or {},
        }

    @staticmethod
    def _extract_world_info(world_data: dict[str, Any]) -> dict[str, str]:
        """从 world.md 提取信息"""
        metadata = world_data.get("metadata", {}) or {}
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

    # ============================================================
    # 内部：LLM 生成
    # ============================================================
    def _llm_generate_outline(
        self, world_info: dict[str, str], arch_data: dict[str, Any]
    ) -> dict[str, Any]:
        """调 LLM 生成 synopsis + sublines[]"""
        arch = arch_data["architecture"] or {}
        pt = arch.get("protagonist_triple", {}) or {}
        mp = arch.get("main_plot", {}) or {}

        user_prompt = M3_USER_PROMPT_TEMPLATE.format(
            title=arch_data["title"],
            scope=world_info.get("scope", ""),
            story_core=arch.get("story_core", ""),
            protagonist_who=pt.get("who", ""),
            protagonist_want=pt.get("want", ""),
            protagonist_obstacle=pt.get("obstacle", ""),
            main_plot_beginning=mp.get("beginning", ""),
            main_plot_development=mp.get("development", ""),
            main_plot_twist=mp.get("twist", ""),
            main_plot_resolution=mp.get("resolution", ""),
            sublines_preview=arch.get("sublines_preview", ""),
            conflict_nodes=arch.get("conflict_nodes", ""),
            theme=arch.get("theme", ""),
            ending=arch.get("ending", ""),
            emotional_tone=arch.get("emotional_tone", ""),
            arch_synopsis=arch.get("synopsis", ""),
        )
        # G11：写作方法模板注入（project/method.md 存在即追加；缺失/关闭不注入）
        if self.method_enabled:
            try:
                from agent.core.method_style import load_method_text
                from agent.prompts import G11_METHOD_INSTRUCTION_TEMPLATE

                method_text, _name = load_method_text(self.project_dir, enabled=True)
                if method_text:
                    user_prompt += G11_METHOD_INSTRUCTION_TEMPLATE.format(
                        method_text=method_text
                    )
            except Exception:  # noqa: BLE001 - 模板读取失败降级，不阻断大纲生成
                pass
        # P1 修复（2026-08-21）：JSON 解析失败自动重试一次（截断多为瞬时，重试常可恢复），
        # 重试仍失败才降级占位（不再让用户被迫手动重建大纲）。
        last_text = ""
        for attempt in range(2):
            resp = self.llm.chat_creative(
                messages=[
                    {"role": "system", "content": M3_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.75,
                max_tokens=3000,
                enable_thinking=False,
            )
            last_text = resp.text
            try:
                data = parse_llm_json(resp.text)
                # 兜底：sublines 为空时放一条空结构
                if not data.get("sublines"):
                    data["sublines"] = [self._empty_subline("未命名支线")]
                return data
            except ValueError:
                if attempt == 0:
                    self.console.print(
                        "[yellow]⚠ 大纲 JSON 解析失败，自动重试一次...[/yellow]"
                    )
        # JSON 解析失败（重试后）：降级为纯文本简介 + 空支线结构
        return {
            "synopsis": last_text[:300],
            "sublines": [self._empty_subline("架构拆解失败，请手动补充")],
        }

    @staticmethod
    def _empty_subline(name: str) -> dict[str, Any]:
        return {
            "subline_name": name,
            "goal": "",
            "characters": "",
            "conflicts": "",
            "constraints": "",
            "mainline_relation": "",
            "pressure_curve": {
                "setup": "", "conflict": "", "climax": "", "relief": ""
            },
        }

    # ============================================================
    # 内部：渲染 / 保存
    # ============================================================
    def _render_and_save_outline(
        self, title: str, synopsis: str, sublines: list[dict[str, Any]]
    ) -> None:
        """渲染并保存 outline.md"""
        template = self.jinja_env.get_template("outline.md.j2")
        content = template.render(
            title=title,
            synopsis=synopsis,
            sublines=sublines,
        )
        self.outline_file.write_text(content, encoding="utf-8")

    def _render_and_save_sublines(
        self, sublines: list[dict[str, Any]]
    ) -> list[Path]:
        """渲染每条支线的 subline.md，返回文件路径列表"""
        template = self.jinja_env.get_template("subline.md.j2")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        subline_dir = self.project_dir / "sublines"
        subline_dir.mkdir(parents=True, exist_ok=True)

        paths: list[Path] = []
        for idx, s in enumerate(sublines, 1):
            name = s.get("subline_name", f"支线{idx}")
            # subline_id: S01_镜灵觉醒（特殊字符替换为下划线）
            safe = re.sub(r"[^\w\u4e00-\u9fff]+", "_", name).strip("_")
            subline_id = f"S{idx:02d}_{safe or f'subline{idx}'}"

            pc = s.get("pressure_curve", {}) or {}
            # pressure_curve 可能是 dict 或 str
            pressure_curve = {
                "setup": str(pc.get("setup", "") if isinstance(pc, dict) else ""),
                "conflict": str(pc.get("conflict", "") if isinstance(pc, dict) else ""),
                "climax": str(pc.get("climax", "") if isinstance(pc, dict) else ""),
                "relief": str(pc.get("relief", "") if isinstance(pc, dict) else ""),
            }
            content = template.render(
                subline_id=subline_id,
                subline_name=name,
                created_at=now,
                goal=s.get("goal", ""),
                characters=s.get("characters", ""),
                conflicts=s.get("conflicts", ""),
                constraints=s.get("constraints", ""),
                mainline_relation=s.get("mainline_relation", ""),
                pressure_curve=pressure_curve,
            )
            # 写入 sublines/S<NN>_<name>/subline.md
            path = subline_dir / subline_id / "subline.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            paths.append(path)
        return paths

    # ============================================================
    # 呈现
    # ============================================================
    def _present(
        self,
        title: str,
        synopsis: str,
        sublines: list[dict[str, Any]],
        subline_files: list[Path],
    ) -> None:
        """呈现结果给用户"""
        self.console.print(
            Panel(
                f"[bold]故事简介[/bold]\n{synopsis[:300]}{'...' if len(synopsis) > 300 else ''}",
                title=f"[bold]outline.md · {title}[/bold]",
                border_style="cyan",
            )
        )
        # 支线表格
        table = Table(title="顶层支线任务列表（剧集树根节点）")
        table.add_column("编号", style="cyan", justify="right")
        table.add_column("支线名", style="magenta")
        table.add_column("目标", style="white", overflow="fold")
        table.add_column("压力曲线（章）", style="yellow")
        for idx, s in enumerate(sublines, 1):
            pc = s.get("pressure_curve", {}) or {}
            curve = ""
            if isinstance(pc, dict):
                curve = f"铺{pc.get('setup','')}·冲{pc.get('conflict','')}·高{pc.get('climax','')}·舒{pc.get('relief','')}"
            table.add_row(
                f"S{idx:02d}",
                s.get("subline_name", ""),
                (s.get("goal", "") or "")[:40],
                curve,
            )
        self.console.print(table)

        self.console.print(
            f"\n[bold green]✓ 大纲已生成[/bold green]：{self.outline_file}"
        )
        self.console.print(
            f"[green]✓ 支线占位[/green]：{len(subline_files)} 条 → sublines/S*/subline.md"
        )
        self.console.print(
            "[dim]下一步：使用 /design-characters 进入角色设计（M4）。[/dim]"
        )
