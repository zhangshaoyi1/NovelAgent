"""规划闸门（登记单 20261004_规划质量分层守卫·件 2）。

背景
----
规划是"越晚发现越贵"的上游：规划出错 → 下游判据派生自规划（自我印证）
自动放行 → 精确执行 → 全书级返工。本模块给 ``PlanStore.mutate()`` 的落盘
动作加**分层守卫**：

- **L1 确定性硬冲突（零 LLM，恒拦）**：新规划与事实卡死亡名单 / 真相断言
  （death）的"复活类"冲突——机器可判的冲突不花 LLM、不给评审官越权放行的机会；
- **L2 独立 LLM 评审（带牙）**：参照系 = 前序事实（事实卡 + 真相账本），
  **严禁注入 design_brief 渲染产物**（打破自我印证）；pass 放行 / revise 打回
  （上限 2 次，第 3 次 → 升级检查点挂起交人工）/ unavailable → 挂起（不放行）。

失败语义按"错误后果单价"选（登记单 §九沉淀纪律）：写盘闸门的 fail-open
代价是全书返工，故 revise/unavailable 均不放行；唯一例外是 ``review_llm=None``
（**有意设计**）：规划 agent 链路必须注入 llm 得到完整闸门；无注入 =
人工编辑路径（人在场拍板），放行 + degrade 留痕。

分层（R6）：本模块在 core，可 import base/client/其他 core（延迟导入），
**不得** import agents/workflows。agents/workflows import 本模块合法。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from agent.core.infra.degrade import degrade

#: 评审留痕（JSONL 只追加；eval 式回放用）
PLAN_GATE_LOG = ".state/plan_gate.jsonl"
#: 人工豁免台账（override=True 时追加；谁拍板谁留痕）
PLAN_GATE_WAIVERS = ".state/plan_gate_waivers.jsonl"
#: revise 打回计数（{signature: count}，signature=changed keys 排序 join）
PLAN_GATE_REFUSALS = ".state/plan_gate_refusals.json"
#: revise 打回上限（第 3 次 revise → 升级检查点挂起，不形成打回死循环）
REFUSAL_LIMIT = 2

#: 复活类词（低误报优先：只做"死亡角色 + 复活词同现"这一条确定性规则）
_REVIVE_WORDS = ("复活", "重生", "还魂", "起死回生", "苏醒")

VALID_VERDICTS = ("pass", "revise")


class PlanGateRejected(RuntimeError):
    """规划闸门拒写（``PlanStore.mutate`` 不落盘、不留史，异常向上抛）。"""

    def __init__(self, feedback: str) -> None:
        super().__init__(feedback)
        self.feedback = feedback


class PlanGateOutput(BaseModel):
    """规划闸门评审官结构化输出契约。"""

    verdict: str  # pass / revise（合法性在 review_plan_diff 内校验）
    feedback: str = ""


class PlanGateResult:
    """一次规划评审的结论（三态：pass / revise / unavailable）。"""

    def __init__(self, verdict: str, feedback: str = "", source: str = "llm") -> None:
        self.verdict = verdict
        self.feedback = feedback
        self.source = source

    @property
    def ok(self) -> bool:
        return self.verdict == "pass"


def plan_gate_enabled() -> bool:
    """总闸：env ``NOVEL_PLAN_GATE_REVIEW != "0"``（默认开，用户 2026-10-04 拍板）。"""
    return os.getenv("NOVEL_PLAN_GATE_REVIEW", "1") != "0"


# ---------------------------------------------------------------- 确定性硬冲突
def _flatten_strings(obj: Any, path: str = "") -> list[tuple[str, str]]:
    """把 plan dict 递归展平成 (字段路径, 字符串值) 列表（只收集 str 值）。"""
    out: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.extend(_flatten_strings(v, f"{path}.{k}" if path else str(k)))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            out.extend(_flatten_strings(v, f"{path}[{i}]"))
    elif isinstance(obj, str):
        out.append((path or "<root>", obj))
    return out


def _dead_names(project_dir: str | Path) -> tuple[dict[str, int], list[str]]:
    """收集死亡名单：(名字 → 死亡章)，及含对应死者名的 active 死亡断言 id 列表。

    来源（均只读）：
    - 事实卡状态 ``FactCardStateStore.load().dead``（``dead: dict[name, 死亡章]``）；
    - 真相账本 ``TruthLedgerStore`` 中 kind=="death" 且 active、claim 含该名字的断言
      （作为补充证据；名字主源仍是事实卡——claim 文本反解人名不可靠，不猜）。
    """
    dead: dict[str, int] = {}
    try:
        from agent.core.continuity.fact_card import FactCardStateStore

        dead = dict(FactCardStateStore(project_dir).load().dead or {})
    except Exception as e:  # noqa: BLE001 - 事实卡缺席/损坏 → 无死亡名单，宁漏勿滥
        degrade("plan_gate.fact_state", "事实卡状态读取失败，硬冲突检查跳过死亡名单", e)
    claims: list[str] = []
    try:
        from agent.core.story.truth_ledger import TruthLedgerStore

        store = TruthLedgerStore(project_dir)
        store.load()
        claims = [
            f"{a.assertion_id}（第{a.chapter_established}章确立）：{a.claim}"
            for a in store.active()
            if a.kind == "death"
        ]
    except Exception as e:  # noqa: BLE001 - 账本缺席不阻断检查
        degrade("plan_gate.truth_ledger", "真相账本读取失败，硬冲突检查跳过死亡断言证据", e)
    return dead, claims


def hard_conflict_check(project_dir: str | Path, new_plan: dict) -> list[str]:
    """确定性硬冲突检查（零 LLM）：死亡角色在规划字段值中与复活类词同现。

    规则（低误报优先，只此一条）：对死亡名单每个名字 d——若新 plan 某个
    **同一字符串字段值内**同时出现 d 与复活类词（复活/重生/还魂/起死回生/苏醒），
    记一条硬冲突（含名字、字段路径、证据片段、死亡章/断言证据）。
    返回冲突消息列表（空 = 无硬冲突）。
    """
    dead, claims = _dead_names(project_dir)
    if not dead:
        return []
    conflicts: list[str] = []
    for path, value in _flatten_strings(new_plan):
        for name, chapter in dead.items():
            if name not in value:
                continue
            for w in _REVIVE_WORDS:
                if w in value:
                    evidence = next(
                        (c for c in claims if name in c),
                        f"事实卡死亡名单：{name}（第{chapter}章死亡）",
                    )
                    conflicts.append(
                        f"硬冲突：已死亡角色「{name}」（第{chapter}章死亡）出现在规划字段"
                        f" `{path}` 且与复活类词「{w}」同值出现。证据：{evidence[:200]}。"
                        "若确需复活/归来桥段，必须走真相账本裁决通道（ChangeGate/arbiter）"
                        "先推翻死亡断言，或 override=True 人工拍板并说明理由。"
                    )
                    break  # 同一名字同一字段值只记一条
    return conflicts


def check_outline_text(project_dir: str | Path, text: str) -> list[str]:
    """outline.md / subline.md 直写前的确定性检查（薄封装，m3_outline 用）。

    同一死亡+复活词规则，输入是整篇 markdown 文本（按命中点前后 20 字窗口匹配）。
    只留痕告警**不阻断**（outline 是 plan.json 的渲染下游，plan 闸门已拦主要内容；
    结果追加进 ``.state/plan_gate.jsonl``（source=outline_check）供观测）。
    """
    dead, claims = _dead_names(project_dir)
    hits: list[str] = []
    if dead and text:
        for name, chapter in dead.items():
            start = 0
            while True:
                idx = text.find(name, start)
                if idx < 0:
                    break
                window = text[max(0, idx - 20): idx + len(name) + 20]
                if any(w in window for w in _REVIVE_WORDS):
                    evidence = next(
                        (c for c in claims if name in c),
                        f"事实卡死亡名单：{name}（第{chapter}章死亡）",
                    )
                    hits.append(
                        f"outline 疑似复活已死亡角色「{name}」（第{chapter}章死亡）："
                        f"…{window[:60]}…。证据：{evidence[:120]}"
                    )
                    break
                start = idx + len(name)
    _log(
        Path(project_dir),
        verdict="hit" if hits else "pass",
        source="outline_check",
        feedback="；".join(hits)[:800],
        reason="m3_outline 直写检查",
        keys_changed=[],
    )
    return hits


# ---------------------------------------------------------------- LLM 评审
def _log(
    project_dir: Path,
    *,
    verdict: str,
    source: str,
    feedback: str,
    reason: str,
    keys_changed: list[str],
) -> None:
    """闸门留痕（JSONL 只追加；失败不阻断——留痕不得影响裁决链）。"""
    try:
        p = project_dir / PLAN_GATE_LOG
        p.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "at": time.time(),
            "reason": reason[:200],
            "keys_changed": keys_changed,
            "verdict": verdict,
            "source": source,
            "feedback": feedback[:400],
        }
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001 - 留痕失败不影响裁决
        degrade("plan_gate.log", "规划闸门留痕落盘失败", e)


def _changed(old: dict, new: dict) -> list[str]:
    return sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))


def _snippet(v: Any, limit: int = 400) -> str:
    if isinstance(v, str):
        s = v
    else:
        try:
            s = json.dumps(v, ensure_ascii=False)
        except Exception:  # noqa: BLE001 - 不可序列化字段降级 str()
            s = str(v)  # noqa: SILENT_DEGRADE reason=best-effort ref=20261004_规划质量分层守卫_收口评审与挂起语义.md
    return s[:limit] + ("…（已截断）" if len(s) > limit else "")


def _fact_summary(project_dir: Path) -> str:
    """前序事实摘要：死亡名单（含章号）+ 脱手物品 + 真相 active 断言。

    ★ 参照系独立性红线（登记单验收 5）：这里**严禁**注入 design_brief 的任何
    渲染产物——评审官只能拿"已发生的事实"对照规划，不能拿"规划自己的判据"
    审规划（自我印证）。
    """
    lines: list[str] = []
    try:
        from agent.core.continuity.fact_card import FactCardStateStore

        state = FactCardStateStore(project_dir).load()
        if state.dead:
            lines.append(
                "死亡名单："
                + "；".join(f"{n}（第{c}章死亡）" for n, c in sorted(state.dead.items()))
            )
        if state.items_gone:
            lines.append(
                "已脱手物品："
                + "；".join(f"{n}（第{c}章脱手）" for n, c in sorted(state.items_gone.items()))
            )
    except Exception as e:  # noqa: BLE001 - 事实卡缺席 → 摘要缺失，评审照常
        degrade("plan_gate.fact_state", "评审事实摘要读取失败（死亡/物品段缺失）", e)
    try:
        from agent.core.story.truth_ledger import TruthLedgerStore

        _tstore = TruthLedgerStore(project_dir)
        _tstore.load()
        rendered = _tstore.render_for_brief()
        if rendered:
            lines.append(rendered)
    except Exception as e:  # noqa: BLE001 - 账本缺席不阻断评审
        degrade("plan_gate.truth_ledger", "评审真相摘要渲染失败（真相段缺失）", e)
    return "\n".join(lines) or "（无前序事实记录）"


def review_plan_diff(
    project_dir: str | Path,
    old: dict,
    new: dict,
    *,
    llm: Any,
    reason: str = "",
) -> PlanGateResult:
    """独立规划评审官裁决一次 old→new 规划变更。**永不抛异常**。

    异常 → ``PlanGateResult("unavailable", ..., source="degraded")``（degrade 留痕）。
    内部做 2 次尝试（首次失败重试一次），均失败才 unavailable。
    参照系 = 前序事实摘要（事实卡 + 真相账本），**不含 design_brief**。
    """
    project_path = Path(project_dir)
    keys = _changed(old, new)
    diff_rows = [
        f"- `{k}`\n  旧: {_snippet(old.get(k))}\n  新: {_snippet(new.get(k))}"
        for k in keys
    ]
    verdict_obj: Any = None
    try:
        from agent.client.gateway_adapter import chat_utility_structured
        from agent.core.infra.prompt_manager import pm

        user = pm.get("agents.plan_gate").render_user(
            reason=str(reason or "（未提供变更原因）")[:300],
            keys_changed="、".join(keys) or "（无字段变更）",
            diff_summary="\n".join(diff_rows) or "（无字段变更）",
            fact_summary=_fact_summary(project_path),
        )
        last_err: Exception | None = None
        for _attempt in range(2):  # 2 次尝试：单次 LLM 抖动不应直接 unavailable
            try:
                verdict_obj = chat_utility_structured(
                    llm,
                    messages=[
                        {"role": "system", "content": pm.get("agents.plan_gate").system},
                        {"role": "user", "content": user},
                    ],
                    schema=PlanGateOutput,
                    max_tokens=1024,
                    enable_thinking=False,
                    name="plan_gate",
                )
                break
            except Exception as e:  # noqa: BLE001, SILENT_DEGRADE reason=retry-loop - 异常被捕获后统一重抛（非静默）
                last_err = e
        if verdict_obj is None:
            raise last_err  # type: ignore[misc]
        raw = str(verdict_obj.verdict).strip().lower()
        if raw not in VALID_VERDICTS:
            raise ValueError(f"评审输出非法 verdict={raw!r}")
        result = PlanGateResult(raw, str(verdict_obj.feedback or "").strip(), source="llm")
    except Exception as e:  # noqa: BLE001 - 评审不可用按挂起语义处理（调用方 enforce_plan_gate）
        degrade(
            "plan_gate.review",
            "规划闸门评审两次尝试均失败（unavailable；写盘闸门将挂起不放行）",
            e,
        )
        result = PlanGateResult("unavailable", f"评审不可用：{e}", source="degraded")
    _log(project_path, verdict=result.verdict, source=result.source,
         feedback=result.feedback, reason=reason, keys_changed=keys)
    return result


def _refusal_count_path(project_dir: Path) -> Path:
    return project_dir / ".state" / PLAN_GATE_REFUSALS


def _load_refusals(project_dir: Path) -> dict[str, int]:
    try:
        data = json.loads(_refusal_count_path(project_dir).read_text(encoding="utf-8"))
        return {k: int(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 - 计数文件缺失/损坏视为空（重新计数）
        return {}  # noqa: SILENT_DEGRADE reason=best-effort ref=20261004_规划质量分层守卫_收口评审与挂起语义.md


def _bump_refusal(project_dir: Path, signature: str) -> int:
    counts = _load_refusals(project_dir)
    counts[signature] = counts.get(signature, 0) + 1
    try:
        p = _refusal_count_path(project_dir)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(counts, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:  # noqa: BLE001 - 计数落盘失败按本次计数 1 处理（不阻断拒写）
        degrade("plan_gate.refusals", "打回计数落盘失败（本次拒写不受影响）", e)
    return counts[signature]


def _clear_refusal(project_dir: Path, signature: str) -> None:
    counts = _load_refusals(project_dir)
    if signature in counts:
        counts.pop(signature, None)
        try:
            _refusal_count_path(project_dir).write_text(
                json.dumps(counts, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        except Exception as e:  # noqa: BLE001 - 清零失败只影响下次计数起点
            degrade("plan_gate.refusals", "打回计数清零失败", e)


def enforce_plan_gate(
    store: Any,
    old: dict,
    new: dict,
    *,
    review_llm: Any,
    console: Any = None,
    reason: str = "",
    override: bool = False,
) -> None:
    """规划落盘闸门总入口（``PlanStore.mutate`` 在 ``_atomic_write`` 前调用）。

    不通过时抛 :class:`PlanGateRejected`（调用方不得写盘、不留史）。

    处置顺序：
    1. ``override=True`` → 写豁免台账（人工拍板路径），返回；
    2. 开关关（``NOVEL_PLAN_GATE_REVIEW=0``）→ 留痕 source=disabled，返回；
    3. 确定性硬冲突 → 留痕 source=deterministic，抛 PlanGateRejected；
    4. ``review_llm is None`` → degrade 留痕放行（**有意设计**：人工编辑路径，
       人在场拍板；规划 agent 链路必须注入 llm 得到完整闸门）；
    5. LLM 评审 pass → 留痕、清空该 signature 打回计数，返回；
       revise → 计数 ≤2 抛 PlanGateRejected（打回重规划）；≥3 → 升级检查点
       挂起 + 抛 PlanGateRejected（不形成打回死循环）；
    6. 评审 unavailable → 升级检查点挂起 + 抛 PlanGateRejected（**不放行**）。
    """
    project_dir = Path(store.project_dir)
    keys = _changed(old, new)
    signature = ",".join(keys)

    # 1. 人工拍板豁免路径：写台账，不评审
    if override:
        try:
            p = project_dir / PLAN_GATE_WAIVERS
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {
                            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            "reason": reason[:300],
                            "keys_changed": keys,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except Exception as e:  # noqa: BLE001 - 台账落盘失败不阻断豁免本身
            degrade("plan_gate.waiver", "豁免台账落盘失败", e)
        _log(project_dir, verdict="waived", source="override",
             feedback=reason, reason=reason, keys_changed=keys)
        return

    # 2. 总闸关闭
    if not plan_gate_enabled():
        _log(project_dir, verdict="pass", source="disabled",
             feedback="规划闸门开关关闭", reason=reason, keys_changed=keys)
        return

    # 3. 确定性硬冲突（零 LLM，恒拦）
    conflicts = hard_conflict_check(project_dir, new)
    if conflicts:
        feedback = "\n".join(conflicts)
        _log(project_dir, verdict="rejected", source="deterministic",
             feedback=feedback, reason=reason, keys_changed=keys)
        raise PlanGateRejected(feedback)

    # 4. 无 LLM 注入 = 人工编辑路径（有意放行 + 留痕）
    if review_llm is None:
        degrade(
            "plan_gate.no_llm",
            "规划写入未注入评审 LLM（人工编辑路径），留痕放行；"
            "规划 agent 链路必须注入 llm 获得完整闸门",
        )
        _log(project_dir, verdict="pass", source="no_llm",
             feedback="无 LLM 注入（人工路径），留痕放行", reason=reason, keys_changed=keys)
        return

    # 5. LLM 评审
    result = review_plan_diff(project_dir, old, new, llm=review_llm, reason=reason)
    if result.verdict == "pass":
        _clear_refusal(project_dir, signature)
        return

    if result.verdict == "revise":
        count = _bump_refusal(project_dir, signature)
        if count <= REFUSAL_LIMIT:
            _log(project_dir, verdict="rejected", source="llm",
                 feedback=result.feedback, reason=reason, keys_changed=keys)
            raise PlanGateRejected(
                f"规划闸门评审未通过（第 {count}/{REFUSAL_LIMIT + 1} 次打回），"
                "请按反馈修订后重新提交：\n" + result.feedback
            )
        # 两次修订仍不过 → 升级检查点挂起，把争议摆给作者
        from agent.core.engine.checkpoint import raise_checkpoint

        _log(project_dir, verdict="rejected", source="llm_escalate",
             feedback=result.feedback, reason=reason, keys_changed=keys)
        raise_checkpoint(
            project_dir,
            pause_reason="规划闸门：两次修订仍不过",
            risks=[result.feedback],
            actions=[
                "checkpoint-continue -d <项目>   # 人工拍板认可规划，放行继续",
                "adjust-route -d <项目> --intent <修改方向>  # 按评审意见调整路线后放行",
            ],
            console=console,
        )
        raise PlanGateRejected(
            "规划闸门：两次修订仍未通过评审，已挂起待人工裁决。\n" + result.feedback
        )

    # 6. 评审不可用（unavailable）：不放行，挂起
    from agent.core.engine.checkpoint import raise_checkpoint

    _log(project_dir, verdict="rejected", source="unavailable",
         feedback=result.feedback, reason=reason, keys_changed=keys)
    raise_checkpoint(
        project_dir,
        pause_reason="规划闸门评审不可用（LLM 不可用）",
        risks=[result.feedback or "规划评审 LLM 不可用"],
        actions=[
            "checkpoint-continue -d <项目>   # 人工拍板放行（跳过本次评审）",
            "retry  # LLM 恢复后重试（RESUME 恢复写作流）",
        ],
        console=console,
    )
    raise PlanGateRejected("评审不可用，已挂起待人工/重试")


# ---------------------------------------------------------------- 评审模型分档
def resolve_review_llm(fallback: Any, console: Any = None) -> Any:
    """评审模型分档（登记单 20261004·件 8）。

    env ``NOVEL_REVIEW_MODEL_PROFILE`` 指定档案名时，用该档案（便宜档）建
    **独立评审 Gateway**；未设置/档案缺失/构建失败 → 沿用调用方原 llm。
    """
    try:
        from agent.base.model_profiles import review_llm_kwargs

        kwargs = review_llm_kwargs()
    except Exception as e:  # noqa: BLE001 - 档案机制异常降级沿用原 llm
        degrade("plan_gate.review_profile", "评审模型档案解析失败，沿用原 llm", e)
        return fallback
    if not kwargs:
        return fallback
    try:
        from agent.client.gateway_adapter import create_gateway_from_llm_config
        from agent.base.llm import LLMConfig

        gw = create_gateway_from_llm_config(LLMConfig(**kwargs))
        if console is not None:
            try:
                console.print(
                    f"[cyan]规划评审使用独立模型档（{kwargs.get('model') or '?'}）[/cyan]"
                )
            except Exception:  # noqa: SILENT_DEGRADE reason=logging-only
                pass
        return gw
    except Exception as e:  # noqa: BLE001 - 独立网关构建失败降级沿用原 llm
        degrade("plan_gate.review_profile", "独立评审网关构建失败，沿用原 llm", e)
        return fallback
