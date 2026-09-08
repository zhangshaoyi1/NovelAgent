"""EvaluatorAgent 的报告结构与协议类型（自 evaluator.py 拆出）"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol

from agent.core.quality.scoring.reader_appeal import (  # to_markdown 子块渲染用
    APPEAL_LABELS,
    GOLDEN_GATE_PREFIX,
)


from agent.core.quality.dimension_registry import (  # HA-Eval L2：量纲契约 SSOT
    DimensionSpec,
    enforce_contract,
    get_spec,
)


@dataclass
class DimensionResult:
    """单维结果。

    HA-Eval L2（2026-09-08）：新增 ``spec`` 字段，把维度的量纲/值域/作用域声明
    绑定到结果上，构造时即执行契约校验——把「3 条缺陷」喂给 0-100 评分维这类
    量纲错配在**构造期**暴露，而不是等到回滚删完章才发现。

    向后兼容：``spec`` / ``evidence`` 均为可选参数且置于末尾，
    既有位置参数调用（``DimensionResult(name, label, value, threshold, direction,
    required, source, soft_margin=, scope=)``）零改动；``to_dict()`` 输出字段不变。
    """

    name: str
    label: str
    value: float
    threshold: float
    direction: str  # ">=" 或 "<="（value 与 threshold 的关系）
    required: bool  # 不可放宽（硬指标）
    source: str = ""  # computed | llm | default
    # G2 容差带：硬门禁恒 0；仅 coherence（0-100 量纲）用 5 吸收 LLM 噪声，其余保持严格。
    soft_margin: float = 0.0
    # 作用域声明：window=每轮窗口均评（默认）；
    # book_ending=全书收尾验收维，仅结局窗口内启用。
    # first_chapters=开头若干章（末窗回滚修不到）。登记表见 dimension_registry。
    scope: str = "window"
    # ---- HA-Eval L2：量纲契约（None → 由 registry 按 name 自动绑定）----
    spec: DimensionSpec | None = None
    # ---- HA-Eval L3：评分证据（prompt_hash / cache_hit / confidence…）----
    evidence: Any | None = None

    def __post_init__(self) -> None:
        if self.spec is None:
            self.spec = get_spec(self.name)
        if self.spec is not None:
            enforce_contract(self.spec, self.value)

    @property
    def unit(self) -> str:
        """量纲（未登记维度返回空串）。"""
        return self.spec.unit.value if self.spec is not None else ""

    @property
    def confidence(self) -> float:
        """证据置信度；0.0 表示不可信，**禁止触发任何处置动作**（L4 守门依据）。

        无证据（确定性计算维）恒为 1.0——computed 维度不依赖 LLM，天然可信。
        """
        if self.evidence is None:
            return 1.0
        return float(getattr(self.evidence, "confidence", 1.0))

    @property
    def passed(self) -> bool:
        # G2：引入容差带 soft_margin，边界合格章节不被误杀；劣质章节（远低于阈值）仍被抓。
        if self.direction == ">=":
            return self.value >= self.threshold - self.soft_margin - 1e-9
        return self.value <= self.threshold + self.soft_margin + 1e-9

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "value": self.value,
            "threshold": self.threshold,
            "direction": self.direction,
            "required": self.required,
            "source": self.source,
            "soft_margin": self.soft_margin,
            "scope": self.scope,
            "passed": self.passed,
        }


# G2 soft_margin 注入映射。
# HA-Eval L2（2026-09-08）：原为硬编码字典，现由 dimension_registry 登记表**派生**，
# 保证与 DimensionSpec.soft_margin 单一事实来源。保留名称以兼容既有 import。
from agent.core.quality.dimension_registry import (  # noqa: F401
    _SOFT_MARGIN,
)


@dataclass
class RepairPlan:
    """回溯修复方案。"""

    target_chapter: int
    chapters_to_rewrite: list[int]
    reason: str
    rolled_back: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_chapter": self.target_chapter,
            "chapters_to_rewrite": list(self.chapters_to_rewrite),
            "reason": self.reason,
            "rolled_back": self.rolled_back,
        }


@dataclass
class NovelHealthReport:
    """全书"不崩"体检报告。"""

    overall_pass: bool
    score: float = 0.0
    dimensions: list[DimensionResult] = field(default_factory=list)
    rolled_back: bool = False
    rollback_attempts: int = 0
    escalated: bool = False
    escalated_reason: str = ""
    repair: Optional[RepairPlan] = None
    notes: list[str] = field(default_factory=list)
    # G5：迷爱看（读者吸引力）六维子块（不崩与迷爱看双闸分离展示）
    appeal: Optional[dict] = None
    # ---- G6：三闸子块 ----
    golden_three: Optional[dict] = None   # B4：黄金三章（source/mode/total/verdict/六维明细）
    ai_flavor: Optional[dict] = None      # B5：AI 味命中（由 pipeline 在评测后回填）
    padding: Optional[dict] = None        # B6：防注水（重复度 + 信息密度）
    # ---- G7：人话总结层（主理人拍板 1/2：确定性模板拼装，零 LLM；表格前插总结段）----
    summary: Optional[dict] = None

    def dimension(self, name: str) -> Optional[DimensionResult]:
        return next((d for d in self.dimensions if d.name == name), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_pass": self.overall_pass,
            "score": round(self.score, 2),
            "dimensions": [d.to_dict() for d in self.dimensions],
            "rolled_back": self.rolled_back,
            "rollback_attempts": self.rollback_attempts,
            "escalated": self.escalated,
            "escalated_reason": self.escalated_reason,
            "repair": self.repair.to_dict() if self.repair else None,
            "notes": list(self.notes),
            "appeal": self.appeal,
            "golden_three": self.golden_three,
            "ai_flavor": self.ai_flavor,
            "padding": self.padding,
            # ---- G7（只增不删）：人话总结层 ----
            "summary": self.summary,
        }

    def to_markdown(self) -> str:
        lines = ["# 全书「不崩」体检报告"]
        lines.append("")
        verdict = "✅ 通过" if self.overall_pass else (
            "⚠️ 需人工介入" if self.escalated else "🔧 已触发自动回溯"
        )
        lines.append(f"- **总评**：{verdict}（综合分 {self.score:.1f}/100）")
        lines.append(f"- **回溯次数**：{self.rollback_attempts}　**已回退**：{self.rolled_back}")
        if self.escalated:
            lines.append(f"- **上报原因**：{self.escalated_reason}")
        lines.append("")
        # ---- G7：人话总结段（主理人拍板 2：表格前插入；不重构既有表格与 G5/G6 子块）----
        if self.summary is not None:
            s = self.summary
            lines.append("")
            lines.append("## 一句话总结")
            lines.append(f"- **{s.get('headline', '')}**")
            for f in s.get("failures", []):
                lines.append(f"- {f.get('line', '')}（{f.get('reason', '')}）")
                src = f.get("suggestion_source", "")
                if f.get("suggestion"):
                    lines.append(
                        f"  - 建议（{'来自 LLM' if src == 'llm' else '模板'}）：{f.get('suggestion')}"
                    )
            for ns in s.get("next_steps", []):
                lines.append(f"- 下一步：{ns}")
            lines.append("")
        lines.append("| 维度 | 指标 | 实测 | 合格线 | 达标 |")
        lines.append("|---|---|---|---|---|")
        for d in self.dimensions:
            mark = "✓" if d.passed else "✗"
            lines.append(
                f"| {d.label} | {d.name} | {d.value} | "
                f"{d.direction} {d.threshold} | {mark} |"
            )
        if self.repair:
            lines.append("")
            lines.append(
                f"**修复方案**：回退至第 {self.repair.target_chapter} 章，"
                f"重写 {self.repair.chapters_to_rewrite}，原因：{self.repair.reason}"
            )
        for n in self.notes:
            lines.append(f"- {n}")
        # ---- G5：迷爱看小节（仅当 appeal 子块存在）----
        if self.appeal is not None:
            a = self.appeal
            lines.append("")
            if a.get("source") == "offline":
                lines.append("## 迷爱看（离线通过，未实测）")
                lines.append(
                    "> LLM 不可用，六维门禁按「降级不阻断」判通过；"
                    "配置真实 LLM 后才会真实评估读者吸引力。"
                )
            else:
                lines.append("## 迷爱看（读者吸引力六维）")
                verdict = a.get("verdict", "")
                lines.append(
                    f"- **综合分**：{a.get('total_score', 0)}/{a.get('threshold', 60)}"
                    f"（{verdict}）　**达标**：{'✓' if a.get('passed') else '✗'}"
                )
                lines.append("")
                lines.append("| 维度 | 得分 | 触底线 | 达标 |")
                lines.append("|---|---|---|---|")
                dims = a.get("dimensions", {})
                for k, v in dims.items():
                    ok = v.get("score", 0) >= v.get("floor", 40)
                    mark = "✓" if ok else "✗"
                    lines.append(
                        f"| {APPEAL_LABELS.get(k, k)} | {v.get('score', 0)} "
                        f"| {v.get('floor', 40)} | {mark} |"
                    )
                if a.get("one_liner"):
                    lines.append(f"> {a.get('one_liner')}")

        # ---- G6：黄金三章小节 ----
        if self.golden_three is not None:
            g = self.golden_three
            lines.append("")
            if g.get("source") == "offline":
                lines.append("## 黄金三章（离线通过，未实测）")
                lines.append(
                    "> LLM 不可用，黄金三章门禁按「降级不阻断」判通过；"
                    "配置真实 LLM 后才会实测开局吸引力。"
                )
            else:
                lines.append("## 黄金三章（B4 开局门禁）")
                mode_txt = (
                    "三章拼接一次评分" if g.get("mode") == "join"
                    else "每章独立评分取最差（超长回退）"
                )
                lines.append(
                    f"- **综合分**：{g.get('total_score', 0)}/{g.get('threshold', 60)}"
                    f"（{g.get('verdict', '')}）　**达标**：{'✓' if g.get('passed') else '✗'}　"
                    f"（{mode_txt} · source={g.get('source', 'llm')}）"
                )
                lines.append("")
                lines.append("| 维度 | 得分 | 触底线 | 达标 |")
                lines.append("|---|---|---|---|")
                for k, v in g.get("dimensions", {}).items():
                    ok = v.get("score", 0) >= v.get("floor", 40)
                    mark = "✓" if ok else "✗"
                    lines.append(
                        f"| {APPEAL_LABELS.get(k, k)} | {v.get('score', 0)} "
                        f"| {v.get('floor', 40)} | {mark} |"
                    )
                if g.get("one_liner"):
                    lines.append(f"> {g.get('one_liner')}")

        # ---- G6：去 AI 味小节（标红，advisory 不阻断）----
        if self.ai_flavor is not None and self.ai_flavor.get("count", 0) > 0:
            a = self.ai_flavor
            lines.append("")
            lines.append("## 去 AI 味（⚠ 命中 AI 腔词句）")
            lines.append(f"- **模式**：{a.get('mode', 'advisory')}　**命中**：{a.get('count', 0)} 处")
            for hit in a.get("hits", []):
                lines.append(
                    f"- [red]第 {hit.get('chapter', '?')} 章：{hit.get('message', '')}[/red]"
                )

        # ---- G6：防注水小节 ----
        if self.padding is not None:
            p = self.padding
            lines.append("")
            lines.append("## 防注水（B6）")
            rep = p.get("repetition", {})
            rep_mark = "✓" if rep.get("passed") else "✗"
            lines.append(
                f"- **重复句占比**：{rep.get('ratio', 0)} ≤ {rep.get('threshold', 0.30)}　{rep_mark}"
                f"（{rep.get('repeated_sentences', 0)}/{rep.get('total_sentences', 0)} 句）"
            )
            den = p.get("info_density", {})
            if den.get("flagged"):
                lines.append(
                    f"- [yellow]⚠ 信息密度偏低：推进句占比 {den.get('advancing_ratio', 0)}"
                    f" < {den.get('floor', 0.25)}（软标红，仅提示）[/yellow]"
                )
        return "\n".join(lines)


# score_fn 签名：(维度名, project_dir) -> 该维度原始值（float）
ScoreFn = Callable[[str, str], float]
# rewriter 签名：(待重写章节号列表) -> None
RewriterFn = Callable[[list[int]], None]


class RollbackProvider(Protocol):
    """分叉点回滚能力接口（D-J 反向依赖修复）。

    接口定义在**消费方**（agents），由 workflow 层（``m10_rollback.M10RollbackWorkflow``）
    实现，经 ``EvaluatorAgent(rollback_provider=...)`` 构造注入，使 agents 不再直接
    import workflows。未注入时 ``trigger_rollback`` 懒加载 M10RollbackWorkflow 兜底。
    """

    def rollback_to_chapter(self, target_chapter: int) -> Any:
        """回滚到指定章节（1-based），返回含 ``success`` 字段的结果对象。"""
        ...

