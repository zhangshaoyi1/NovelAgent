"""红线：契约字段标签不得泄漏进正文（2026-09-18，受控实验 ch3 实测）。

背景
----
``prompts/m3/outline.md`` v4 把契约字段名定为
``章首钩子/章尾钩子/爽点/目标情绪/在场/禁/验收``，而
``guardrails._META_LEAK_RE`` 恰把 ``章末悬念`` 列为 **error 级**禁词
⇒ **同一批词：供给侧当"字段名"用、检测侧当"污染词"拦**，两边各写一份字面量。

实测泄漏（``chapters/ch003.md:254``）::

    - *钩子：章末悬念从笼统的「外域词汇」收敛到更具体的……*

⇒ 被「写作元指令泄漏」判 blocking ⇒ ch3 未过门禁（**重写后仍失败**）。
这是「提示词语言锚 ↔ 消费者解析式是同一件事的两半」的**反向破裂**。

断言口径
--------
1. 词表唯一真源：guardrails 的检测词表由 ``chapter_contract.CONTRACT_LEAK_LABELS``
   派生；提示词里的字段名与 ``CONTRACT_FIELDS`` **机器交叉核对**（缺失/多余须为 0）；
2. 类级指纹：与措辞无关 —— 写手把字段名改写成「钩子：…」也拦得住；
3. **判据产出必须在修复端可达**：判 blocking 的批注行必须能被确定性删除
   （否则又是一次「判而不可修」⇒ 整章重写）；
4. 生产入口可达：``_run_deslop``（写时门禁**之前**）真的清掉，行为级断言。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import agent

from agent.core.quality.guardrails.guardrails import _META_LEAK_RE, Guardrails
from agent.core.story.chapter_contract import (
    CONTRACT_FIELDS,
    CONTRACT_LEAK_LABELS,
    find_contract_annotations,
    is_contract_annotation_line,
    strip_contract_annotations,
)
from agent.core.story.text_hygiene import clean_hard_pollutions, scan_hard_pollutions

_PROMPTS = Path(agent.__file__).parent / "prompts"

#: 受控实验 ch003.md:254 的**真实**泄漏行
LEAK_LINE = (
    "- *钩子：章末悬念从笼统的「外域词汇」收敛到更具体的「为什么上古玉简里会出现和外"
    "域有关的词汇」，并呼应玉简背后的账目结构异常（前几行是数字，最后变成一句无解释的"
    "话），让悬念更具体可感。*"
)
SAMPLE = f"他握着玉简，指节发白。\n{LEAK_LINE}\n窗外的风停了一瞬。"


def _meta_leak_hits(text: str) -> list[str]:
    g = Guardrails()
    return [
        v.rule_id for v in g.check(text).violations if "meta" in v.rule_id or "hard" in v.rule_id
    ]


def test_real_leak_line_is_blocking() -> None:
    """ch3 真实泄漏行必须被判 blocking（两端：guardrails 元指令 + L2 硬污染）。"""
    assert find_contract_annotations(SAMPLE), "批注指纹未识别 ch3 泄漏行"
    assert "meta_instruction_leak" in _meta_leak_hits(SAMPLE), _meta_leak_hits(SAMPLE)
    assert any("契约批注" in h for h in scan_hard_pollutions(SAMPLE)), scan_hard_pollutions(SAMPLE)


def test_annotation_is_deterministically_removable() -> None:
    """判 blocking 的批注行必须能确定性删除 —— 且删完两端都不再报（闭环）。

    这是本红线最核心的一条：ch3 的失分点不是"没检测到"，而是**检测到却只能整章重写**。
    """
    stripped, traced = strip_contract_annotations(SAMPLE)
    assert traced, "清理未留痕（批测反思的『对策→执行记录』闭环缺证据）"
    assert "章末悬念" not in stripped, stripped
    assert "他握着玉简" in stripped and "窗外的风停了一瞬" in stripped, (
        f"清理误删了叙事正文：{stripped}"
    )
    assert not find_contract_annotations(stripped)
    assert "meta_instruction_leak" not in _meta_leak_hits(stripped), (
        f"清理后仍被 guardrails 拦：{_meta_leak_hits(stripped)}"
    )
    assert not scan_hard_pollutions(stripped), scan_hard_pollutions(stripped)


def test_l2_clean_removes_annotation() -> None:
    """落盘兜底路径（rewrite / paragraph_rewriter）也能修，不只写章路径。"""
    cleaned, traced = clean_hard_pollutions(SAMPLE)
    assert not scan_hard_pollutions(cleaned), scan_hard_pollutions(cleaned)
    assert any("契约批注" in t for t in traced), traced


def test_guardrail_wordlist_derived_from_ssot() -> None:
    """guardrails 检测词表必须涵盖契约真源的全部元词标签。"""
    missing = [x for x in CONTRACT_LEAK_LABELS if x not in _META_LEAK_RE.pattern]
    assert not missing, f"检测词表与契约真源脱钩（prompt 改名即双向破裂）：{missing}"


def test_common_short_words_not_in_wordlist() -> None:
    """常用短词**有意**不进词表（正文里「他站在场边」「验收了药材」是正常用语）。"""
    for word in ("爽点", "在场", "禁", "验收"):
        assert word not in CONTRACT_LEAK_LABELS, f"{word} 进词表会大量误杀正文"
        assert not is_contract_annotation_line(f"他站在场边，看着远处。"), word


def test_prompt_field_names_match_ssot() -> None:
    """机器交叉核对：提示词里的契约字段名必须都在 ``CONTRACT_FIELDS`` 内。

    这是"文档/提示词不会悄悄漂移"的可验证判据 —— 比人眼通读可靠。

    2026-10-01 结构化收口（登记单 20261001_m3_chapter_hooks结构化）后改为三方对齐：
    提示词声明 JSON 键（chapter_hooks 元素）→ ``m3_outline._HOOK_FIELDS`` 把键映射为
    写手侧中文标签 → 中文标签必须 ⊆ ``chapter_contract.CONTRACT_FIELDS``。
    """
    src = (_PROMPTS / "m3" / "outline.md").read_text(encoding="utf-8")
    prompt_keys = {m.strip() for m in re.findall(r'"([a-z_]{2,16})"\s*:', src)}
    assert {"ch", "tier", "open_hook", "end_hook", "cool_point", "emotion",
            "present", "forbidden", "acceptance"} <= prompt_keys, (
        "提示词未声明 chapter_hooks 数组的标准字段集（JSON 键漂移）"
    )

    from agent.workflows.planning.m3_outline import M3OutlineWorkflow
    from agent.core.story.chapter_contract import CONTRACT_FIELDS, PACE_TIER_FIELD

    hook_keys = {k for k, _ in M3OutlineWorkflow._HOOK_FIELDS}
    assert hook_keys == {"open_hook", "end_hook", "cool_point", "emotion",
                         "present", "forbidden", "acceptance"}, (
        "m3_outline._HOOK_FIELDS 与提示词声明的 JSON 键不一致"
    )
    labels = {label for _, label in M3OutlineWorkflow._HOOK_FIELDS}
    unknown = labels - set(CONTRACT_FIELDS)
    assert not unknown, (
        f"提示词契约标签 {sorted(unknown)} 未在 chapter_contract.CONTRACT_FIELDS 登记 —— "
        "否则检测侧无从派生"
    )
    assert PACE_TIER_FIELD in CONTRACT_FIELDS, "档位字段脱离 SSOT"


def test_class_level_fingerprint_catches_variants() -> None:
    """与措辞无关的类级指纹：写手改写字段名也拦得住。"""
    variants = [
        "- *钩子：他把线索收进袖中*",
        "- 爽点：当场打脸",
        "* 目标情绪：压抑转释然",
        "- 在场=林凡，周管事",
    ]
    for v in variants:
        assert is_contract_annotation_line(v), f"类级指纹漏掉变体：{v}"
        assert find_contract_annotations(v), v


def test_narrative_text_and_frontmatter_untouched() -> None:
    """不误杀：正常叙事句、含关键词的句子、YAML front-matter 列表都不动。"""
    for ok_line in (
        "他站在场边，看着远处的人群，什么也没说。",
        "「禁地不可入内。」老者沉声道。",
        "她验收了那批药材，点点头。",
    ):
        assert not is_contract_annotation_line(ok_line), ok_line

    fm = (
        "---\nchapter: 3\nevidence_chain:\n  characters:\n"
        "  - field: 身份/动机\n---\n正文开始了。"
    )
    out, traced = strip_contract_annotations(fm)
    assert out == fm and not traced, (out, traced)


class _QuietConsole:
    def print(self, *a, **k) -> None:  # noqa: ANN002, ANN003
        pass


def test_production_entry_strips_annotation(tmp_path: Path) -> None:
    """生产入口 ``_run_deslop``（门禁**之前**）真的清掉契约批注并落轨迹。"""
    from agent.workflows.writing.agentic_write import AgenticWriteWorkflow

    wf = object.__new__(AgenticWriteWorkflow)
    wf.project_dir = tmp_path
    wf.console = _QuietConsole()
    wf.deslop_enabled = True
    wf.llm = None

    out = wf._run_deslop(SAMPLE, {"chapter_num": 3})
    assert "章末悬念" not in out, f"生产入口未清契约批注（门禁仍会判 blocking）：{out}"
    trace = tmp_path / ".state" / "l1_trace.jsonl"
    assert trace.exists(), "契约批注清理未落执行轨迹"
    rec = json.loads(trace.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["ch"] == 3
    assert any("契约批注" in r for r in rec["replaced"]), rec
