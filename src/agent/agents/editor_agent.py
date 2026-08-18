"""EditorAgent —— 主编 / 一致性仲裁（Phase 2 多智能体团队之一）

职责（设计文档 §2.3 Editor）：一致性仲裁——关系 / 伏笔 / 设定冲突检测与解决，
冻结字段守护。对单章草稿做"出版前"一致性审查，输出 ``EditReport``。

复用现有能力（不推倒重来）：
- ``core.consistency_checker.ConsistencyChecker``：内置规则集（字段冲突委托
  ConflictArbiter、境界越级、金手指越界、关系网一致性、时间线）。
- ``relations/graph.md`` / ``foreshadows.md``：原始 markdown（RelationManager /
  ForeshadowManager 仍是 stub，故直接读文件，与 ContextLoader / M10ResumeWorkflow 同策略）。

离线友好：
- ``consistency_fn`` 可注入（离线测试用），不注入则走真实 ConsistencyChecker
  （其 LLM 部分失败会优雅降级为空冲突，不阻断）。
- 冻结字段守卫为确定性启发式（无需 LLM），可独立测试。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from rich.console import Console

from agent.core.consistency_checker import CheckTrigger, ConsistencyChecker, Severity


@dataclass
class EditConflict:
    """单条一致性冲突（归一化自 ConsistencyChecker.Conflict）。"""

    rule_id: str
    severity: str  # block | warn
    description: str
    suggestions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "description": self.description,
            "suggestions": list(self.suggestions),
        }


@dataclass
class EditReport:
    """编辑审查报告。"""

    passed: bool
    conflicts: list[EditConflict] = field(default_factory=list)
    frozen_violations: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)

    @property
    def block_count(self) -> int:
        return sum(1 for c in self.conflicts if c.severity == "block")

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "block_count": self.block_count,
            "conflicts": [c.to_dict() for c in self.conflicts],
            "frozen_violations": list(self.frozen_violations),
            "suggestions": list(self.suggestions),
        }

    def to_markdown(self) -> str:
        lines = ["# 编辑审查报告"]
        lines.append("")
        lines.append(f"- **结论**：{'通过' if self.passed else '存在需处理项'}")
        lines.append(f"- **阻断项**：{self.block_count}")
        if self.conflicts:
            lines.append("")
            lines.append("## 一致性冲突")
            for c in self.conflicts:
                lines.append(
                    f"- [{c.severity}] {c.rule_id}：{c.description}"
                )
                for s in c.suggestions:
                    lines.append(f"  - 建议：{s}")
        if self.frozen_violations:
            lines.append("")
            lines.append("## 冻结字段违例")
            for v in self.frozen_violations:
                lines.append(f"- {v}")
        if self.suggestions:
            lines.append("")
            lines.append("## 其他建议")
            for s in self.suggestions:
                lines.append(f"- {s}")
        return "\n".join(lines)


# 注入的冲突检测函数签名：(project_dir, chapter_text, ctx) -> list[EditConflict]
ConsistencyFn = Callable[[str, str, Any], list[EditConflict]]

# 冻结字段违例启发式：当实体被提及且附近出现否定/改写词时，标记疑似违例。
_NEGATION_NEAR = re.compile(r"(不是|并非|已经死了|牺牲了|不再是|改名为|其实是)")


class EditorAgent:
    """主编 Agent：单章一致性仲裁 + 冻结字段守护。

    Args:
        project_dir: 小说项目目录。
        llm_client: LLM（供 ConsistencyChecker 的 LLM 规则使用；可None）。
        console: rich 控制台。
        consistency_fn: 注入冲突检测（离线测试用）；不传走真实 ConsistencyChecker。
        frozen_fields: 冻结的权威事实（canonical 陈述）；默认从整合记忆读取。
    """

    def __init__(
        self,
        project_dir: str | Path,
        llm_client: Any = None,
        console: Console | None = None,
        consistency_fn: ConsistencyFn | None = None,
        frozen_fields: list[str] | None = None,
        memory: Any = None,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.llm = llm_client
        self.console = console or Console()
        self.consistency_fn = consistency_fn
        self.memory = memory
        self.frozen_fields = list(frozen_fields) if frozen_fields is not None else None
        self._checker = ConsistencyChecker(self.project_dir)

    # ---------------------------------------------------------------- 冲突检测
    def _detect_conflicts(
        self, chapter_text: str, ctx: Any
    ) -> list[EditConflict]:
        if self.consistency_fn is not None:
            return self.consistency_fn(str(self.project_dir), chapter_text, ctx)
        # 真实路径：ConsistencyChecker（LLM 部分失败会优雅降级为空）
        try:
            report = self._checker.check(
                CheckTrigger.POST_WRITE,
                ctx={"chapter_text": chapter_text, **(ctx or {})},
            )
            return [
                EditConflict(
                    rule_id=c.rule_id,
                    severity=("block" if c.severity == Severity.BLOCK else "warn"),
                    description=c.description,
                    suggestions=list(c.suggestions),
                )
                for c in report.conflicts
            ]
        except Exception:  # noqa: BLE001
            return []

    # ---------------------------------------------------------------- 冻结字段
    def _resolve_frozen_fields(self) -> list[str]:
        if self.frozen_fields is not None:
            return self.frozen_fields
        if self.memory is not None:
            try:
                facts = self.memory.consolidated.get("facts", []) or []
                # 以"冻结:"前缀标记权威事实
                frozen = [f for f in facts if f.startswith("冻结:")]
                if frozen:
                    return frozen
            except Exception:  # noqa: BLE001
                pass
        return []

    def _check_frozen(self, chapter_text: str, frozen_fields: list[str]) -> list[str]:
        """确定性启发式：实体被提及且附近出现否定/改写词 → 疑似违例。"""
        violations: list[str] = []
        for fact in frozen_fields:
            # 取实体内核（"冻结:主角名为林轩" → "主角名为林轩"）
            core = fact.split(":", 1)[-1] if ":" in fact else fact
            # 提取候选实体：CJK 连续串（≥2）；对长串额外取首2/尾2（名字常为结尾）
            entities: list[str] = []
            for run in re.findall(r"[\u4e00-\u9fff]{2,}", core):
                entities.append(run)
                if len(run) >= 3:
                    entities.append(run[:2])
                    entities.append(run[-2:])
            entities += re.findall(r"[A-Za-z]{2,}", core)
            for ent in entities:
                for m in re.finditer(re.escape(ent), chapter_text):
                    start = max(0, m.start() - 24)
                    window = chapter_text[start : m.end() + 24]
                    if _NEGATION_NEAR.search(window):
                        violations.append(
                            f"冻结事实「{fact}」疑似被改写（章节中出现「{ent}」附近的否定表述）"
                        )
                        break
        return violations

    # ---------------------------------------------------------------- 主入口
    def review(self, chapter_text: str, ctx: Any = None) -> EditReport:
        """审查单章草稿。

        Returns:
            EditReport（passed 为真当且仅当无 block 冲突且无冻结违例）
        """
        conflicts = self._detect_conflicts(chapter_text, ctx)
        frozen_fields = self._resolve_frozen_fields()
        violations = self._check_frozen(chapter_text, frozen_fields)

        suggestions: list[str] = []
        for c in conflicts:
            suggestions.extend(c.suggestions)
        if violations:
            suggestions.append("请核实并修正冻结字段违例，或显式在设定集中更新该事实。")

        passed = (not any(c.severity == "block" for c in conflicts)) and (not violations)
        report = EditReport(
            passed=passed,
            conflicts=conflicts,
            frozen_violations=violations,
            suggestions=suggestions,
        )
        if self.memory is not None:
            try:
                self.memory.log(
                    "edit",
                    f"编辑审查{'通过' if passed else '有冲突'}",
                    {"passed": passed, "block": report.block_count},
                )
            except Exception:  # noqa: BLE001
                pass
        return report

    def review_chapter_file(self, chapter_path: str | Path, ctx: Any = None) -> EditReport:
        """读取章节文件（frontmatter + 正文）后审查。"""
        import frontmatter

        post = frontmatter.load(Path(chapter_path))
        return self.review(post.content, ctx)
