"""处置方案语义终审（HA-Eval L4 第五道守门，登记单 20261001_信任链与叙事上限六项能力·子项 2）

背景
----
L4 处置层的四道守门（置信度/代价/双证据/dry-run）全部是**确定性**检查——它们能拦
「证据不可信」「代价超限」，但拦不住「方案本身在语义上会破坏这本书」。例如体检判
人设崩坏授权回滚重写，而守门器看不到：本书的弧光设计本就允许这个"崩坏"（登记的
成长轨迹），销毁末窗反而把对的改错。

设计
----
- 定位：**处置层终审法官**，不是全能监工——只审 ROLLBACK_REWRITE 级处置的
  **修复方案本身**，经 :class:`~agent.core.quality.disposition.DispositionGate`
  以第五道守门接入（注入式 ``semantic_review_fn``，core/quality 内聚，无跨层依赖）。
- 证据源：处置计划（动作/原因/失败维度明细）+ 影响章数 + Supervisor advisory
  告警（经 ``supervisor_alerts_fn`` 注入——由 workflows 层供给，避免
  core/quality → core/supervisor 的子包级新增依赖）。
- 三态结论：``allow``（放行）/ ``veto``（否决 → 守门器拒绝，升级人工）/
  ``unavailable``（LLM 不可用 → **降级为现状行为**，即四道守门语义不变，
  不新增裸奔面；degrade 显性留痕）。
- 一切裁决留痕：每次 review 追加一条 ``quality_audit.jsonl`` 快照
  （``pass_scope=disposition_review``，维度 ``disposition_semantic_review``），
  ``eval_audit`` 可回放；veto 同时经守门器拒绝路径进入 escalated_reason
  → pipeline failure 事件 → Web 运行控制台可见。
- 开关：``NOVELAGENT_DISPOSITION_REVIEW=0`` 关闭（关闭也经审计留痕）。
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from pydantic import BaseModel

from agent.core.infra.degrade import degrade


class DispositionReviewSchema(BaseModel):
    """语义终审结构化输出契约。"""

    verdict: str  # "allow" | "veto"（Literal 校验在 review() 内做，容错更友好）
    reason: str = ""


@dataclass
class SemanticReviewVerdict:
    """一次语义终审的结论。"""

    decision: str  # allow / veto / unavailable / disabled
    reason: str = ""
    #: llm（真实裁决）/ degraded（LLM 不可用降级）/ disabled（开关关闭）
    source: str = "llm"

    @property
    def ok(self) -> bool:
        """守门器视角：veto 之外一律放行（unavailable/disabled = 现状行为）。"""
        return self.decision != "veto"


class DispositionSemanticReviewer:
    """处置方案语义终审法官（LLM）。

    Args:
        llm: LLM 客户端（经 Gateway，core→client 合规）。
        project_dir: 项目目录（审计快照落 ``<project>/.state/quality_audit.jsonl``）。
        console: rich console（可选，仅日志）。
        supervisor_alerts_fn: Supervisor advisory 告警供给器（``() -> list[str]``），
            由调用方注入；None 则终审证据不含监督告警。
        enabled: 显式开关；None 时读环境变量 ``NOVELAGENT_DISPOSITION_REVIEW``。
    """

    def __init__(
        self,
        llm: Any,
        project_dir: Any = None,
        console: Any = None,
        supervisor_alerts_fn: Callable[[], list[str]] | None = None,
        enabled: bool | None = None,
    ) -> None:
        self.llm = llm
        self.project_dir = project_dir
        self.console = console
        self.supervisor_alerts_fn = supervisor_alerts_fn
        if enabled is None:
            enabled = os.getenv("NOVELAGENT_DISPOSITION_REVIEW", "1") != "0"
        self.enabled = bool(enabled)

    # ---------------------------------------------------------------- 提示与调用
    def _build_messages(self, plan: Any, chapters: int, alerts: Sequence[str]) -> list[dict[str, str]]:
        from agent.core.infra.prompt_manager import pm

        dim_lines = []
        for d in getattr(plan, "dims", []) or []:
            name = str(getattr(d, "name", "?"))
            label = str(getattr(d, "label", name))
            value = getattr(d, "value", "?")
            desc = str(getattr(d, "description", "") or "")[:200]
            dim_lines.append(f"- {label}({name})：value={value}；{desc}" if desc else f"- {label}({name})：value={value}")
        alert_lines = [f"- {a}" for a in alerts[:8]]
        user = pm.get("quality.disposition_review").render_user(
            action=str(getattr(plan, "action", "")),
            chapters=chapters,
            reason=str(getattr(plan, "reason", "")),
            dim_lines="\n".join(dim_lines) or "（无明细）",
            alert_lines="\n".join(alert_lines) or "（无）",
        )
        return [
            {"role": "system", "content": pm.get("quality.disposition_review").system},
            {"role": "user", "content": user},
        ]

    def _audit(self, verdict: SemanticReviewVerdict, plan: Any, chapters: int) -> None:
        """裁决留痕（只追加，失败不阻断——审计不得影响处置主流程）。"""
        try:
            from agent.core.quality.audit import AuditDimension, AuditRecord, QualityAuditStore

            QualityAuditStore(self.project_dir).append(
                AuditRecord(
                    at=time.time(),
                    overall_pass=None,
                    pass_scope="disposition_review",
                    dimensions=[
                        AuditDimension(
                            name="disposition_semantic_review",
                            value=0.0 if verdict.decision == "veto" else 1.0,
                            source=verdict.source,
                            unit="bool",
                            issues=[
                                f"action={getattr(plan, 'action', '?')} chapters={chapters}"
                                f" verdict={verdict.decision}：{verdict.reason[:200]}"
                            ],
                        )
                    ],
                )
            )
        except Exception as e:  # noqa: BLE001
            degrade("quality.disposition_review", "终审裁决审计快照落盘失败", e)

    def review(self, plan: Any, *, chapters: int) -> SemanticReviewVerdict:
        """对处置方案做一次语义终审。永不抛异常（异常=unavailable 降级）。"""
        if not self.enabled:
            v = SemanticReviewVerdict("disabled", "语义终审开关关闭，按四道守门现状语义", source="disabled")
            self._audit(v, plan, chapters)
            return v
        alerts: list[str] = []
        if self.supervisor_alerts_fn is not None:
            try:
                alerts = list(self.supervisor_alerts_fn() or [])
            except Exception as e:  # noqa: BLE001 - 证据供给失败不拦终审本身
                degrade("quality.disposition_review", "Supervisor 证据供给异常，终审按无告警进行", e)
                alerts = []
        try:
            from agent.client.gateway_adapter import chat_utility_structured

            verdict = chat_utility_structured(
                self.llm,
                messages=self._build_messages(plan, chapters, alerts),
                schema=DispositionReviewSchema,
                max_tokens=512,
                enable_thinking=False,
                name="disposition_review",
            )
            raw = str(verdict.verdict).strip().lower()
            if raw not in ("allow", "veto"):
                raise ValueError(f"终审输出非法 verdict={raw!r}")
            v = SemanticReviewVerdict(raw, str(verdict.reason or "").strip(), source="llm")
        except Exception as e:  # noqa: BLE001 - LLM 不可用 → 降级为现状行为（四道守门语义）
            degrade(
                "quality.disposition_review",
                "处置语义终审调用/解析失败，降级为现状行为（四道守门语义不变，不否决）",
                e,
            )
            v = SemanticReviewVerdict("unavailable", f"终审不可用：{e}", source="degraded")
        self._audit(v, plan, chapters)
        if v.decision == "veto" and self.console is not None:
            try:
                self.console.print(f"[red]✗ 处置方案被语义终审否决：{v.reason}[/red]")
            except Exception:  # noqa: SILENT_DEGRADE reason=logging-only - 打印失败不影响裁决
                pass
        return v

    def gate_fn(self) -> Callable[[Any, int], Any]:
        """产出守门器第五道的注入函数：``(plan, chapters) -> Authorization``。"""
        from agent.core.quality.disposition import Authorization

        def _review(plan: Any, chapters: int) -> Authorization:
            v = self.review(plan, chapters=chapters)
            return Authorization(v.ok, v.reason or v.decision)

        return _review


def collect_supervisor_alerts(project_dir: Any, current_chapter: int = 0, limit: int = 8) -> list[str]:
    """取 Supervisor advisory 告警作为终审证据（workflows 层注入用；零 LLM）。

    懒加载 ``core.supervisor``——本函数供 workflows/agents 层调用，
    ``core/quality`` 自身不新增对 supervisor 的子包级依赖。
    """
    try:
        from agent.core.supervisor.supervisor import create_default_engine

        report = create_default_engine(str(project_dir)).check_all(max(int(current_chapter), 0))
        lines = [
            f"[{i.severity}/{i.dimension}] {i.message}"
            for i in report.issues[:limit]
            if i.severity in ("warning", "critical")
        ]
        return lines
    except Exception as e:  # noqa: BLE001 - 监督证据失败不拦终审
        degrade("quality.disposition_review", "Supervisor 告警读取失败，终审按无告警进行", e)
        return []
