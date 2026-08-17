"""M15 书虫 Skill（独立可移植）

基于 PRD F15.1-F15.6，实现以资深书虫视角评估小说标题/开头吸引力的 skill。

核心特性：
    - 独立性契约（F15.6）：纯文本输入 / JSON+MD 双形态输出 / 无状态 / 无外部依赖
    - 7 维度评估（F15.3）：标题吸引力/开篇钩子/节奏/人物辨识度/题材契合/同质化/章末钩子
    - 版本对比（F15.5）：支持 v1→v2 提升点对比
    - 可被任何 AI 工具加载（F15.1）：通过 SKILL.md frontmatter 声明能力

加载方式：
    /load-skill bookworm        # 加载并注册 /bookworm-review 命令
    /bookworm-review ...        # 执行测评

文件结构：
    agent/skills/bookworm/
    ├── SKILL.md                # 能力声明（frontmatter + 文档）
    ├── persona.md              # 书虫人格设定
    ├── rubrics.md              # 评估维度与评分标准
    └── genre_expectations/
        └── <genre>.md          # 各题材读者期待
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import frontmatter
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agent.core.llm_client import LLMClient
from agent.prompts import M15_BOOKWORM_SYSTEM_PROMPT, M15_BOOKWORM_USER_TEMPLATE
from agent.utils import parse_llm_json


# ============================================================
# 数据契约
# ============================================================
@dataclass
class BookwormInput:
    """书虫测评输入（F15.6 独立性契约）"""

    title: str  # 章节标题或副标题
    book_name: str  # 小说名称
    opening_text: str  # 开头正文（建议前 3000 字或前 3 章）
    genre: str | None = None  # 题材（可选，如 xiuxian/romance/mystery）


@dataclass
class BookwormIssue:
    """问题项"""

    severity: str  # block | warn
    description: str
    location: str = ""


@dataclass
class BookwormReview:
    """书虫测评结果（JSON + MD 双形态）"""

    total_score: int
    dimensions: dict[str, int]
    one_liner_feeling: str
    issues: list[BookwormIssue]
    suggestions: list[str]
    reference: str
    # 元信息
    input_snapshot: BookwormInput | None = None
    version: str = ""

    # ------ JSON 形态 ------
    def to_dict(self) -> dict[str, Any]:
        d = {
            "total_score": self.total_score,
            "dimensions": dict(self.dimensions),
            "one_liner_feeling": self.one_liner_feeling,
            "issues": [asdict(i) for i in self.issues],
            "suggestions": list(self.suggestions),
            "reference": self.reference,
        }
        if self.version:
            d["version"] = self.version
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    # ------ Markdown 形态 ------
    def to_markdown(self) -> str:
        lines: list[str] = []
        v_tag = f" v{self.version}" if self.version else ""
        lines.append(f"# 书虫测评报告{v_tag}")
        lines.append("")
        # 总分
        verdict = _score_verdict(self.total_score)
        lines.append(f"**总评分**：{self.total_score}/100 （{verdict}）")
        lines.append("")
        # 一句话感受
        lines.append(f"> {self.one_liner_feeling}")
        lines.append("")
        # 维度评分表
        lines.append("## 维度评分")
        lines.append("")
        lines.append("| 维度 | 得分 |")
        lines.append("|---|---|")
        dim_labels = {
            "title_appeal": "标题吸引力",
            "opening_hook": "开篇钩子",
            "pacing": "节奏",
            "character_distinctiveness": "人物辨识度",
            "genre_fit": "题材契合",
            "originality": "同质化区分",
            "chapter_end_hook": "章末钩子",
        }
        for key, label in dim_labels.items():
            score = self.dimensions.get(key, 0)
            lines.append(f"| {label} | {score}/100 |")
        lines.append("")
        # 问题清单
        if self.issues:
            lines.append("## 问题清单")
            lines.append("")
            for issue in self.issues:
                icon = "🚫" if issue.severity == "block" else "⚠️"
                loc = f" `{issue.location}`" if issue.location else ""
                lines.append(f"- {icon} **[{issue.severity.upper()}]**{loc} {issue.description}")
            lines.append("")
        # 改进建议
        if self.suggestions:
            lines.append("## 改进建议")
            lines.append("")
            for i, s in enumerate(self.suggestions, 1):
                lines.append(f"{i}. {s}")
            lines.append("")
        # 参考对照
        if self.reference:
            lines.append("## 参考对照")
            lines.append("")
            lines.append(self.reference)
            lines.append("")
        return "\n".join(lines)


@dataclass
class BookwormComparison:
    """版本对比结果（F15.5）"""

    old_review: BookwormReview
    new_review: BookwormReview
    score_delta: int
    dimension_deltas: dict[str, int]  # key -> new - old
    improvements: list[str]  # 维度提升点
    regressions: list[str]  # 维度退步点
    resolved_issues: list[str]  # 已解决的 block 问题
    new_issues: list[str]  # 新增的问题


def _score_verdict(score: int) -> str:
    """分数 → 评语"""
    if score >= 90:
        return "会追更、会安利"
    if score >= 80:
        return "会追更"
    if score >= 70:
        return "可看可不看"
    if score >= 60:
        return "勉强能看"
    return "弃书"


# ============================================================
# 维度权重（与 rubrics.md 一致）
# ============================================================
DIMENSION_WEIGHTS: dict[str, float] = {
    "opening_hook": 0.25,
    "title_appeal": 0.15,
    "pacing": 0.15,
    "character_distinctiveness": 0.15,
    "genre_fit": 0.10,
    "originality": 0.10,
    "chapter_end_hook": 0.10,
}

DIMENSION_KEYS = list(DIMENSION_WEIGHTS.keys())


# ============================================================
# Skill 加载器
# ============================================================
@dataclass
class SkillManifest:
    """SKILL.md frontmatter 解析结果"""

    name: str
    version: str
    type: str
    description: str
    commands: list[dict[str, Any]]
    independent: bool = False
    dependencies: list[str] = field(default_factory=list)
    hooks: list[str] = field(default_factory=list)
    skill_dir: Path | None = None

    @property
    def command_names(self) -> list[str]:
        return [c.get("name", "") for c in self.commands if c.get("name")]


def load_skill_manifest(skill_dir: Path) -> SkillManifest:
    """从 SKILL.md 解析 skill 能力声明

    Args:
        skill_dir: skill 目录（含 SKILL.md）

    Raises:
        FileNotFoundError: SKILL.md 不存在
        ValueError: frontmatter 缺少必需字段
    """
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        raise FileNotFoundError(f"SKILL.md 不存在: {skill_md}")
    post = frontmatter.load(skill_md)
    meta = post.metadata
    name = meta.get("name", "")
    if not name:
        raise ValueError(f"SKILL.md 缺少 name 字段: {skill_md}")
    commands = meta.get("commands", []) or []
    return SkillManifest(
        name=name,
        version=str(meta.get("version", "0.0.0")),
        type=meta.get("type", ""),
        description=meta.get("description", ""),
        commands=list(commands),
        independent=bool(meta.get("independent", False)),
        dependencies=list(meta.get("dependencies", []) or []),
        hooks=list(meta.get("hooks", []) or []),
        skill_dir=skill_dir,
    )


# ============================================================
# 书虫 Skill
# ============================================================
class BookwormSkill:
    """书虫测评 Skill

    用法：
        skill = BookwormSkill.load()  # 从内置 agent/skills/bookworm/ 加载
        review = skill.review(BookwormInput(...))
        print(review.to_markdown())
    """

    SKILL_NAME = "bookworm"
    REVIEW_COMMAND = "bookworm-review"

    def __init__(
        self,
        skill_dir: Path,
        manifest: SkillManifest,
        persona: str,
        rubrics: str,
        genre_expectations: dict[str, str],
        llm: LLMClient | None = None,
        console: Console | None = None,
    ) -> None:
        self.skill_dir = skill_dir
        self.manifest = manifest
        self.persona = persona
        self.rubrics = rubrics
        self.genre_expectations = genre_expectations
        self._llm = llm
        self._console = console

    # ------ 加载 ------
    @classmethod
    def load(
        cls,
        skill_dir: Path | None = None,
        llm: LLMClient | None = None,
        console: Console | None = None,
    ) -> "BookwormSkill":
        """加载书虫 skill

        Args:
            skill_dir: skill 目录，None 则用内置 agent/skills/bookworm/
            llm: LLM 客户端，None 则懒加载
            console: rich Console
        """
        if skill_dir is None:
            # agent/skills/bookworm/ 相对于本文件
            skill_dir = Path(__file__).resolve().parent.parent / "skills" / "bookworm"
        skill_dir = Path(skill_dir)

        manifest = load_skill_manifest(skill_dir)
        if manifest.name != cls.SKILL_NAME:
            raise ValueError(
                f"skill 目录 name 不是 {cls.SKILL_NAME}: {manifest.name}"
            )

        persona = (skill_dir / "persona.md").read_text(encoding="utf-8")
        rubrics = (skill_dir / "rubrics.md").read_text(encoding="utf-8")

        # 加载所有题材期待
        genre_dir = skill_dir / "genre_expectations"
        genre_expectations: dict[str, str] = {}
        if genre_dir.exists():
            for f in genre_dir.glob("*.md"):
                genre_expectations[f.stem] = f.read_text(encoding="utf-8")

        return cls(
            skill_dir=skill_dir,
            manifest=manifest,
            persona=persona,
            rubrics=rubrics,
            genre_expectations=genre_expectations,
            llm=llm,
            console=console,
        )

    # ------ LLM ------
    @property
    def llm(self) -> LLMClient:
        if self._llm is None:
            self._llm = LLMClient()
        return self._llm

    @property
    def console(self) -> Console:
        if self._console is None:
            self._console = Console()
        return self._console

    # ------ 测评 ------
    def review(
        self,
        inp: BookwormInput,
        version: str = "",
        save_dir: Path | None = None,
    ) -> BookwormReview:
        """执行书虫测评

        Args:
            inp: 测评输入
            version: 版本标签（如 "1"、"2"），用于多次测评对比
            save_dir: 保存目录，None 则不保存

        Returns:
            BookwormReview
        """
        system_prompt = self._build_system_prompt(inp.genre)
        user_prompt = self._build_user_prompt(inp)

        resp = self.llm.chat_utility(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,  # 评估任务低温度保证稳定
        )

        data = parse_llm_json(resp.text)
        review = self._parse_review(data, inp, version)

        if save_dir is not None:
            self._save(review, save_dir)

        return review

    # ------ 版本对比 ------
    def compare(
        self,
        old: BookwormReview,
        new: BookwormReview,
    ) -> BookwormComparison:
        """对比两次测评结果（F15.5）

        Args:
            old: 旧版本测评
            new: 新版本测评

        Returns:
            BookwormComparison
        """
        score_delta = new.total_score - old.total_score
        dimension_deltas: dict[str, int] = {}
        improvements: list[str] = []
        regressions: list[str] = []
        dim_labels = {
            "title_appeal": "标题吸引力",
            "opening_hook": "开篇钩子",
            "pacing": "节奏",
            "character_distinctiveness": "人物辨识度",
            "genre_fit": "题材契合",
            "originality": "同质化区分",
            "chapter_end_hook": "章末钩子",
        }
        for key in DIMENSION_KEYS:
            old_v = old.dimensions.get(key, 0)
            new_v = new.dimensions.get(key, 0)
            delta = new_v - old_v
            dimension_deltas[key] = delta
            label = dim_labels.get(key, key)
            if delta > 0:
                improvements.append(f"{label} +{delta}")
            elif delta < 0:
                regressions.append(f"{label} {delta}")

        # 问题变化
        old_block_descs = {
            i.description for i in old.issues if i.severity == "block"
        }
        new_block_descs = {
            i.description for i in new.issues if i.severity == "block"
        }
        resolved_issues = sorted(old_block_descs - new_block_descs)
        new_issues = sorted(new_block_descs - old_block_descs)

        return BookwormComparison(
            old_review=old,
            new_review=new,
            score_delta=score_delta,
            dimension_deltas=dimension_deltas,
            improvements=improvements,
            regressions=regressions,
            resolved_issues=resolved_issues,
            new_issues=new_issues,
        )

    # ------ 终端展示 ------
    def show_review(self, review: BookwormReview) -> None:
        """在终端展示测评结果"""
        console = self.console
        verdict = _score_verdict(review.total_score)
        v_tag = f" v{review.version}" if review.version else ""

        # 总分面板
        color = (
            "green" if review.total_score >= 80
            else "yellow" if review.total_score >= 70
            else "red"
        )
        console.print(
            Panel(
                f"[bold {color}]{review.total_score}/100[/bold {color}] {verdict}\n"
                f"[italic]{review.one_liner_feeling}[/italic]",
                title=f"书虫测评{v_tag}",
                border_style=color,
            )
        )

        # 维度评分表
        table = Table(title="维度评分", show_lines=False)
        table.add_column("维度", style="cyan")
        table.add_column("得分", justify="right")
        table.add_column("条", style="dim")
        dim_labels = {
            "title_appeal": "标题吸引力",
            "opening_hook": "开篇钩子",
            "pacing": "节奏",
            "character_distinctiveness": "人物辨识度",
            "genre_fit": "题材契合",
            "originality": "同质化区分",
            "chapter_end_hook": "章末钩子",
        }
        for key in DIMENSION_KEYS:
            score = review.dimensions.get(key, 0)
            bar_len = max(1, score // 10)
            bar = "█" * bar_len + "░" * (10 - bar_len)
            label = dim_labels.get(key, key)
            table.add_row(label, f"{score}/100", bar)
        console.print(table)

        # 问题清单
        if review.issues:
            console.print("\n[bold]问题清单[/bold]")
            for issue in review.issues:
                if issue.severity == "block":
                    icon = "🚫"
                    style = "bold red"
                else:
                    icon = "⚠️"
                    style = "yellow"
                loc = f" [dim]({issue.location})[/dim]" if issue.location else ""
                console.print(f"  {icon} [{style}]{issue.description}[/{style}]{loc}")

        # 改进建议
        if review.suggestions:
            console.print("\n[bold green]改进建议[/bold green]")
            for i, s in enumerate(review.suggestions, 1):
                console.print(f"  [green]{i}.[/green] {s}")

        # 参考对照
        if review.reference:
            console.print(f"\n[dim]参考对照：{review.reference}[/dim]")

    def show_comparison(self, comp: BookwormComparison) -> None:
        """展示版本对比"""
        console = self.console
        old_v = comp.old_review.version or "v1"
        new_v = comp.new_review.version or "v2"
        console.print(
            Panel(
                f"[bold]版本对比 {old_v} → {new_v}[/bold]",
                border_style="blue",
            )
        )

        # 总分变化
        delta = comp.score_delta
        if delta > 0:
            console.print(f"总评分：{comp.old_review.total_score} → [green]{comp.new_review.total_score} (+{delta})[/green]")
        elif delta < 0:
            console.print(f"总评分：{comp.old_review.total_score} → [red]{comp.new_review.total_score} ({delta})[/red]")
        else:
            console.print(f"总评分：{comp.old_review.total_score} → {comp.new_review.total_score} (持平)")

        # 维度变化
        if comp.improvements:
            console.print(f"\n[green]提升[/green]：{', '.join(comp.improvements)}")
        if comp.regressions:
            console.print(f"[red]退步[/red]：{', '.join(comp.regressions)}")

        # 问题变化
        if comp.resolved_issues:
            console.print(f"\n[green]已解决问题[/green]：")
            for i in comp.resolved_issues:
                console.print(f"  ✓ {i}")
        if comp.new_issues:
            console.print(f"[red]新增问题[/red]：")
            for i in comp.new_issues:
                console.print(f"  ✗ {i}")

    # ------ 内部方法 ------
    def _build_system_prompt(self, genre: str | None) -> str:
        genre_block = ""
        if genre and genre in self.genre_expectations:
            genre_block = f"\n# 题材读者期待（{genre}）\n\n{self.genre_expectations[genre]}"
        elif genre:
            genre_block = f"\n# 题材读者期待\n\n（未内置 {genre} 题材期待，按通用网文标准评估）"
        return M15_BOOKWORM_SYSTEM_PROMPT.format(
            persona=self.persona,
            rubrics=self.rubrics,
            genre_expectations=genre_block,
        )

    def _build_user_prompt(self, inp: BookwormInput) -> str:
        genre_line = f"【题材】{inp.genre}" if inp.genre else "【题材】未指定"
        return M15_BOOKWORM_USER_TEMPLATE.format(
            book_name=inp.book_name,
            title=inp.title,
            genre_line=genre_line,
            opening_text=inp.opening_text,
        )

    def _parse_review(
        self,
        data: dict[str, Any],
        inp: BookwormInput,
        version: str,
    ) -> BookwormReview:
        """解析 LLM JSON 输出为 BookwormReview"""
        dims = data.get("dimensions", {}) or {}
        # 补齐缺失维度为 0
        for key in DIMENSION_KEYS:
            if key not in dims:
                dims[key] = 0
        # 校验分数范围
        for key in list(dims.keys()):
            try:
                v = int(dims[key])
            except (TypeError, ValueError):
                v = 0
            dims[key] = max(0, min(100, v))

        # 总分：若 LLM 未给或与加权不符，按权重重算
        total = data.get("total_score")
        try:
            total = int(total)
        except (TypeError, ValueError):
            total = self._compute_total(dims)
        total = max(0, min(100, total))

        issues_raw = data.get("issues", []) or []
        issues: list[BookwormIssue] = []
        for it in issues_raw:
            if not isinstance(it, dict):
                continue
            severity = str(it.get("severity", "warn")).lower()
            if severity not in ("block", "warn"):
                severity = "warn"
            issues.append(
                BookwormIssue(
                    severity=severity,
                    description=str(it.get("description", "")),
                    location=str(it.get("location", "")),
                )
            )

        suggestions = [str(s) for s in (data.get("suggestions", []) or [])]
        reference = str(data.get("reference", ""))

        return BookwormReview(
            total_score=total,
            dimensions=dims,
            one_liner_feeling=str(data.get("one_liner_feeling", "")),
            issues=issues,
            suggestions=suggestions,
            reference=reference,
            input_snapshot=inp,
            version=version,
        )

    @staticmethod
    def _compute_total(dims: dict[str, int]) -> int:
        """按权重加权计算总分"""
        total = 0.0
        for key, weight in DIMENSION_WEIGHTS.items():
            total += dims.get(key, 0) * weight
        return int(round(total))

    def _save(self, review: BookwormReview, save_dir: Path) -> None:
        """保存测评结果到目录"""
        save_dir = Path(save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)
        v = review.version or "review"
        # JSON
        (save_dir / f"bookworm_review_{v}.json").write_text(
            review.to_json(), encoding="utf-8"
        )
        # MD
        (save_dir / f"bookworm_review_{v}.md").write_text(
            review.to_markdown(), encoding="utf-8"
        )


# ============================================================
# Skill 注册表（供 /load-skill 使用）
# ============================================================
class SkillRegistry:
    """已加载 skill 的注册表

    记录当前会话已加载的 skill 及其注册的命令。
    """

    # 内置 skill 名称 → 加载函数
    BUILTIN_SKILLS: dict[str, str] = {
        "bookworm": "agent.workflows.m15_bookworm:BookwormSkill",
    }

    def __init__(self) -> None:
        self.loaded: dict[str, BookwormSkill] = {}
        # 命令 → skill 名称
        self.command_to_skill: dict[str, str] = {}

    def load_builtin(
        self,
        name: str,
        llm: LLMClient | None = None,
        console: Console | None = None,
    ) -> BookwormSkill:
        """加载内置 skill

        Args:
            name: skill 名称（如 bookworm）

        Raises:
            ValueError: 未知 skill
        """
        if name not in self.BUILTIN_SKILLS:
            raise ValueError(
                f"未知 skill: {name}，内置 skill: {', '.join(self.BUILTIN_SKILLS)}"
            )
        if name == "bookworm":
            skill = BookwormSkill.load(llm=llm, console=console)
        else:  # pragma: no cover
            raise ValueError(f"skill {name} 未实现加载逻辑")
        self.loaded[name] = skill
        for cmd in skill.manifest.command_names:
            self.command_to_skill[cmd] = name
        return skill

    def get_skill(self, name: str) -> BookwormSkill | None:
        return self.loaded.get(name)

    def get_skill_for_command(self, command: str) -> BookwormSkill | None:
        skill_name = self.command_to_skill.get(command)
        if skill_name is None:
            return None
        return self.loaded.get(skill_name)

    def list_loaded(self) -> list[str]:
        return list(self.loaded.keys())

    def is_loaded(self, name: str) -> bool:
        return name in self.loaded
