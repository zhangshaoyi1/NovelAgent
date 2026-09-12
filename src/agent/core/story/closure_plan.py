"""完本收束计划（closure plan，长线一致性设计稿第一期·E，2026-09-12）

背景：读者等了几百万字的东西没交代，是最伤读者的完本失败模式。本模块在
完本前把「所有未了事项」逐条收集成收束计划：未回收伏笔（foreshadows.md）、
进行中叙事线、实体未了义务、未销账问题债务。计划落盘
``.state/closure_plan.json`` 并可渲染为写时注入文本（结局模式章自动携带），
处置方案（回收/有意留白且显性登记）由规划者/管理者团队裁决——骨架期先做
确定性收集与注入，LLM 处置裁决见设计稿第二期。

纯文件读写，无 LLM；失败显性（degrade / 返回空并留痕），绝不静默。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CLOSURE_FILE = ".state/closure_plan.json"


def build_closure_plan(
    project_dir: str | Path, foreshadows: list[dict[str, str]] | None = None
) -> dict[str, Any]:
    """确定性收集全部未了事项（伏笔/叙事线/实体义务/问题债务）。

    伏笔段由调用方注入（R6 分层：core 不 import workflows）——CLI 层用
    ``M13ForeshadowWorkflow._parse_table`` 解析后传入；core 内不自行解析。
    """
    project_dir = Path(project_dir)
    if foreshadows is None:
        # 调用方未注入伏笔段：显性降级为空段（不由 core 越层解析）
        from agent.core.infra.degrade import degrade

        degrade(
            "closure_plan.foreshadows.skip",
            "调用方未注入伏笔解析结果，收束计划缺伏笔段（请从 CLI 层传入）",
        )
        foreshadows = []

    threads: list[dict[str, Any]] = []
    obligations: list[dict[str, str]] = []
    try:
        from agent.core.story.entity_ledger import EntityLedgerStore

        st = EntityLedgerStore(project_dir).load()
        threads = [
            {"id": t.id, "name": t.name, "bound_entity": t.bound_entity,
             "urgency": t.urgency, "milestones": len(t.milestones)}
            for t in st.open_threads()
        ]
        for e in st.entities:
            for ob in e.open_obligations():
                obligations.append({"entity": e.name, "text": ob.text})
    except Exception as e:  # noqa: BLE001
        from agent.core.infra.degrade import degrade

        degrade("closure_plan.roster", "实体名册读取失败，收束计划缺名册段", e)

    issue_debts: list[dict[str, str]] = []
    try:
        from agent.core.story.issue_debt import IssueDebtStore

        issue_debts = [
            {"id": d.id, "kind": d.kind, "constraint": d.constraint}
            for d in IssueDebtStore(project_dir).load().open_items()
        ]
    except Exception as e:  # noqa: BLE001
        from agent.core.infra.degrade import degrade

        degrade("closure_plan.debts", "问题债务读取失败，收束计划缺债务段", e)

    plan = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "foreshadows": foreshadows,
        "threads": threads,
        "obligations": obligations,
        "issue_debts": issue_debts,
    }
    plan["total_open"] = (
        len(foreshadows) + len(threads) + len(obligations) + len(issue_debts)
    )
    return plan


def save_closure_plan(project_dir: str | Path, plan: dict[str, Any]) -> Path:
    path = Path(project_dir) / CLOSURE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def load_closure_plan(project_dir: str | Path) -> dict[str, Any]:
    path = Path(project_dir) / CLOSURE_FILE
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError) as e:
        from agent.core.infra.degrade import degrade

        degrade("closure_plan.load", "收束计划文件损坏，按空处理", e)
        return {}


def render_closure_text(plan: dict[str, Any], limit: int = 12) -> str:
    """渲染为结局章写时注入文本（逐条处置要求）；空计划返回空串。"""
    if not plan or not plan.get("total_open"):
        return ""
    lines: list[str] = []
    for f in (plan.get("foreshadows") or [])[:limit]:
        lines.append(f"- 伏笔 {f['fid']}（{f['state']}）：{f['content']}（预期回收点：{f['expected_resolve']}）")
    for t in (plan.get("threads") or [])[:limit]:
        lines.append(f"- 叙事线 {t['name']}（绑定{t['bound_entity'] or '无'}，已推进{t['milestones']}节）：须在本卷内推进或收束")
    for ob in (plan.get("obligations") or [])[:limit]:
        lines.append(f"- 实体义务 [{ob['entity']}]：{ob['text']}")
    for d in (plan.get("issue_debts") or [])[:limit]:
        lines.append(f"- 问题债务 [{d['id']}]：{d['constraint']}")
    if not lines:
        return ""
    return (
        f"\n【完本收束清单（共 {plan['total_open']} 项未了事项，结局段必须逐条处置："
        "要么正面回收，要么有意留白且给读者明确的余韵指向；禁止装作不存在）】\n"
        + "\n".join(lines)
    )


__all__ = [
    "build_closure_plan",
    "save_closure_plan",
    "load_closure_plan",
    "render_closure_text",
    "CLOSURE_FILE",
]
