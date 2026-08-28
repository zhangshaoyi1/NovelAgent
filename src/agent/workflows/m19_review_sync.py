"""M19 复核同步工作流（上游改动后，下游阶段的复核检查单）

当某阶段被确认后，其上游产物又被修改（mtime 基线变化 → 下游自动标记「待复核」），
本工作流调用 LLM 对比「上游最新内容」与「下游当前内容」，产出复核检查单：

- uncovered（未覆盖）：上游新增/改动但下游完全没回应的内容 → 需要补写
- conflict（冲突）  ：下游明确写出的内容与上游最新内容矛盾 → 需要改写

设计要点：
- 只读分析、不直接修改任何产物文件——决策权始终在作者（非破坏性原则）。
- 检查单由调用方（Web / CLI）保存与逐条裁决，本工作流只负责「找问题」。
- 阶段产物路径为静态数据（与 web/state.py 保持一致），不反向依赖 web 层。

用法：
    wf = M19ReviewSyncWorkflow(project_dir)
    result = wf.review(target_stage="outline", changed_upstreams=["architecture"])
    for f in result.findings: ...
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console

from agent.client import LLMClient
from agent.core.engine.workflow_registry import workflow
from agent.prompts import M19_REVIEW_SYSTEM_PROMPT, M19_REVIEW_USER_TEMPLATE
from agent.utils import parse_llm_json

# 阶段产物路径（目录时聚合目录内所有 md）。与 web/state.py 的 STAGE_FILES 保持一致。
STAGE_FILES: dict[str, str] = {
    "world": "world.md",
    "discussion": "discussion.md",
    "architecture": "architecture.md",
    "outline": "outline.md",
    "characters": "characters",
}

STAGE_LABEL: dict[str, str] = {
    "world": "设定世界",
    "discussion": "脉络讨论",
    "architecture": "故事架构",
    "outline": "创作大纲",
    "characters": "角色设计",
}

# 单段内容送入 prompt 的最大字符数（控制 tokens 成本）
MAX_SECTION_CHARS = 3000
MAX_UPSTREAM_TOTAL = 6000


@dataclass
class ReviewFinding:
    """单条复核发现"""

    kind: str  # uncovered | conflict
    severity: str  # high | medium | low
    target: str  # 下游受影响条目
    issue: str  # 问题描述
    upstream_ref: str = ""  # 上游对应内容节选
    suggestion: str = ""  # 建议处理方式

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ReviewFinding":
        return cls(
            kind=str(d.get("kind", "conflict")),
            severity=str(d.get("severity", "medium")),
            target=str(d.get("target", "")),
            issue=str(d.get("issue", "")),
            upstream_ref=str(d.get("upstream_ref", "")),
            suggestion=str(d.get("suggestion", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "target": self.target,
            "issue": self.issue,
            "upstream_ref": self.upstream_ref,
            "suggestion": self.suggestion,
        }


@dataclass
class M19ReviewResult:
    """复核检查单结果"""

    target_stage: str
    findings: list[ReviewFinding] = field(default_factory=list)
    summary: str = ""
    generated_at: str = ""


@workflow("m19_review_sync")
class M19ReviewSyncWorkflow:
    """M19 复核同步工作流：LLM 找出下游阶段的未覆盖 / 冲突"""

    def __init__(
        self,
        project_dir: Path | str,
        llm_client: LLMClient | None = None,
        console: Console | None = None,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.llm = llm_client or LLMClient()
        self.console = console or Console()

    # ============================================================
    # 入口
    # ============================================================
    def review(
        self,
        target_stage: str,
        changed_upstreams: list[str],
    ) -> M19ReviewResult:
        """生成目标下游阶段的复核检查单。

        Args:
            target_stage: 待复核的下游阶段 key（STAGE_FILES 之一）
            changed_upstreams: 自确认后发生改动的上游阶段 key 列表
                （由调用方按 mtime 基线推导，本工作流只负责读取内容对比）

        Returns:
            M19ReviewResult：未覆盖 / 冲突条目 + 总体结论
        """
        if target_stage not in STAGE_FILES:
            raise ValueError(f"未知阶段：{target_stage}")

        target_content = self._read_stage(target_stage, MAX_SECTION_CHARS * 2)
        upstream_parts: list[str] = []
        for up in changed_upstreams:
            if up in STAGE_FILES:
                label = STAGE_LABEL.get(up, up)
                content = self._read_stage(up, MAX_SECTION_CHARS)
                if content:
                    upstream_parts.append(f"【{label}】\n{content}")
        upstream_content = "\n\n".join(upstream_parts)
        if not upstream_content:
            raise ValueError("上游均无内容，无法复核")

        self.console.print(
            f"\n[cyan]正在复核「{STAGE_LABEL.get(target_stage, target_stage)}」"
            f"（上游改动：{', '.join(STAGE_LABEL.get(u, u) for u in changed_upstreams)}）...[/cyan]"
        )
        findings, summary = self._llm_review(
            target_stage, upstream_content[:MAX_UPSTREAM_TOTAL], target_content
        )

        result = M19ReviewResult(
            target_stage=target_stage,
            findings=findings,
            summary=summary,
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        self._present(result, changed_upstreams)
        return result

    # ============================================================
    # 内部：读取阶段内容
    # ============================================================
    def _read_stage(self, stage_key: str, max_chars: int) -> str:
        """读取阶段产物内容（目录时聚合所有 md；截断到 max_chars）。"""
        rel = STAGE_FILES.get(stage_key)
        if not rel:
            return ""
        p = self.project_dir / rel
        text = ""
        if p.is_file():
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
        elif p.is_dir():
            parts: list[str] = []
            try:
                for f in sorted(p.glob("*.md")):
                    if f.is_file():
                        parts.append(
                            f"## {f.stem}\n"
                            + f.read_text(encoding="utf-8", errors="replace")
                        )
            except OSError:
                pass
            text = "\n\n".join(parts)
        return text[:max_chars]

    # ============================================================
    # 内部：LLM 复核
    # ============================================================
    def _llm_review(
        self, target_stage: str, upstream_content: str, target_content: str
    ) -> tuple[list[ReviewFinding], str]:
        """调 LLM 产出检查单 JSON；解析失败/为空时降级为『未发现明显问题』。"""
        user_prompt = M19_REVIEW_USER_TEMPLATE.format(
            target_label=STAGE_LABEL.get(target_stage, target_stage),
            upstream_content=upstream_content or "（无）",
            target_content=target_content or "（无）",
        )
        last_text = ""
        for attempt in range(2):
            resp = self.llm.chat_creative(
                messages=[
                    {"role": "system", "content": M19_REVIEW_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.4,
                max_tokens=2000,
                enable_thinking=False,
            )
            last_text = resp.text
            try:
                data = parse_llm_json(resp.text)
                findings = [ReviewFinding.from_dict(f) for f in (data.get("findings") or [])]
                summary = str(data.get("summary", ""))
                return findings, summary
            except ValueError:
                if attempt == 0:
                    self.console.print(
                        "[yellow]⚠ 复核 JSON 解析失败，自动重试一次...[/yellow]"
                    )
        self.console.print(
            "[yellow]⚠ 复核 JSON 解析失败（重试后），降级为『未发现明显问题』。[/yellow]"
        )
        return [], last_text[:200] or "复核结果解析失败，请稍后重试。"

    # ============================================================
    # 呈现
    # ============================================================
    def _present(self, result: M19ReviewResult, changed_upstreams: list[str]) -> None:
        from rich.panel import Panel

        label = STAGE_LABEL.get(result.target_stage, result.target_stage)
        if not result.findings:
            self.console.print(
                Panel(
                    f"[green]✓ 未发现需调整的问题[/green]\n{result.summary}",
                    title=f"[bold]复核：{label}[/bold]",
                    border_style="green",
                )
            )
            return
        lines: list[str] = []
        for f in result.findings:
            tag = "冲突" if f.kind == "conflict" else "未覆盖"
            lines.append(f"  • [{tag}] {f.target}：{f.issue}")
            if f.suggestion:
                lines.append(f"    建议：{f.suggestion}")
        self.console.print(
            Panel(
                f"[bold]「{label}」复核检查单[/bold] · 共 {len(result.findings)} 条\n"
                + "\n".join(lines)
                + f"\n\n{result.summary}",
                title=f"[bold]复核：{label}[/bold]",
                border_style="cyan",
            )
        )
