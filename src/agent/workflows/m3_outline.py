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

from agent.core.infra.prompt_manager import pm
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

from agent.client import LLMClient
from agent.core.story.setting_manager import SettingManager
from agent.core.engine.state_machine import Event, State, StateMachine
from agent.core.quality.guardrails import is_architecture_confirmed
from agent.core.registry.genre_pack import first_genre, first_genre_label
from agent.core.engine.workflow_registry import workflow
from agent.utils import parse_llm_json

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


@dataclass
class M3Result:
    """M3 执行结果"""

    outline_file: Path
    synopsis: str
    sublines: list[dict[str, Any]] = field(default_factory=list)
    subline_files: list[Path] = field(default_factory=list)


@workflow("m3_outline")
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
    def run(self, feedback: str = "") -> M3Result:
        """运行 M3 工作流

        Args:
            feedback: 作者修改意见（非空则在此基础上迭代修订，不改变状态）

        Raises:
            RuntimeError: 状态不符 / world.md 不存在 / 架构未确认
        """
        self.state_machine.load()
        # 迭代修订（feedback 非空）对任意状态放行（命令层已将门禁交给前置文件校验）；
        # 仅初稿生成要求处于架构确认后的阶段。
        if not feedback and self.state_machine.state not in (
            State.ARCH_CONFIRMED,
            State.OUTLINING,
        ):
            raise RuntimeError(
                f"当前状态 {self.state_machine.state.value} 不允许生成大纲，"
                f"需处于 ARCH_CONFIRMED 状态"
            )

        world_data = self.sm.load_world()
        if not world_data["exists"]:
            raise RuntimeError("world.md 不存在，请先运行 M1 配置")

        # ★门禁：架构必须已确认（生成或迭代大纲的前置）
        if not is_architecture_confirmed(self.project_dir):
            raise RuntimeError(
                "故事架构尚未确认，请先运行 /confirm-architecture 后再生成大纲"
            )

        arch_data = self._load_architecture()
        world_info = self._extract_world_info(world_data)
        title = arch_data["title"]

        action = "迭代修订大纲" if feedback else "生成大纲与支线任务"
        self.console.print(f"\n[cyan]正在{action}...[/cyan]")
        outline = self._llm_generate_outline(world_info, arch_data, feedback)
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
    def _extract_world_info(world_data: dict[str, Any]) -> dict[str, Any]:
        """从 world.md 提取信息"""
        metadata = world_data.get("metadata", {}) or {}
        content = world_data.get("content", "")
        synopsis = ""
        if "## 故事简介" in content:
            parts = content.split("## 故事简介", 1)
            if len(parts) > 1:
                synopsis = parts[1].split("##", 1)[0].strip()[:500]
        style = metadata.get("style", {}) or {}
        # 用体量详情构造给 LLM 的完整体量描述（含预计总章数），
        # 兼容旧项目（无 scope_total_words/scope_chapter_length 时回退为档位描述）。
        from agent.core.story.volume import describe_scope, estimate_chapters

        scope_key = metadata.get("scope", "medium")
        scope_total_words = metadata.get("scope_total_words")
        scope_chapter_length = metadata.get("scope_chapter_length") or (
            style.get("chapter_length") if isinstance(style, dict) else None
        )
        scope_desc = describe_scope(
            scope_key,
            total_words=scope_total_words,
            chapter_length=scope_chapter_length,
        )
        expected_chapters = estimate_chapters(
            scope_key,
            total_words=scope_total_words,
            chapter_length=scope_chapter_length,
        )
        return {
            "title": metadata.get("title", ""),
            "scope_key": scope_key,
            "scope": scope_desc,
            "expected_chapters": expected_chapters,
            "genre": first_genre(metadata),
            "genre_label": first_genre_label(metadata),
            "tone": style.get("tone", "") if isinstance(style, dict) else str(style),
            "synopsis": synopsis,
        }

    # ============================================================
    # 内部：LLM 生成
    # ============================================================
    def _llm_generate_outline(
        self,
        world_info: dict[str, str],
        arch_data: dict[str, Any],
        feedback: str = "",
    ) -> dict[str, Any]:
        """调 LLM 生成 synopsis + sublines[]

        当 feedback 非空时，读取当前 outline.md 作为基础并结合意见修订，
        LLM 在既有大纲基础上按作者意见修改，而非从零重写。
        """
        arch = arch_data["architecture"] or {}
        pt = arch.get("protagonist_triple", {}) or {}
        mp = arch.get("main_plot", {}) or {}

        user_prompt = pm.get("m3.outline").render_user(
            title=arch_data["title"],
            scope=world_info.get("scope", ""),
            expected_total_note=(
                f"目标总章数约 {world_info.get('expected_chapters', 60)} 章，"
                "各阶段压力曲线章节区间应尽量贴合该规模。"
            ) + ("（当前为大纲迭代修订，可在既有框架上增删支线与调整区间。）" if feedback else ""),
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
                from agent.core.story.method_style import load_method_text

                method_text, _name = load_method_text(self.project_dir, enabled=True)
                if method_text:
                    user_prompt += pm.get("g11.method_instruction").render_user(
                        method_text=method_text
                    )
            except Exception:  # noqa: BLE001 - 模板读取失败降级，不阻断大纲生成
                pass
        # A 系列：问答面板确定的作者偏好注入初始生成 prompt（迭代修订以作者意见为准）
        if not feedback:
            from agent.workflows.qa_sync import format_qa_constraints

            qa_text = format_qa_constraints(self.project_dir, "outline")
            if qa_text:
                user_prompt += qa_text
        # 反馈修订：带上现有大纲 + 作者意见，让 LLM 在既有基础上修改而非推倒重来
        if feedback:
            current = ""
            if self.outline_file.exists():
                try:
                    current = self.outline_file.read_text(encoding="utf-8")[-4000:]
                except OSError:
                    current = ""
            user_prompt += (
                "\n\n【作者修改意见】请严格在『现有大纲』基础上按以下意见修订，"
                "只改动被要求的部分，其余保持稳定：\n"
                f"{feedback}\n"
                + (f"\n【现有大纲】（供参考，非逐字保留）\n{current}" if current else "")
            )
        # P1 修复（2026-08-21）+ 迭代强化：JSON 解析失败自动重试一次
        # （截断多为瞬时，重试常可恢复；重试时强化「纯 JSON」约束），
        # 重试仍失败则明确抛错，绝不静默写入「架构拆解失败」占位大纲。
        last_text = ""
        system_prompt = pm.get("m3.outline").render_system(genre=world_info.get("genre_label", ""))
        # dots3-note-prev 等模型在紧预算下偶发截断/空回，采用「充足预算 + 递增重试」：
        # 初版 A 方案 8192 实测百万字稳定，这里保留充足预算并递增，规避偶发空回。
        _budgets = (16384, 16384, 20480)
        for attempt in range(len(_budgets)):
            resp = self.llm.chat_creative(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.75,
                max_tokens=_budgets[attempt],
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
                if attempt == len(_budgets) - 1:
                    raise RuntimeError(
                        "大纲生成结果无法解析为 JSON（可能被截断或格式异常），"
                        f"请重试。原始输出片段：{last_text[:200]}"
                    )
                self.console.print(
                    "[yellow]⚠ 大纲 JSON 解析失败，自动重试一次...[/yellow]"
                )
                system_prompt = (
                    pm.get("m3.outline").render_system(genre=world_info.get("genre_label", ""))
                    + "\n\n【重要】请只输出一个合法的 JSON 对象，"
                    "不要包含 ```json 代码块标记，不要输出任何解释性文字。"
                )
        raise RuntimeError(
            "大纲生成结果无法解析为 JSON（可能被截断或格式异常），"
            f"请重试。原始输出片段：{last_text[:200]}"
        )

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
