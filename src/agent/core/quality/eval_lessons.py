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

# 维度 → 正向修复指引（2026-09-10 回滚率削减·P0）。
# 只告诉 Writer"哪里错了"会催生保守灌水（五灵破 ch151-155 三连"教学→成功"
# 桥段实证），每条教训必须成对给出"正确的做法"。
_GUIDANCE: dict[str, str] = {
    "character_stability_high": (
        "重读角色档案 characters/*.md 的生死/性格/口癖真源，写前核对本章"
        "每个出场角色的当前状态；让每个角色的言行都能从档案推导"
    ),
    "setting_consistency_high": (
        "境界体系/金手指规则以 world.md 冻结版为准，写前逐条核对本章用到的"
        "每条规则与数值，禁止即兴发明未登记的能力或境界"
    ),
    "logic_holes": (
        "关键转折必须有动机铺垫、因果链完整；禁止靠巧合、碰巧救人、信息凭空"
        "出现推进剧情；反派行动要有合理目的与代价"
    ),
    "coherence": (
        "本章开头显式承接上一章结尾的时间/地点/悬念；场景与时间切换必须"
        "交代过渡，禁止跳跃式剪辑"
    ),
    "readability": (
        "每 800 字内设一个小钩子（危机/反转/新信息/情绪冲击）；章末留强悬念"
    ),
    "padding_repetition_abnormal": (
        "每个场景必须推进剧情或人物关系；静态描写与重复情绪独白压缩到 3 句"
        "以内；禁止连续章节复用同一种桥段模板"
    ),
    "foreshadow_recycle_rate": (
        "优先回收登记在案的旧伏笔再埋新伏笔；回收时要显式点名伏笔本体，"
        "让读者能对上号"
    ),
    "text_hygiene_blocking": (
        "落笔前自查四类硬伤：比喻必须本体喻体齐全（禁止『跟……似的』式残句）；"
        "成语不确定就查证替换；同一短语全章至多出现两次；感叹号每千字不超过 6 个，"
        "情绪用情节与细节传达而非标点"
    ),
    "debut_continuity": (
        "任何实体首次登场必须完整引入（这是谁、与主角什么关系）；"
        "『再次/依旧/仍旧』等口吻只允许用于已在前文登场过的实体，"
        "写前核对登场记录"
    ),
}

_GUIDANCE_DEFAULT = "对照该维度合格线逐条自查修正，修正手段必须落在具体情节上而非口号"


def guidance_for(name: str, label: str = "") -> str:
    """按维度名（回退中文名包含匹配）取正向修复指引。"""
    g = _GUIDANCE.get(str(name or ""))
    if not g:
        hay = f"{name or ''}{label or ''}"
        for key, val in _GUIDANCE.items():
            if key in hay or (label and key.rstrip("_high") in hay):
                g = val
                break
    return g or _GUIDANCE_DEFAULT


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
        path = _lessons_path(project_dir)
        overall_pass_flag = bool(getattr(report, "overall_pass", False))
        payload = {
            "at": time.time(),
            "overall_pass": overall_pass_flag,
            "score": round(float(getattr(report, "score", 0.0)), 2),
            "failures": failures,
            "suggestions": suggestions,
            "history": _merge_history(path, failures, overall_pass_flag),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        tmp.replace(path)
    except Exception:
        pass  # noqa: SILENT_DEGRADE


def _merge_history(
    path: Path, failures: list[dict[str, Any]], overall_pass: bool
) -> dict[str, int]:
    """跨轮累计每个维度的连续失败次数（顽固问题标记）。

    体检通过即清零（与"通过即写空 failures"同语义）；失败则对每个失败维度
    count+1。读不到旧文件/解析失败按空处理（静默降级，不阻断）。
    """
    prev: dict[str, int] = {}
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            raw = data.get("history") or {}
            if isinstance(raw, dict):
                prev = {str(k): int(v) for k, v in raw.items() if v}
    except Exception:  # noqa: BLE001
        prev = {}  # noqa: SILENT_DEGRADE - 旧文件损坏按空 history 处理
    if overall_pass:
        return {}
    merged = dict(prev)
    for f in failures:
        name = str(f.get("name", ""))
        if name:
            merged[name] = int(prev.get(name, 0)) + 1
    return merged


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
        history = data.get("history") or {}
        for f in failures:
            label = f.get("label", f.get("name", "?"))
            arrow = "≥" if f.get("direction") == ">=" else "≤"
            streak = 0
            try:
                streak = int(history.get(f.get("name"), 0) or 0)
            except Exception:  # noqa: BLE001
                streak = 0  # noqa: SILENT_DEGRADE
            tag = (
                f"（已连续 {streak} 轮不达标，属顽固问题，本轮必须彻底解决）"
                if streak >= 2
                else ""
            )
            issues = f.get("issues") or []
            if issues:
                lines.append(
                    f"【{label}】实测 {f.get('value')} {arrow} 合格线 "
                    f"{f.get('threshold')}{tag}，具体问题："
                )
                lines.extend(issues)
            else:
                lines.append(
                    f"【{label}】实测 {f.get('value')} {arrow} 合格线 "
                    f"{f.get('threshold')}{tag}（无逐条明细，请整体自查该维度）"
                )
            lines.append(f"- 正向做法：{guidance_for(f.get('name', ''), label)}")
        for s in (data.get("suggestions") or [])[:4]:
            lines.append(f"- 评审建议：{s}")
        text = "\n".join(lines)
        return text[:max_chars]
    except Exception:
        return ""  # noqa: SILENT_DEGRADE
