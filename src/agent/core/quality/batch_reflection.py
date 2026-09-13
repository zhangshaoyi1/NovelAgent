"""L2 批间反思（batch reflection，长线一致性设计稿第二期·归档三层反思）

设计稿 §9 第二层：本书的写作方法论反思。每批写完跑一次（固定 1 次 LLM 调用，
频控对齐 §9），把本批的生产侧证据——门禁告警留章、管理者审计发现、未销账
问题债务、上轮体检教训——提炼为结构化作战争笔记：

    现象（本批哪里不好/反复出现）→ 定位（根因在哪）→ 对策（下一批具体怎么做）
    → 验证（对策是否已被后续批次检验）

产出落 ``.state/batch_reflection.json``（最新 + 历史滚动 20 条），并经
``batch_replan.build_batch_summary`` 注入下一批复规划（规划者消费），经
``load_latest_reflection_text`` 供其他环节查询。

注意（§9 边界）：L2 是**本书自己的**作战笔记，不做跨书泛化——那归 L3
（未落地），泛化错了会污染所有后续书。失败显性降级（degrade），不阻断收尾。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field


class ReflectionError(Exception):
    """反思模块操作违规（文件损坏等）。"""


class ReflectionItem(BaseModel):
    """一条反思（现象 → 定位 → 对策 → 是否已验证）。"""

    phenomenon: str = Field(default="", description="现象：本批反复出现/显著的问题（引用具体章号或数据）")
    cause: str = Field(default="", description="定位：根因推断（要落到可操作的机制/上下文/规则上）")
    action: str = Field(default="", description="对策：下一批的具体动作（可执行、可检验）")
    verified: bool = Field(default=False, description="该对策是否已被后续批次验证过")


class ReflectionOutput(BaseModel):
    """反思结构化输出。"""

    items: list[ReflectionItem] = Field(default_factory=list, description="2-5 条，按重要性排序")
    summary: str = Field(default="", description="一句话总结本批质量态势")


REFLECTION_FILE = ".state/batch_reflection.json"
_HISTORY_CAP = 20


def _load_flags(project_dir: Path, limit: int = 8) -> list[dict[str, Any]]:
    p = project_dir / ".state" / "chapter_quality_flags.json"
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    flags = data.get("flags", []) if isinstance(data, dict) else []
    return flags[-limit:]


def _load_audit(project_dir: Path) -> dict[str, Any]:
    p = project_dir / ".state" / "plan_audit.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def build_reflection_input(project_dir: str | Path, since_ch: int = 0) -> str:
    """确定性装配反思输入（本批生产侧证据；单源失败降级为占位行）。

    ``since_ch``：只保留该章之后的证据（上次反思已覆盖的旧证据会误导 LLM
    把存量命中当成本批复发——2026-09-13 实弹实证：L1 硬拦截生效后第 15-19
    章零新增命中，但反思仍引用第 2/7/14 章旧命中误判"未落地"）。
    """
    from agent.core.infra.degrade import degrade

    project_dir = Path(project_dir)
    parts: list[str] = []

    try:
        flags = [
            f for f in _load_flags(project_dir)
            if not since_ch or int(f.get("chapter", 0) or 0) > since_ch
        ]
        if flags:
            lines = [f"- 第{f.get('chapter', '?')}章：{'；'.join(f.get('violations', [])[:3])}"
                     for f in flags[-5:]]
            parts.append("【门禁告警留章（重写后仍不达标）】\n" + "\n".join(lines))
        else:
            parts.append("门禁告警留章：近期无。")
    except Exception as e:  # noqa: BLE001
        degrade("batch_reflection.input.flags", "质量标记读取失败，反思输入缺该段", e)

    audit = _load_audit(project_dir)
    findings = audit.get("findings") or []
    if findings:
        lines = [f"- [{f.get('level')}/{f.get('manager')}] {f.get('message')}" for f in findings[:8]]
        parts.append("【管理者计划审计发现】\n" + "\n".join(lines))

    try:
        from agent.core.story.issue_debt import IssueDebtStore

        debts = IssueDebtStore(project_dir).load().open_items()
        if debts:
            parts.append("【未销账问题债务（前 5）】\n" + "\n".join(
                f"- [{d.kind}] {d.constraint}" for d in debts[:5]
            ))
    except Exception as e:  # noqa: BLE001
        degrade("batch_reflection.input.debts", "问题债务读取失败，反思输入缺该段", e)

    try:
        from agent.core.quality.eval_lessons import load_eval_lessons_text

        lessons = load_eval_lessons_text(project_dir)
        if lessons:
            parts.append(lessons)
    except Exception as e:  # noqa: BLE001
        degrade("batch_reflection.input.lessons", "体检教训读取失败，反思输入缺该段", e)


    # ---- 对策执行记录 → 门禁回归证据（对策→执行记录→门禁回归→销账 闭环）----
    l1_lines: list[str] = []
    new_hits = 0
    try:
        trace_p = project_dir / ".state" / "l1_trace.jsonl"
        if trace_p.exists():
            for line in trace_p.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                ch = int(rec.get("ch", 0) or 0)
                if since_ch and ch <= since_ch:
                    continue
                l1_lines.append(f"- 第{ch}章替换：{'；'.join(rec.get('replaced', []))}")
    except Exception as e:  # noqa: BLE001
        degrade("batch_reflection.input.l1", "L1 执行轨迹读取失败，缺该段", e)
    try:
        new_hits = len([
            f for f in _load_flags(project_dir)
            if not since_ch or int(f.get("chapter", 0) or 0) > since_ch
        ])
    except Exception as e:  # noqa: BLE001
        degrade("batch_reflection.input.regress", "门禁回归统计失败", e)
    if l1_lines or since_ch:
        verdict = "（对策已兑现）" if (new_hits == 0 and l1_lines) else ("（仍有命中，对策未完全兑现）" if new_hits else "")
        parts.append(
            "【L1 禁词对策：执行记录 → 门禁回归（硬证据）】\n"
            + ("\n".join(l1_lines) if l1_lines else "- 本批无替换执行（生成侧零命中）")
            + f"\n- 门禁回归：自第{since_ch or 0}章后新增命中 {new_hits} 处" + verdict
        )
    return "\n\n".join(parts) if parts else "（本批无生产侧异常证据——质量态势平稳）"


def record_batch_reflection(
    project_dir: str | Path,
    batch_end_ch: int = 0,
    *,
    chat_fn: Callable[[list[dict[str, str]]], dict[str, Any]] | None = None,
) -> bool:
    """批末反思：装配输入 → LLM 提炼 → 落盘（最新 + 历史）。失败 degrade 返回 False。

    chat_fn 可注入（离线测试）；缺省惰性创建 Gateway，chat_structured 强制
    ReflectionOutput 结构。
    """
    from agent.core.infra.degrade import degrade

    project_dir = Path(project_dir)
    try:
        if chat_fn is None:
            from agent.client.gateway_adapter import chat_structured, create_gateway
            from agent.core.base.structured_output import StructuredOutputError

            llm = create_gateway()

            def chat_fn(messages):  # noqa: F811
                # 2026-09-13 实弹：长现象文本在 2048 处截断致 JSON 解析失败——
                # 放宽到 3072，仍失败则附错误重试一次（对齐质检门禁重试约定）
                try:
                    return chat_structured(llm, messages, ReflectionOutput,
                                           use="utility", temperature=0.4,
                                           max_tokens=3072, enable_thinking=False)
                except StructuredOutputError as first_e:
                    retry_messages = messages[:-1] + [{
                        "role": "user",
                        "content": messages[-1]["content"]
                        + "\n\n【上次输出解析失败原因，务必修正】请只输出一个合法 JSON 对象，"
                          "现象/定位/对策各字段控制在 80 字以内，不要包含 ```json 标记：\n"
                        + str(first_e),
                    }]
                    return chat_structured(llm, retry_messages, ReflectionOutput,
                                           use="utility", temperature=0.4,
                                           max_tokens=3072, enable_thinking=False)

        # 已有上批反思 → 先取其末章（证据时效过滤 + 验证闭环）
        prior = load_latest(project_dir)
        evidence = build_reflection_input(
            project_dir, since_ch=int((prior or {}).get("batch_end_ch", 0) or 0)
        )
        prior_text = ""
        if prior:
            prior_text = "【上一批反思的对策（先检验是否兑现，再提新对策）】\n" + "\n".join(
                f"- {it.get('action', '')}" for it in prior.get("items", []) if it.get("action")
            )
        messages = [
            {"role": "system", "content": (
                "你是本书的写作方法教练，做批间反思。只依据给定证据提炼 2-5 条反思，"
                "每条=现象（引用具体章号/数据）→定位（根因落到机制/上下文/规则）→"
                "对策（下一批可执行、可检验的具体动作）。这是本书自己的作战笔记，"
                "不做跨书泛化。输出 JSON：items（phenomenon/cause/action/verified）+ summary。"
            )},
            {"role": "user", "content": (
                f"【本批截至章】第 {batch_end_ch or '?'} 章\n\n"
                f"{prior_text + chr(10) + chr(10) if prior_text else ''}"
                f"【本批生产侧证据】\n{evidence}"
            )},
        ]
        out = chat_fn(messages)
        data = out if isinstance(out, dict) else out.model_dump()

        store = _load_store(project_dir)
        entry = {
            "batch_end_ch": int(batch_end_ch or 0),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "summary": str(data.get("summary") or ""),
            "items": [
                {"phenomenon": i.get("phenomenon", ""), "cause": i.get("cause", ""),
                 "action": i.get("action", ""), "verified": bool(i.get("verified", False))}
                for i in (data.get("items") or [])
            ],
        }
        store["latest"] = entry
        store.setdefault("history", []).append(entry)
        store["history"] = store["history"][-_HISTORY_CAP:]
        _save_store(project_dir, store)
        return True
    except Exception as e:  # noqa: BLE001 - 显性降级：反思失败不阻断收尾，但必须留痕
        degrade("batch_reflection.record", "批间反思失败，本批不产生作战笔记", e)
        return False


def _load_store(project_dir: Path) -> dict[str, Any]:
    p = project_dir / REFLECTION_FILE
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError) as e:
        raise ReflectionError(f"反思文件损坏（{p}）：{e}") from e


def _save_store(project_dir: Path, store: dict[str, Any]) -> None:
    p = project_dir / REFLECTION_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def load_latest(project_dir: str | Path) -> dict[str, Any] | None:
    """最近一次反思条目（无/损坏 → None，调用方 degrade）。"""
    store = _load_store(Path(project_dir))
    return store.get("latest")


def load_latest_reflection_text(project_dir: str | Path, limit: int = 5) -> str:
    """最近反思的人话文本（供复规划摘要注入；空 → ""）。"""
    entry = load_latest(project_dir)
    if not entry or not entry.get("items"):
        return ""
    lines = [f"上批反思总结：{entry['summary']}"] if entry.get("summary") else []
    for it in entry["items"][:limit]:
        tag = "（已验证）" if it.get("verified") else ""
        lines.append(f"- 现象：{it.get('phenomenon', '')}｜对策：{it.get('action', '')}{tag}")
    if not lines:
        return ""
    return "\n【上批作战笔记（本批须兑现对策，验收看下一批反思）】\n" + "\n".join(lines)


__all__ = [
    "ReflectionError",
    "ReflectionItem",
    "ReflectionOutput",
    "REFLECTION_FILE",
    "build_reflection_input",
    "record_batch_reflection",
    "load_latest",
    "load_latest_reflection_text",
]
