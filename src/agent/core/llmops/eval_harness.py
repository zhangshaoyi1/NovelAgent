"""LLMOps · 评测回归（Phase 3）

把 Evaluator 的「不崩」体检报告按次记录，提供**回归检测**：
- 综合分较上次显著下降（默认跌 > 10 分）；
- 上次通过的维度本次未通过（新退化）；
- 触发过自动回溯 / 人工上报。

配合 PromptRegistry，可在改提示/模型后跑回归，确认"不崩"未被破坏。
持久化：``<project>/.state/llmops/eval_runs.jsonl``。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class EvalRun:
    overall_pass: bool
    score: float
    dimensions: dict[str, bool] = field(default_factory=dict)  # name -> passed
    rolled_back: bool = False
    escalated: bool = False
    tags: dict[str, Any] = field(default_factory=dict)
    at: float = field(default_factory=lambda: time.time())

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_pass": self.overall_pass,
            "score": self.score,
            "dimensions": self.dimensions,
            "rolled_back": self.rolled_back,
            "escalated": self.escalated,
            "tags": self.tags,
            "at": self.at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EvalRun":
        return cls(
            overall_pass=bool(d.get("overall_pass", False)),
            score=float(d.get("score", 0.0)),
            dimensions=dict(d.get("dimensions", {}) or {}),
            rolled_back=bool(d.get("rolled_back", False)),
            escalated=bool(d.get("escalated", False)),
            tags=dict(d.get("tags", {}) or {}),
            at=float(d.get("at", 0.0)),
        )


@dataclass
class RegressionIssue:
    kind: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "message": self.message}


class EvalHarness:
    """评测回归记录器。"""

    def __init__(self, project_dir: str | Path | None = None) -> None:
        self.project_dir = Path(project_dir) if project_dir else None
        self._runs: list[EvalRun] = []
        if self.project_dir is not None:
            self._file = self.project_dir / ".state" / "llmops" / "eval_runs.jsonl"
            self._load()
        else:
            self._file = None

    def _load(self) -> None:
        if self._file is None or not self._file.exists():
            return
        try:
            for line in self._file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    self._runs.append(EvalRun.from_dict(json.loads(line)))
        except (json.JSONDecodeError, OSError):
            self._runs = []

    def _persist(self) -> None:
        if self._file is None:
            return
        self._file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._file.with_suffix(".tmp")
        lines = [json.dumps(r.to_dict(), ensure_ascii=False) for r in self._runs]
        tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        tmp.replace(self._file)

    def record(self, report: dict[str, Any], tags: dict[str, Any] | None = None) -> EvalRun:
        """记录一次体检（传入 NovelHealthReport.to_dict()）。"""
        dims: dict[str, bool] = {}
        for d in report.get("dimensions", []) or []:
            dims[d.get("name", "")] = bool(d.get("passed", False))
        run = EvalRun(
            overall_pass=bool(report.get("overall_pass", False)),
            score=float(report.get("score", 0.0)),
            dimensions=dims,
            rolled_back=bool(report.get("rolled_back", False)),
            escalated=bool(report.get("escalated", False)),
            tags=tags or {},
        )
        self._runs.append(run)
        self._persist()
        return run

    def history(self) -> list[EvalRun]:
        return list(self._runs)

    def detect_regression(self, score_drop: float = 10.0) -> list[RegressionIssue]:
        """检测最近一次相对上一次的回归。"""
        issues: list[RegressionIssue] = []
        if len(self._runs) < 2:
            return issues
        prev, cur = self._runs[-2], self._runs[-1]
        if prev.score - cur.score >= score_drop:
            issues.append(
                RegressionIssue(
                    "score_drop",
                    f"综合分下降 {prev.score - cur.score:.1f}（{prev.score:.1f}→{cur.score:.1f}）",
                )
            )
        for name, passed in cur.dimensions.items():
            if not passed and prev.dimensions.get(name, False):
                issues.append(
                    RegressionIssue("dim_regress", f"维度「{name}」由通过退化为未通过")
                )
        return issues

    def latest(self) -> EvalRun | None:
        return self._runs[-1] if self._runs else None
