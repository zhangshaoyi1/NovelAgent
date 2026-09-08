"""体检教训持久化（2026-09-08 反馈闭环修复·缺口 2）

背景
----
``build_rewrite_hint`` 只在「回滚→重写」循环内生效：体检通过后写下一轮
**新章**时，Writer 的任务里没有任何上一轮失败信息，同类问题（AI 腔、
人设漂移、设定矛盾）可能在新章重犯。

设计
----
- 体检结束后由 Pipeline 调 :func:`save_eval_lessons`，把失败维度 +
  评审逐条问题明细写入 ``.state/memory/eval_lessons.json``；
- 体检**通过**时同样落盘但 ``failures`` 为空 —— 天然完成"教训清零"，
  无需额外的失效逻辑；
- Writer（agentic_write）在**非重写**路径下调 :func:`load_eval_lessons_text`
  取人话教训段注入任务（重写路径已有 build_rewrite_hint，不重复注入）。

依赖方向：本模块仅依赖标准库，不 import agents/workflows（core 层红线）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

_LESSONS_REL = Path(".state") / "memory" / "eval_lessons.json"

# 注入 prompt 的教训段上限（字符），防止失败过多时挤占正文预算
_MAX_LESSON_CHARS = 1600
_MAX_ISSUES_PER_DIM = 5
_MAX_DESC_CHARS = 160


def _lessons_path(project_dir: str | Path) -> Path:
    return Path(project_dir) / _LESSONS_REL


def _issue_lines(ev: Any, dim_label: str) -> list[str]:
    """从失败维度的证据提取逐条问题（人话格式）。"""
    lines: list[str] = []
    if ev is None or float(getattr(ev, "confidence", 1.0)) <= 0.0:
        return lines  # 证据不可信（如缓存串值）不输出，避免误导
    for it in (getattr(ev, "issues", None) or [])[:_MAX_ISSUES_PER_DIM]:
        if not isinstance(it, dict):
            continue
        desc = str(it.get("desc", "") or "").strip()
        if not desc:
            continue
        typ = str(it.get("type", "") or "").strip()
        sev = str(it.get("severity", "") or "").strip()
        tag = f"（{typ}·{sev}）" if typ else ""
        lines.append(f"- {dim_label}{tag}：{desc[:_MAX_DESC_CHARS]}")
    return lines


def save_eval_lessons(project_dir: str | Path, report: Any) -> None:
    """体检结束后落盘教训（失败明细）。体检通过则写空 failures 清零。

    任何异常静默降级（教训是增强信息，不得阻断写作流水线）。
    """
    try:
        failed = [
            d for d in (getattr(report, "dimensions", None) or []) if not d.passed
        ]
        failures: list[dict[str, Any]] = []
        for d in failed:
            failures.append({
                "name": d.name,
                "label": d.label,
                "value": d.value,
                "threshold": d.threshold,
                "direction": d.direction,
                "issues": _issue_lines(getattr(d, "evidence", None), d.label),
            })
        # 评审建议（迷爱看/黄金三章子块的 suggestions）一并落盘——
        # 这两类维度失败时 failures 里只有数字行，建议才是可操作内容。
        suggestions: list[str] = []
        for sub in (getattr(report, "appeal", None), getattr(report, "golden_three", None)):
            if isinstance(sub, dict):
                for s in (sub.get("suggestions") or [])[:3]:
                    if isinstance(s, str) and s.strip():
                        suggestions.append(s.strip()[:_MAX_DESC_CHARS])
        payload = {
            "at": time.time(),
            "overall_pass": bool(getattr(report, "overall_pass", False)),
            "score": round(float(getattr(report, "score", 0.0)), 2),
            "failures": failures,
            "suggestions": suggestions,
        }
        path = _lessons_path(project_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        tmp.replace(path)
    except Exception:
        pass  # noqa: SILENT_DEGRADE


def load_eval_lessons_text(
    project_dir: str | Path, max_chars: int = _MAX_LESSON_CHARS
) -> str:
    """读取最近一次体检教训的人话文本段。

    无失败（通过/未评过/读失败）返回空串 —— 调用方据此跳过注入。
    """
    try:
        path = _lessons_path(project_dir)
        if not path.exists():
            return ""
        data = json.loads(path.read_text(encoding="utf-8"))
        failures = data.get("failures") or []
        if not failures or data.get("overall_pass", True):
            return ""
        lines = [
            f"上一轮全书体检未通过（综合分 {data.get('score', '?')}/100），"
            "以下问题已在新章节中出现过，请务必规避同类错误："
        ]
        for f in failures:
            label = f.get("label", f.get("name", "?"))
            arrow = "≥" if f.get("direction") == ">=" else "≤"
            issues = f.get("issues") or []
            if issues:
                lines.append(f"【{label}】实测 {f.get('value')} {arrow} 合格线 {f.get('threshold')}，具体问题：")
                lines.extend(issues)
            else:
                lines.append(
                    f"【{label}】实测 {f.get('value')} {arrow} 合格线 {f.get('threshold')}（无逐条明细，请整体自查该维度）"
                )
        for s in (data.get("suggestions") or [])[:4]:
            lines.append(f"- 评审建议：{s}")
        text = "\n".join(lines)
        return text[:max_chars]
    except Exception:
        return ""  # noqa: SILENT_DEGRADE
