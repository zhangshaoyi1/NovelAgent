"""连续性账本 · LLM delta 生产者（竞品差距改进计划 P1-5 收尾）。

背景：``m5_persist._archive_chapter`` 的确定性抽取器只能抓「状态/归属/计数」类
事实与章尾 must_carry，抓不到**信息差变化**（谁知道什么）与**开环剧情线推进/
闭环**——这两类正是下一章上下文投影最需要的长程一致性信息，此前一直是空。
本模块把 PRD §17.4 P1-5 的最后一环接上：章后由 LLM 对照期初投影 + 本章正文
产出 ``LedgerDelta`` 增量，代码层经 ``ContinuityLedgerStore.apply_delta`` 严格
校验（extra="forbid" / 预检 / 幂等）后应用——LLM 只产 delta、不产全量，幻觉
字段或编造 loop_id 会在校验层被显式拒绝。

失败语义（红线：失败必须显性化）：LLM 调用 / JSON 解析 / delta 校验任一失败
→ ``degrade()`` 记录后返回 False，**不阻断写章**（降级不阻断），账本保持
确定性抽取器已提交的状态，绝不允许静默半应用。
"""

from __future__ import annotations

import logging
from typing import Any

from agent.core.infra.degrade import degrade
from agent.core.infra.prompt_manager import pm
from agent.utils import parse_llm_json

logger = logging.getLogger(__name__)

# 投影有界化：结算提示词只带「判断增量所需」的最小期初视图
_MAX_FACTS = 40
_MAX_LOOPS = 30
_MAX_KNOWLEDGE = 20
_MAX_BODY = 6000


def _render_opening_state(ledger: Any) -> str:
    """把期初账本渲染成结算员可读的有界文本。"""
    lines: list[str] = []
    facts = ledger.facts[-_MAX_FACTS:]
    if facts:
        lines.append("已登记事实（domain/主体/字段=当前值）：")
        for f in facts:
            lines.append(f"- {f.domain}/{f.subject_id}/{f.field} = {f.value}")
    loops = ledger.open_loops[-_MAX_LOOPS:]
    if loops:
        lines.append("未闭环剧情线（loop_op 只能引用这里的 loop_id）：")
        for lo in loops:
            lines.append(
                f"- loop_id={lo.loop_id} | kind={lo.kind} | status={lo.status} | {lo.detail}"
            )
    else:
        lines.append("未闭环剧情线：（无）")
    knowledge = ledger.knowledge[-_MAX_KNOWLEDGE:]
    if knowledge:
        lines.append("信息差（主体→谁/知情度）：")
        for k in knowledge:
            lines.append(f"- {k.subject_id} → {k.audience}:{k.audience_id} = {k.level}")
    if not lines:
        return "（空账本：本章为账本首批登记，只登记本章确实确立的事实）"
    return "\n".join(lines)


def _anchor_commit_ids(delta_dict: dict[str, Any], commit_id: str) -> dict[str, Any]:
    """证据链锚统一收口为本 commit——LLM 不得伪造他章证据（与 apply 层口径一致）。"""
    for f in delta_dict.get("facts") or []:
        f["source_commit_id"] = commit_id
    for k in delta_dict.get("knowledge") or []:
        k["source_commit_id"] = commit_id
    for op in delta_dict.get("loop_ops") or []:
        op["source_commit_id"] = commit_id
    return delta_dict


def produce_and_apply_delta(
    project_dir: str,
    llm: Any,
    *,
    chapter_num: int,
    chapter_title: str,
    chapter_text: str,
) -> bool:
    """章后 LLM 结算：产出并应用本章 ``LedgerDelta``。

    Returns:
        True=delta 已应用落盘；False=结算失败（已 degrade 记录，账本保持原样）。
    """
    try:
        from agent.client.gateway_adapter import chat_utility
        from agent.core.continuity import ContinuityLedgerStore
        from agent.core.continuity.delta import LedgerDelta, LedgerDeltaError
    except Exception as e:  # noqa: BLE001 - 依赖缺失属环境问题，显式降级
        degrade("ledger_delta.import", "连续性结算依赖不可用，本章跳过 LLM 结算", e)
        return False

    commit_id = f"ch{chapter_num:03d}"
    try:
        store = ContinuityLedgerStore(project_dir)
        ledger = store.load()
        opening_state = _render_opening_state(ledger)
        body = (chapter_text or "").strip()[:_MAX_BODY]
        if not body:
            return False

        user = pm.get("m5.ledger_delta").render_user(
            chapter_num=chapter_num,
            chapter_title=chapter_title,
            commit_id=commit_id,
            opening_state=opening_state,
            chapter_text=body,
        )
        system = pm.get("m5.ledger_delta").render_system(
            chapter_num=chapter_num,
            chapter_title=chapter_title,
            commit_id=commit_id,
            opening_state=opening_state,
            chapter_text=body,
        )

        delta_dict: dict[str, Any] | None = None
        last_err: Exception | None = None
        for attempt in range(2):  # 解析/校验失败附错误详情重试 1 次（G4 约定）
            try:
                resp = chat_utility(
                    llm,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user if attempt == 0
                         else user + f"\n\n【上次结算失败原因，务必修正】\n{last_err}"},
                    ],
                    max_tokens=4096,
                    enable_thinking=False,
                )
                parsed = parse_llm_json(resp)
                if not isinstance(parsed, dict):
                    raise ValueError("结算输出不是 JSON 对象")
                if parsed.get("chapter") != chapter_num:
                    raise ValueError(
                        f"delta 章号不符：期望 {chapter_num}，实际 {parsed.get('chapter')}"
                    )
                parsed = _anchor_commit_ids(parsed, commit_id)
                parsed["chapter"] = chapter_num
                delta = LedgerDelta(**parsed)  # extra="forbid"，幻觉字段在此显式报错
                delta_dict = delta.model_dump()
                break
            except (ValueError, TypeError) as e:
                last_err = e

        if delta_dict is None:
            degrade(
                "ledger_delta.settle",
                f"第{chapter_num}章 LLM 账本结算失败（重试 1 次仍失败），"
                "本章仅保留确定性抽取事实",
                last_err,
            )
            return False

        store.apply_delta(LedgerDelta(**delta_dict))
        logger.info("[ledger_delta] 第%d章结算完成（%s）", chapter_num, commit_id)
        return True
    except LedgerDeltaError as e:
        degrade(
            "ledger_delta.apply",
            f"第{chapter_num}章账本 delta 校验失败（账本未被修改），"
            "请检查本章是否存在编造 loop_id / 幻觉字段",
            e,
        )
        return False
    except Exception as e:  # noqa: BLE001 - 结算失败降级不阻断写章（失败已显性记录）
        degrade("ledger_delta.unexpected", f"第{chapter_num}章账本结算异常", e)
        return False


__all__ = ["produce_and_apply_delta"]
