"""M21 成书质量评审工作流（多视角对抗式评审）

移植自 oh-story-claudecod 的 story-review skill，落地为 NovelAgent 工作流：

- 多视角对抗式评审：4 个评审维度各一次 LLM 独立评审
  （结构架构 architect / 设定一致性 consistency / 读者市场吸引力 reader / 埋线与伏笔 foreshadow），
  再综合裁决一次 LLM（verdict）。
- mode 参数：full（4 视角）/ lean（2 视角：结构+一致性）/ solo（1 视角综合）。
- platform 参数：fanqie / qidian / zhihu 加载对应平台 rubric 注入评审提示词，
  默认 general 使用内置通用 rubric（quality-rubric.md 精简）。
- 只读分析、不修改任何产物文件；报告写入 ``{project_dir}/.state/review/review-*.md``。

用法：
    wf = M21ReviewWorkflow(project_dir)
    report = wf.review(scope="all", mode="full", platform="general")
    print(report.to_json())
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import frontmatter
from rich.console import Console

from agent.client.gateway_adapter import create_gateway, chat_utility
from llmagent.gateway import Gateway
from agent.core.engine.workflow_registry import workflow
from agent.core.infra.prompt_manager import pm
from agent.utils import parse_llm_json

# ============================================================
# 常量
# ============================================================

# 各 mode 对应的评审维度（key, label）
MODE_DIMENSIONS: dict[str, list[tuple[str, str]]] = {
    "full": [
        ("architect", "结构架构"),
        ("consistency", "设定一致性"),
        ("reader", "读者市场吸引力"),
        ("foreshadow", "埋线与伏笔"),
    ],
    "lean": [
        ("architect", "结构架构"),
        ("consistency", "设定一致性"),
    ],
    "solo": [],  # solo 用 verdict 提示词做单视角综合评审
}

VALID_MODES = ("full", "lean", "solo")

# platform → prompt 键（pm.get 加载，正文即 rubric 文本）
PLATFORM_RUBRICS: dict[str, str] = {
    "fanqie": "m21.fanqie",
    "qidian": "m21.qidian",
    "zhihu": "m21.zhihu",
}

# 默认通用 rubric（quality-rubric.md 的 authoring 类标准精简）
GENERAL_RUBRIC = """# 通用质量评分标准（authoring 类）

| 检查项 | PASS | FAIL |
|--------|------|------|
| 输出格式 | 格式规范（一段一句、无空行、对话格式） | 存在格式违规 |
| 情绪连贯 | 情绪有起伏且有转折 | 情绪平直无变化 |
| 设定一致 | 与已有设定不矛盾 | 存在设定矛盾 |
| 钩子密度 | 每章有钩子 | 连续章节无钩子 |
"""

# 单段内容送入 prompt 的最大字符数（控制 tokens 成本）
MAX_SECTION_CHARS = 3000
MAX_SCOPE_CHARS = 12000  # 评审范围正文上限
MAX_CONTEXT_CHARS = 8000  # 项目设定参考上限

# ============================================================
# 数据契约
# ============================================================
@dataclass
class ReviewIssue:
    """单条评审问题"""

    severity: str  # block | warn
    location: str  # 问题位置
    description: str  # 问题描述
    suggestion: str = ""  # 修改建议

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ReviewIssue":
        return cls(
            severity=str(d.get("severity", "warn")).lower(),
            location=str(d.get("location", "")),
            description=str(d.get("description", "")),
            suggestion=str(d.get("suggestion", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DimensionResult:
    """单个评审维度的结果"""

    key: str  # architect | consistency | reader | foreshadow | solo
    label: str
    verdict: str  # APPROVE | CONCERNS | REJECT
    issues: list[ReviewIssue] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "verdict": self.verdict,
            "issues": [i.to_dict() for i in self.issues],
            "summary": self.summary,
        }


@dataclass
class ReviewReport:
    """评审报告"""

    mode: str
    platform: str
    scope: str
    generated_at: str
    dimensions: list[DimensionResult] = field(default_factory=list)
    overall_verdict: str = "CONCERNS"  # APPROVE | CONCERNS | REJECT
    total_score: int = 0
    issues: list[ReviewIssue] = field(default_factory=list)
    verdict_text: str = ""
    recommendations: list[str] = field(default_factory=list)
    disagreements: list[str] = field(default_factory=list)
    report_file: Path | None = None

    # ------ JSON 形态 ------
    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "platform": self.platform,
            "scope": self.scope,
            "generated_at": self.generated_at,
            "overall_verdict": self.overall_verdict,
            "total_score": self.total_score,
            "dimensions": [d.to_dict() for d in self.dimensions],
            "issues": [i.to_dict() for i in self.issues],
            "verdict_text": self.verdict_text,
            "recommendations": list(self.recommendations),
            "disagreements": list(self.disagreements),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    # ------ Markdown 形态 ------
    def to_markdown(self) -> str:
        lines: list[str] = []
        lines.append("# 成书质量评审报告")
        lines.append("")
        lines.append(f"- **评审模式**：{self.mode}")
        lines.append(f"- **目标平台**：{self.platform}")
        lines.append(f"- **评审范围**：{self.scope}")
        lines.append(f"- **生成时间**：{self.generated_at}")
        lines.append("")
        lines.append(f"## 综合评定：{self.overall_verdict} · {self.total_score}/100")
        lines.append("")
        if self.verdict_text:
            lines.append(f"> {self.verdict_text}")
            lines.append("")
        # 各视角
        if self.dimensions:
            lines.append("## 分视角评审")
            lines.append("")
            for d in self.dimensions:
                lines.append(f"### {d.label}（{d.verdict}）")
                lines.append("")
                if d.issues:
                    for i in d.issues:
                        icon = "🚫" if i.severity == "block" else "⚠️"
                        loc = f" `{i.location}`" if i.location else ""
                        lines.append(f"- {icon} **[ {i.severity.upper()} ]**{loc} {i.description}")
                else:
                    lines.append("- 未发现问题")
                if d.summary:
                    lines.append(f"\n{d.summary}")
                lines.append("")
        # 问题清单
        lines.append("## 问题清单")
        lines.append("")
        if self.issues:
            for i in self.issues:
                icon = "🚫" if i.severity == "block" else "⚠️"
                loc = f" `{i.location}`" if i.location else ""
                lines.append(f"- {icon} **[ {i.severity.upper()} ]**{loc} {i.description}")
                if i.suggestion:
                    lines.append(f"  - 建议：{i.suggestion}")
        else:
            lines.append("未发现明显问题。")
        lines.append("")
        # 修改建议
        if self.recommendations:
            lines.append("## 修改建议")
            lines.append("")
            for n, r in enumerate(self.recommendations, 1):
                lines.append(f"{n}. {r}")
            lines.append("")
        # 分歧
        if self.disagreements:
            lines.append("## 评审分歧")
            lines.append("")
            for d in self.disagreements:
                lines.append(f"- {d}")
            lines.append("")
        return "\n".join(lines)


# ============================================================
# 工作流
# ============================================================
@workflow("m21_review")
class M21ReviewWorkflow:
    """M21 成书质量评审工作流：多视角对抗式评审 + 综合裁决"""

    def __init__(
        self,
        project_dir: Path | str,
        llm_client: Gateway | None = None,
        console: Console | None = None,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.llm = llm_client or create_gateway()
        self.console = console or Console()

    # ============================================================
    # 入口
    # ============================================================
    def review(
        self,
        scope: str = "all",
        mode: str = "full",
        platform: str = "general",
        save: bool = True,
    ) -> ReviewReport:
        """执行成书质量评审。

        Args:
            scope: 评审范围：all / latest / 1-10 / 1,3,5（逗号/区间混用）。
            mode: 评审模式：full（4 视角）/ lean（2 视角）/ solo（1 视角综合）。
            platform: 目标平台 rubric：fanqie / qidian / zhihu / general。
            save: 是否把报告写入 ``.state/review/review-*.md``。

        Returns:
            ReviewReport
        """
        mode = (mode or "full").strip().lower()
        platform = (platform or "general").strip().lower()
        if mode not in VALID_MODES:
            raise ValueError(f"非法 mode：{mode}，可选：{' / '.join(VALID_MODES)}")
        if platform not in PLATFORM_RUBRICS and platform != "general":
            raise ValueError(
                f"非法 platform：{platform}，可选：general / {' / '.join(PLATFORM_RUBRICS)}"
            )

        scope_text = self._read_scope(scope)
        if not scope_text:
            raise ValueError(f"评审范围内没有可用的章节内容（scope={scope}）")

        context_text = self._read_context()
        rubric = self._load_rubric(platform)

        self.console.print(
            f"\n[cyan]开始成书质量评审[/cyan] · 模式 [bold]{mode}[/bold]"
            f" · 平台 [bold]{platform}[/bold] · 范围 [bold]{scope}[/bold]"
        )

        dim_results: list[DimensionResult] = []
        if mode != "solo":
            for key, label in MODE_DIMENSIONS[mode]:
                dim = self._dimension_review(key, label, scope_text, context_text, rubric)
                dim_results.append(dim)
        else:
            # solo：verdict 提示词即单视角综合评审
            dim_results = []

        dimensions_summary = self._dimensions_summary(dim_results)
        (
            overall,
            total_score,
            issues,
            verdict_text,
            recommendations,
            disagreements,
        ) = self._verdict_review(scope_text, context_text, rubric, dimensions_summary)

        if mode == "solo":
            # 把综合评审同时呈现为一个视角结果
            dim_results = [
                DimensionResult(
                    key="solo",
                    label="综合评审",
                    verdict=overall,
                    issues=list(issues),
                    summary=verdict_text,
                )
            ]

        report = ReviewReport(
            mode=mode,
            platform=platform,
            scope=scope,
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            dimensions=dim_results,
            overall_verdict=overall,
            total_score=total_score,
            issues=self._merge_issues(dim_results, issues),
            verdict_text=verdict_text,
            recommendations=recommendations,
            disagreements=disagreements,
        )

        if save:
            report.report_file = self._save_report(report)

        self._present(report)
        return report

    # ============================================================
    # 内部：读取项目内容
    # ============================================================
    def _parse_scope(self, scope: str, max_chapter: int) -> list[int]:
        """解析 --scope：'all' / '1-10' / '1,3,5' / '3-5,8' -> 章节号列表（升序去重）。"""
        scope = (scope or "").strip().lower()
        if scope in ("", "all", "latest"):
            if scope == "latest":
                return [max_chapter] if max_chapter >= 1 else []
            return list(range(1, max_chapter + 1))
        nums: set[int] = set()
        for part in scope.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                a, _, b = part.partition("-")
                lo, hi = int(a), int(b)
                nums.update(range(lo, hi + 1))
            else:
                nums.add(int(part))
        return sorted(n for n in nums if 1 <= n <= max_chapter)

    def _chapter_paths(self, chapters: list[int]) -> list[tuple[int, Path]]:
        """映射章节号 -> 章节文件（跳过不存在的文件）。"""
        out: list[tuple[int, Path]] = []
        for n in chapters:
            f = self.project_dir / "chapters" / f"ch{n:03d}.md"
            if f.exists():
                out.append((n, f))
        return out

    def _load_body(self, f: Path) -> str:
        """读取章节正文（去掉 frontmatter）。"""
        try:
            post = frontmatter.load(f)
            return (post.content or "").strip()
        except Exception:  # noqa: BLE001 - 单个文件解析失败降级为整文件文本
            return f.read_text(encoding="utf-8", errors="replace").strip()

    def _read_scope(self, scope: str) -> str:
        """读取评审范围内的章节正文，拼接为 scope_text（截断到 MAX_SCOPE_CHARS）。"""
        chapters_dir = self.project_dir / "chapters"
        max_chapter = max(
            (int(f.stem[2:]) for f in chapters_dir.glob("ch*.md") if f.stem[2:].isdigit()),
            default=0,
        )
        chapters = self._parse_scope(scope, max_chapter)
        paths = self._chapter_paths(chapters)
        parts: list[str] = []
        total = 0
        for n, f in paths:
            body = self._load_body(f)[:MAX_SECTION_CHARS]
            if not body:
                continue
            parts.append(f"## ch{n:03d}.md\n{body}")
            total += len(body)
            if total >= MAX_SCOPE_CHARS:
                break
        return "\n\n".join(parts)[:MAX_SCOPE_CHARS]

    def _read_context(self) -> str:
        """读取项目设定参考（world/outline/architecture/characters），拼接为 context_text。"""
        parts: list[str] = []
        total = 0
        for label, rel in (
            ("world.md", "world.md"),
            ("outline.md", "outline.md"),
            ("architecture.md", "architecture.md"),
        ):
            p = self.project_dir / rel
            if not p.is_file():
                continue
            text = p.read_text(encoding="utf-8", errors="replace")[:MAX_SECTION_CHARS]
            if text.strip():
                parts.append(f"## {label}\n{text}")
                total += len(text)
        chars_dir = self.project_dir / "characters"
        if chars_dir.is_dir():
            char_parts: list[str] = []
            for f in sorted(chars_dir.glob("*.md")):
                if not f.is_file():
                    continue
                text = f.read_text(encoding="utf-8", errors="replace")[:MAX_SECTION_CHARS]
                if text.strip():
                    char_parts.append(f"### {f.stem}\n{text}")
            if char_parts:
                block = "\n\n".join(char_parts)
                parts.append("## characters/\n" + block[:MAX_CONTEXT_CHARS])
                total += min(len(block), MAX_CONTEXT_CHARS)
        return "\n\n".join(parts)[:MAX_CONTEXT_CHARS]

    def _load_rubric(self, platform: str) -> str:
        """加载平台 rubric；general 用内置通用 rubric。"""
        key = PLATFORM_RUBRICS.get(platform)
        if key is None:
            return GENERAL_RUBRIC
        try:
            return pm.get(key).system or GENERAL_RUBRIC
        except KeyError:
            return GENERAL_RUBRIC

    # ============================================================
    # 内部：LLM 评审
    # ============================================================
    def _dimension_review(
        self,
        key: str,
        label: str,
        scope_text: str,
        context_text: str,
        rubric: str,
    ) -> DimensionResult:
        """单维度 LLM 独立评审；解析失败降级（不阻断）。"""
        prompt = pm.get(f"m21.{key}")
        messages = [
            {"role": "system", "content": prompt.system},
            {
                "role": "user",
                "content": prompt.render_user(
                    scope_text=scope_text,
                    context_text=context_text,
                    platform_rubric=rubric,
                ),
            },
        ]
        self.console.print(f"[cyan]· {label}视角评审中...[/cyan]")
        try:
            resp = chat_utility(self.llm, messages=messages, temperature=0.3, enable_thinking=False)
            data = parse_llm_json(resp)
            return self._parse_dimension(key, label, data)
        except ValueError:
            self.console.print(
                f"[yellow]⚠ {label}评审 JSON 解析失败，降级为空结果。[/yellow]"
            )
            return DimensionResult(
                key=key,
                label=label,
                verdict="CONCERNS",
                issues=[
                    ReviewIssue(
                        severity="warn",
                        location="",
                        description=f"{label}评审结果解析失败，未生成结构化问题清单",
                        suggestion="重试该维度评审",
                    )
                ],
                summary="⚠ 解析失败",
            )

    def _verdict_review(
        self,
        scope_text: str,
        context_text: str,
        rubric: str,
        dimensions_summary: str,
    ) -> tuple[str, int, list[ReviewIssue], str, list[str], list[str]]:
        """综合裁决 LLM；解析失败降级。"""
        prompt = pm.get("m21.verdict")
        messages = [
            {"role": "system", "content": prompt.system},
            {
                "role": "user",
                "content": prompt.render_user(
                    dimensions_summary=dimensions_summary,
                    scope_text=scope_text,
                    context_text=context_text,
                    platform_rubric=rubric,
                ),
            },
        ]
        self.console.print("[cyan]· 综合裁决中...[/cyan]")
        try:
            resp = chat_utility(self.llm, messages=messages, temperature=0.3, enable_thinking=False)
            data = parse_llm_json(resp)
            return self._parse_verdict(data)
        except ValueError:
            self.console.print(
                "[yellow]⚠ 综合裁决 JSON 解析失败，降级为 CONCERNS。[/yellow]"
            )
            return "CONCERNS", 0, [], "⚠ 综合裁决解析失败。", [], []

    # ============================================================
    # 内部：解析
    # ============================================================
    def _parse_issues(self, raw: Any) -> list[ReviewIssue]:
        """解析问题项列表，规范化 severity 为 block/warn。"""
        issues: list[ReviewIssue] = []
        for it in (raw or []):
            if not isinstance(it, dict):
                continue
            severity = str(it.get("severity", "warn")).lower()
            if severity not in ("block", "warn"):
                severity = "warn"
            issues.append(
                ReviewIssue(
                    severity=severity,
                    location=str(it.get("location", "")),
                    description=str(it.get("description", "")),
                    suggestion=str(it.get("suggestion", "")),
                )
            )
        return issues

    def _parse_dimension(self, key: str, label: str, data: dict[str, Any]) -> DimensionResult:
        verdict = str(data.get("verdict", "CONCERNS")).upper()
        if verdict not in ("APPROVE", "CONCERNS", "REJECT"):
            verdict = "CONCERNS"
        return DimensionResult(
            key=key,
            label=label,
            verdict=verdict,
            issues=self._parse_issues(data.get("issues", [])),
            summary=str(data.get("summary", "")),
        )

    def _parse_verdict(
        self, data: dict[str, Any]
    ) -> tuple[str, int, list[ReviewIssue], str, list[str], list[str]]:
        overall = str(data.get("overall_verdict", "CONCERNS")).upper()
        if overall not in ("APPROVE", "CONCERNS", "REJECT"):
            overall = "CONCERNS"
        try:
            total = int(data.get("total_score", 0))
        except (TypeError, ValueError):
            total = 0
        total = max(0, min(100, total))
        issues = self._parse_issues(data.get("issues", []))
        verdict_text = str(data.get("verdict_text", ""))
        recommendations = [str(r) for r in (data.get("recommendations", []) or [])]
        disagreements = [str(d) for d in (data.get("disagreements", []) or [])]
        return overall, total, issues, verdict_text, recommendations, disagreements

    @staticmethod
    def _dimensions_summary(dims: list[DimensionResult]) -> str:
        """把各维度结果拼成 verdict 提示词的上下文。"""
        if not dims:
            return "（无分视角评审，请直接综合评审全文）"
        parts: list[str] = []
        for d in dims:
            issue_text = "；".join(
                f"[{i.severity}] {i.description}" for i in d.issues
            ) or "无"
            parts.append(f"## {d.label}（{d.verdict}）\n问题：{issue_text}\n总结：{d.summary}")
        return "\n\n".join(parts)

    @staticmethod
    def _merge_issues(
        dims: list[DimensionResult], verdict_issues: list[ReviewIssue]
    ) -> list[ReviewIssue]:
        """合并各视角与裁决的问题，去重（description+location 相同视为重复），block 优先。"""
        seen: set[tuple[str, str]] = set()
        merged: list[ReviewIssue] = []
        for i in verdict_issues:
            key = (i.description, i.location)
            if key in seen:
                continue
            seen.add(key)
            merged.append(i)
        for d in dims:
            for i in d.issues:
                key = (i.description, i.location)
                if key in seen:
                    continue
                seen.add(key)
                merged.append(i)
        merged.sort(key=lambda x: 0 if x.severity == "block" else 1)
        return merged

    # ============================================================
    # 内部：保存与呈现
    # ============================================================
    def _save_report(self, report: ReviewReport) -> Path:
        """报告写入 {project_dir}/.state/review/review-{YYYYmmdd-HHMMSS}.md。"""
        out_dir = self.project_dir / ".state" / "review"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = out_dir / f"review-{ts}.md"
        path.write_text(report.to_markdown(), encoding="utf-8")
        return path

    def _present(self, report: ReviewReport) -> None:
        """终端呈现报告摘要。"""
        from rich.panel import Panel

        color = {
            "APPROVE": "green",
            "CONCERNS": "yellow",
            "REJECT": "red",
        }.get(report.overall_verdict, "yellow")
        block = sum(1 for i in report.issues if i.severity == "block")
        warn = sum(1 for i in report.issues if i.severity == "warn")
        self.console.print(
            Panel(
                f"[bold {color}]{report.overall_verdict}[/bold {color}] · "
                f"{report.total_score}/100 · 问题 block {block} / warn {warn}\n"
                f"[italic]{report.verdict_text}[/italic]",
                title="成书质量评审",
                border_style=color,
            )
        )
        if report.report_file:
            self.console.print(f"[dim]报告已保存：{report.report_file}[/dim]")
