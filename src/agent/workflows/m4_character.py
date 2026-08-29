"""M4 角色路线与关系网工作流

基于已确认架构 + 已生成大纲，产出：
    - protagonist_route.md（主角成长树，主分支 + 备选分支）
    - characters/<姓名>.md（主要角色档案，按 PRD 5.2 模板）
    - relations/graph.md（初始关系网：Mermaid + 结构化节点/边）
    - foreshadows.md（初始伏笔登记表 F-01 ~ F-08）
    - golden_finger_registration.md（金手指登记：成长/代价/上限，与 world.md 一致）

流程：
    1. 状态门禁：需处于 OUTLINING 或 CHARACTER_DESIGN
    2. 门禁 F14：architecture.confirmed == true
    3. 读 world.md（含金手指初始登记）+ architecture.md（八维度）+ outline.md（支线列表）
    4. 调 LLM 生成主角路线 + 角色档案 + 关系网 + 伏笔表 + 金手指登记
    5. 渲染 Jinja 模板写入 5 类产出
    6. 状态转换 OUTLINING → CHARACTER_DESIGN

状态转换：OUTLINING → CHARACTER_DESIGN
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

from agent.client import LLMClient
from agent.core.story.setting_manager import SettingManager
from agent.core.engine.state_machine import Event, State, StateMachine
from agent.core.quality.guardrails import is_architecture_confirmed
from agent.core.registry.genre_pack import first_genre
from agent.core.engine.workflow_registry import workflow
from agent.prompts import M4_SYSTEM_PROMPT, M4_USER_PROMPT_TEMPLATE
from agent.utils import parse_llm_json

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


@dataclass
class M4Result:
    """M4 执行结果"""

    protagonist_route_file: Path
    graph_file: Path
    foreshadows_file: Path
    golden_finger_file: Path
    character_files: list[Path] = field(default_factory=list)
    protagonist_route: dict[str, Any] = field(default_factory=dict)
    characters: list[dict[str, Any]] = field(default_factory=list)
    relation_graph: dict[str, Any] = field(default_factory=dict)
    foreshadows: list[dict[str, Any]] = field(default_factory=list)
    golden_finger_registration: dict[str, Any] = field(default_factory=dict)


@workflow("m4_character")
class M4CharacterWorkflow:
    """M4 角色路线与关系网工作流"""

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
        self.jinja_env = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            autoescape=select_autoescape(disabled_extensions=("j2",)),
            keep_trailing_newline=True,
        )

    # ============================================================
    # 入口
    # ============================================================
    def run(self, feedback: str = "") -> M4Result:
        """运行 M4 工作流

        Args:
            feedback: 作者修改意见（非空则基于现有角色产物迭代修订，不改变状态）

        Raises:
            RuntimeError: 状态不符 / world.md 不存在 / 架构未确认
        """
        self.state_machine.load()
        # 迭代修订（feedback 非空）对任意状态放行（命令层已将门禁交给前置文件校验）；
        # 仅初稿生成要求处于大纲之后阶段。
        if not feedback and self.state_machine.state not in (
            State.OUTLINING,
            State.CHARACTER_DESIGN,
        ):
            raise RuntimeError(
                f"当前状态 {self.state_machine.state.value} 不允许角色设计，"
                f"需先运行 /outline 进入 OUTLINING 状态"
            )

        world_data = self.sm.load_world()
        if not world_data["exists"]:
            raise RuntimeError("world.md 不存在，请先运行 M1 配置")

        # ★门禁 F14：架构必须已确认
        if not is_architecture_confirmed(self.project_dir):
            raise RuntimeError(
                "故事架构尚未确认，请先运行 /confirm-architecture 后再进行角色设计"
            )

        arch_data = self._load_architecture()
        outline_data = self._load_outline()
        world_info = self._extract_world_info(world_data)
        title = arch_data["title"]

        action = "迭代修订角色设计" if feedback else "生成主角路线、角色档案与关系网"
        self.console.print(f"\n[cyan]正在{action}...[/cyan]")
        m4 = self._llm_generate_characters(
            world_info, arch_data, outline_data, feedback
        )

        protagonist_route = m4.get("protagonist_route") or {}
        characters = m4.get("characters") or []
        # 注：_llm_generate_characters 已保证 characters 非空且 route 含 nodes
        #（两次解析失败会响亮抛错，不再静默降级为占位角色）。
        relation_graph = m4.get("relation_graph") or {}
        foreshadows = m4.get("foreshadows") or []
        golden_finger = m4.get("golden_finger_registration") or {}

        # 渲染并保存
        route_file = self._render_route(protagonist_route)
        character_files = self._render_characters(characters, title)
        graph_file = self._render_graph(relation_graph)
        foreshadows_file = self._render_foreshadows(foreshadows)
        golden_finger_file = self._render_golden_finger(golden_finger)

        # 呈现
        self._present(
            title,
            protagonist_route,
            characters,
            relation_graph,
            foreshadows,
            golden_finger,
        )

        # 状态转换
        if self.state_machine.state == State.OUTLINING:
            self.state_machine.transition(Event.DESIGN_CHARACTERS)
            self.state_machine.save()

        return M4Result(
            protagonist_route_file=route_file,
            character_files=character_files,
            graph_file=graph_file,
            foreshadows_file=foreshadows_file,
            golden_finger_file=golden_finger_file,
            protagonist_route=protagonist_route,
            characters=characters,
            relation_graph=relation_graph,
            foreshadows=foreshadows,
            golden_finger_registration=golden_finger,
        )

    # ============================================================
    # 内部：读取数据
    # ============================================================
    def _load_architecture(self) -> dict[str, Any]:
        arch_file = self.project_dir / "architecture.md"
        if not arch_file.exists():
            raise RuntimeError("architecture.md 不存在，请先完成 M14")
        post = frontmatter.load(arch_file)
        return {
            "title": post.metadata.get("title", ""),
            "confirmed": bool(post.metadata.get("confirmed", False)),
            "architecture": post.metadata.get("architecture", {}) or {},
        }

    def _load_outline(self) -> dict[str, Any]:
        """读 outline.md，提取简介 + 支线表格"""
        outline_file = self.project_dir / "outline.md"
        if not outline_file.exists():
            raise RuntimeError("outline.md 不存在，请先完成 M3")
        post = frontmatter.load(outline_file)
        synopsis = ""
        if "## 故事简介" in post.content:
            p = post.content.split("## 故事简介", 1)[1].split("##", 1)[0].strip()
            synopsis = p[:800]
        sublines = post.metadata.get("sublines", []) or []
        return {
            "synopsis": synopsis,
            "sublines": sublines,
        }

    @staticmethod
    def _extract_world_info(world_data: dict[str, Any]) -> dict[str, Any]:
        metadata = world_data.get("metadata", {}) or {}
        content = world_data.get("content", "")
        style = metadata.get("style", {}) or {}

        # 提取金手指区块
        golden_finger_info = ""
        if "## 金手指登记" in content:
            golden_finger_info = content.split("## 金手指登记", 1)[1].split("##", 1)[0].strip()[:600]

        return {
            "title": metadata.get("title", ""),
            "scope": metadata.get("scope", ""),
            "genre": first_genre(metadata),
            "tone": style.get("tone", "") if isinstance(style, dict) else str(style),
            "golden_finger_info": golden_finger_info,
        }

    # ============================================================
    # 内部：LLM 生成
    # ============================================================
    def _llm_generate_characters(
        self,
        world_info: dict[str, Any],
        arch_data: dict[str, Any],
        outline_data: dict[str, Any],
        feedback: str = "",
    ) -> dict[str, Any]:
        arch = arch_data["architecture"] or {}
        pt = arch.get("protagonist_triple", {}) or {}
        mp = arch.get("main_plot", {}) or {}

        # 支线列表（给 LLM 一个文本摘要）
        sublines_text = outline_data.get("sublines") or []
        if isinstance(sublines_text, list):
            sublines_table = "\n".join(
                f"- S{i+1:02d} {s.get('subline_name','')}: "
                f"目标={s.get('goal','')[:80]}"
                for i, s in enumerate(sublines_text[:8])
            )
        else:
            sublines_table = str(sublines_text)[:1000]

        user_prompt = M4_USER_PROMPT_TEMPLATE.format(
            title=arch_data["title"],
            scope=world_info.get("scope", ""),
            tone=world_info.get("tone", ""),
            story_core=arch.get("story_core", ""),
            protagonist_who=pt.get("who", ""),
            protagonist_want=pt.get("want", ""),
            protagonist_obstacle=pt.get("obstacle", ""),
            main_plot_beginning=mp.get("beginning", ""),
            main_plot_development=mp.get("development", ""),
            main_plot_twist=mp.get("twist", ""),
            main_plot_resolution=mp.get("resolution", ""),
            theme=arch.get("theme", ""),
            ending=arch.get("ending", ""),
            emotional_tone=arch.get("emotional_tone", ""),
            sublines_table=sublines_table,
            golden_finger_info=world_info.get("golden_finger_info", "") or "（世界设定未登记金手指，请按架构描述生成）",
        )
        # A 系列：问答面板确定的作者偏好注入初始生成 prompt（迭代修订以作者意见为准）
        if not feedback:
            from agent.workflows.qa_sync import format_qa_constraints

            qa_text = format_qa_constraints(self.project_dir, "characters")
            if qa_text:
                user_prompt += qa_text

        # 反馈修订：带上现有角色产物 + 作者意见，让 LLM 在既有基础上修改而非推倒重来
        if feedback:
            current = ""
            proto_file = self.project_dir / "protagonist_route.md"
            chars = []
            if proto_file.exists():
                try:
                    current = proto_file.read_text(encoding="utf-8")[-4000:]
                except OSError:
                    current = ""
            chars_dir = self.project_dir / "characters"
            if chars_dir.exists():
                try:
                    chars = sorted(
                        p.name for p in chars_dir.glob("*.md")
                    )[:10]
                except OSError:
                    chars = []
            user_prompt += (
                "\n\n【作者修改意见】请严格在『现有角色设计』基础上按以下意见修订，"
                "只改动被要求的部分，其余保持稳定：\n"
                f"{feedback}\n"
                + (
                    f"\n【现有角色文件】（供参考，非逐字保留）\n"
                    f"主角路线：\n{current or '（无）'}\n"
                    f"角色列表：" + (", ".join(chars) if chars else "（无）")
                    if (current or chars)
                    else ""
                )
            )

        raw = self.llm.chat_creative(
            messages=[
                {"role": "system", "content": M4_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.75,
            max_tokens=16384,
            enable_thinking=False,
        )
        # 解析容错：对齐 M1/M14/M3 策略——JSON 解析失败自动重试一次（截断多为
        # 瞬时，重试时强化「纯 JSON」约束常可恢复），重试仍失败则明确抛错，
        # 绝不静默降级为占位角色（否则真实角色设计会被静默丢弃）。
        system_prompt = M4_SYSTEM_PROMPT
        last_text = raw.text or ""
        for attempt in range(2):
            try:
                data = parse_llm_json(last_text)
                if not isinstance(data, dict):
                    raise ValueError("顶层不是 JSON 对象")
                chars = data.get("characters")
                if not isinstance(chars, list) or not chars:
                    raise ValueError("characters 缺失或为空")
                route = data.get("protagonist_route") or {}
                if not route.get("nodes"):
                    raise ValueError("protagonist_route.nodes 缺失或为空")
                return data
            except ValueError:
                if attempt == 0:
                    self.console.print(
                        "[yellow]⚠ 角色设计 JSON 解析失败，自动重试一次...[/yellow]"
                    )
                    system_prompt = (
                        M4_SYSTEM_PROMPT
                        + "\n\n【重要】请只输出一个合法的 JSON 对象，必须包含 "
                        "protagonist_route（含 nodes）与 characters（至少 1 名角色），"
                        "不要包含 ```json 代码块标记，不要输出任何解释性文字。"
                    )
                    raw = self.llm.chat_creative(
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        temperature=0.75,
                        max_tokens=16384,
                        enable_thinking=False,
                    )
                    last_text = raw.text or ""
                    continue
                raise RuntimeError(
                    "角色设计结果无法解析为 JSON（可能被截断或格式异常），"
                    f"请重试。原始输出片段：{last_text[:200]}"
                )
        raise RuntimeError("角色设计结果无法解析为 JSON，请重试")

    # ============================================================
    # 内部：渲染输出
    # ============================================================
    def _render_route(self, route: dict[str, Any]) -> Path:
        file = self.project_dir / "protagonist_route.md"
        template = self.jinja_env.get_template("protagonist_route.md.j2")
        content = template.render(
            root_node=route.get("root_node", "") or "路线起点",
            nodes=route.get("nodes", []) or [],
        )
        file.write_text(content, encoding="utf-8")
        return file

    def _render_characters(self, characters: list[dict[str, Any]], title: str) -> list[Path]:
        chars_dir = self.project_dir / "characters"
        chars_dir.mkdir(parents=True, exist_ok=True)
        template = self.jinja_env.get_template("character.md.j2")
        created: list[Path] = []
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for c in characters:
            name = c.get("name") or f"角色{len(created)+1}"
            role = c.get("role") or "supporting"
            fname = _safe_filename(name) + ".md"
            file = chars_dir / fname
            # 校验字段补默认
            val = c.get("validation") or {}
            lf = c.get("language_fingerprint") or {}
            arc = c.get("arc") or {}
            content = template.render(
                name=name,
                role=role,
                faction=c.get("faction") or "",
                realm=c.get("realm") or "",
                first_appearance=c.get("first_appearance") or "S01",
                created_at=created_at,
                identity=c.get("identity") or "",
                core_motivation=c.get("core_motivation") or "",
                surface_goal=c.get("surface_goal") or "",
                deep_goal=c.get("deep_goal") or "",
                secret=c.get("secret") or "",
                arc={
                    "start": arc.get("start") or "",
                    "end": arc.get("end") or "",
                },
                language_fingerprint={
                    "catchphrase": lf.get("catchphrase") or "",
                    "sentence_style": lf.get("sentence_style") or "",
                    "vocabulary": lf.get("vocabulary") or "",
                    "banned_words": lf.get("banned_words") or [],
                },
                relations=c.get("relations") or "",
                validation={
                    "motivation_check": val.get("motivation_check") or "",
                    "appearance_interval": val.get("appearance_interval") or 5,
                },
            )
            file.write_text(content, encoding="utf-8")
            created.append(file)
        return created

    def _render_graph(self, graph: dict[str, Any]) -> Path:
        rel_dir = self.project_dir / "relations"
        rel_dir.mkdir(parents=True, exist_ok=True)
        file = rel_dir / "graph.md"
        template = self.jinja_env.get_template("graph.md.j2")
        nodes = graph.get("nodes", []) or []
        edges = graph.get("edges", []) or []
        # lookup: id -> label
        labels: dict[str, str] = {n.get("id", ""): n.get("label", "") for n in nodes}

        def lookup_label(node_id: str) -> str:
            return labels.get(node_id, node_id)

        content = template.render(
            updated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            current_chapter="ch000（初始）",
            nodes=nodes,
            edges=edges,
            lookup_label=lookup_label,
        )
        file.write_text(content, encoding="utf-8")
        return file

    def _render_foreshadows(self, foreshadows: list[dict[str, Any]]) -> Path:
        file = self.project_dir / "foreshadows.md"
        # 若无数据，使用占位
        if not foreshadows:
            foreshadows = [
                {
                    "id": "F-01",
                    "content": "（示例）埋藏位置与回收点需在写作过程中维护",
                    "planted_at": "TBD",
                    "expected_resolve": "TBD",
                    "state": "未埋",
                    "related_characters": "",
                }
            ]
        # 用 jinja 渲染一个灵活表格模板（此处内联简单渲染 + 模板文件）
        template = self.jinja_env.get_template("foreshadows.md.j2")
        # 取第一条来填示例字段位置
        f01 = foreshadows[0] if foreshadows else {}
        content = template.render(
            f01=f01,
        )
        # 模板只有 F-01 单行，我们替换成全量：
        # 构造完整表格 + 统计
        content = self._build_full_foreshadows(foreshadows, content)
        file.write_text(content, encoding="utf-8")
        return file

    def _build_full_foreshadows(self, foreshadows: list[dict[str, Any]], template_rendered: str) -> str:
        """把模板的单 F-01 替换成完整表格 + 统计"""
        rows: list[str] = []
        for f in foreshadows:
            rows.append(
                f"| {f.get('id', '')} | {f.get('content', '')} | {f.get('planted_at', '')} | "
                f"{f.get('expected_resolve', '')} | {f.get('state', '未埋')} | {f.get('related_characters', '')} |"
            )
        table = "\n".join(rows)

        count_status: dict[str, int] = {"未埋": 0, "已埋": 0, "已回收": 0, "已废弃": 0}
        for f in foreshadows:
            s = f.get("state", "未埋")
            if s in count_status:
                count_status[s] += 1
        total_planted = count_status["已埋"] + count_status["已回收"] + count_status["已废弃"]
        rate = f"{count_status['已回收'] / total_planted:.2%}" if total_planted else "N/A"
        stats = (
            f"- 未埋：{count_status['未埋']}\n"
            f"- 已埋：{count_status['已埋']}\n"
            f"- 已回收：{count_status['已回收']}\n"
            f"- 已废弃：{count_status['已废弃']}\n"
            f"- 回收率：{rate}"
        )

        # 用新内容重写整份文档（保留开头 header）
        header = (
            "# 伏笔登记表\n\n"
            "> 状态：未埋 / 已埋 / 已回收 / 已废弃\n"
            "> 规则：每 10 章强制埋 ≥1 长线伏笔、回收 ≥1 旧伏笔\n\n"
        )
        full = (
            f"{header}"
            f"| ID | 伏笔内容 | 埋设位置 | 预期回收点 | 状态 | 关联角色 |\n"
            f"|---|---|---|---|---|---|\n"
            f"{table}\n\n"
            f"## 统计\n\n"
            f"{stats}\n"
        )
        return full

    def _render_golden_finger(self, gf: dict[str, Any]) -> Path:
        file = self.project_dir / "golden_finger_registration.md"
        lines: list[str] = [
            "---",
            f"name: \"{gf.get('name', '')}\"",
            f"type: \"{gf.get('type', '')}\"",
            "frozen: true  # 冻结字段，M7 /revise 时如需改动请先 `/unlock-golden-finger`",
            "---",
            "",
            "# 金手指登记",
            "",
            f"**名称**：{gf.get('name', '')}  ",
            f"**类型**：{gf.get('type', '')}",
            "",
            "## 成长阶段",
            "",
            "| 阶段 | 可用能力 | 代价 |",
            "|---|---|---|",
        ]
        for stage in gf.get("growth_stages", []) or []:
            lines.append(
                f"| {stage.get('stage', '')} | {stage.get('ability', '')} | {stage.get('cost', '')} |"
            )
        lines += [
            "",
            "## 代价规则",
            "",
            str(gf.get("cost_rules", "")),
            "",
            "## 硬上限（冻结字段）",
            "",
            str(gf.get("hard_limits", "")),
            "",
            "## 解锁条件",
            "",
            str(gf.get("unlock_conditions", "")),
            "",
        ]
        file.write_text("\n".join(lines), encoding="utf-8")
        return file

    # ============================================================
    # 内部：呈现
    # ============================================================
    def _present(
        self,
        title: str,
        route: dict[str, Any],
        characters: list[dict[str, Any]],
        graph: dict[str, Any],
        foreshadows: list[dict[str, Any]],
        golden_finger: dict[str, Any],
    ) -> None:
        # 路线摘要
        nodes = route.get("nodes", []) or []
        route_summary = "\n".join(
            f"- {n.get('id','')} [{n.get('chapter_range','')}] {n.get('milestone','')[:50]}"
            for n in nodes[:8]
        )
        self.console.print(
            Panel(
                route_summary or "（未生成）",
                title=f"protagonist_route.md · {title}",
                border_style="magenta",
                expand=False,
            )
        )

        # 角色列表
        t = Table(title="主要角色档案（characters/*.md）")
        t.add_column("角色", style="bold")
        t.add_column("定位")
        t.add_column("势力")
        t.add_column("境界")
        t.add_column("首次出场", style="dim")
        for c in characters[:10]:
            t.add_row(
                c.get("name", ""),
                c.get("role", ""),
                c.get("faction", ""),
                c.get("realm", ""),
                c.get("first_appearance", ""),
            )
        self.console.print(t)

        # 关系网摘要
        nodes = graph.get("nodes", []) or []
        edges = graph.get("edges", []) or []
        self.console.print(
            f"[dim]关系网：{len(nodes)} 节点 / {len(edges)} 边 → relations/graph.md[/dim]"
        )
        self.console.print(
            f"[dim]伏笔：{len(foreshadows)} 条 → foreshadows.md[/dim]"
        )
        if golden_finger:
            self.console.print(
                f"[dim]金手指登记：{golden_finger.get('name', '')} "
                f"({len(golden_finger.get('growth_stages', []) or [])} 阶段) "
                f"→ golden_finger_registration.md[/dim]"
            )
        self.console.print(
            "\n[green]✓ 角色设计完成。[/green]下一步：使用 /write 进入章节创作（M5）。"
        )


def _safe_filename(name: str) -> str:
    # 去掉 windows 非法字符，保留中文名（允许）
    s = re.sub(r'[\\/:*?"<>|]', "_", name).strip().strip(".")
    return s or "unnamed"
