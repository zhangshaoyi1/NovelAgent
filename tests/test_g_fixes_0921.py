# -*- coding: utf-8 -*-
"""红线：G1–G3 修复（2026-09-21 灵荒工坊实验实锤的三处缺口）。

- **G1**（回溯修复越修越多）：hint 必须携带冲突**原文定位**（quote）与
  **既定事实约束**——实验中设定冲突 1→0→3，重写只知"有 N 处冲突"不知
  在哪，且无"保留章不得改"约束 ⇒ 盲改引入新冲突。
- **G2**（评分降级给满分）：综合分聚合必须排除 confidence=0 的维度——
  实验 readability conf=0 value=100 把综合分抬到 85.71（"不知道"被读成
  高分，掩盖真实短板；与 A1 evidence_status 三态同族）。
- **G3**（标题泄漏契约标签）：ch004 落盘「第 4 章 · 档位=垫片」——
  「档位=垫片」恰 4 字符躲过 _TITLE_MIN_LEN=4；词表必须由
  ``CONTRACT_FIELDS`` 派生（纪律 #19），且「字段名+赋值号」形态
  不得误杀正常正文用语（他站在场边/验收了药材）。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.core.quality.guardrails.guardrails import (
    _TITLE_CONTRACT_LABEL_RE,
    Guardrails,
)
from agent.core.story.chapter_contract import CONTRACT_FIELDS
from agent.workflows.pipeline.agentic_pipeline_types import build_rewrite_hint

SRC = Path(__file__).resolve().parents[1] / "src" / "agent"


def _mk_report() -> SimpleNamespace:
    """构造带一个失败维度 + quote 明细的体检报告（模拟 G1 现场形态）。"""
    issue = {"type": "设定", "severity": "high",
             "desc": "ch3 的灵肥配额三成与 ch1 的对赌条件冲突",
             "quote": "扣下他三成配额"}
    dim = SimpleNamespace(
        name="setting_consistency_high", label="设定一致", value=3.0,
        threshold=0.0, direction="<=", passed=False,
        evidence=SimpleNamespace(
            confidence=1.0, rationale="三处冲突", issues=[issue]),
    )
    return SimpleNamespace(dimensions=[dim], escalated_reason="", repair=None)


class TestG1RewriteHint:
    def test_r1_issue_quote_included_in_hint(self) -> None:
        """R1：计数维 issues 的 **quote（原文定位）** 必须编进 hint——
        Writer 定位不到原文就只能盲改（实验：1→0→3 越修越多）。"""
        hint = build_rewrite_hint(_mk_report(), [5])
        assert "原文：" in hint and "扣下他三成配额" in hint, hint

    def test_r2_established_fact_constraint_present(self) -> None:
        """R2：hint 必须含「既定事实约束」——未回退章节是既定事实、
        冲突以保留章为准。防重写时改动 ch1-4 的既定设定。"""
        hint = build_rewrite_hint(_mk_report(), [5])
        assert "既定事实" in hint, hint
        assert "以保留章为准" in hint, hint

    def test_r3_quote_absent_no_tail(self) -> None:
        """R3：issue 无 quote 字段时不得输出空「原文：」尾巴（形态干净）。"""
        dim = SimpleNamespace(
            name="logic_holes", label="逻辑漏洞", value=1.0,
            threshold=0.0, direction="<=", passed=False,
            evidence=SimpleNamespace(confidence=1.0, rationale="",
                                     issues=[{"desc": "第5章跳跃缺乏动机"}]),
        )
        hint = build_rewrite_hint(
            SimpleNamespace(dimensions=[dim], escalated_reason="", repair=None), [5])
        assert "跳跃缺乏动机" in hint
        assert "原文：「」" not in hint

    def test_r4_low_confidence_issues_not_leaked(self) -> None:
        """R4：证据不可信（conf=0）时 issues **不得**编进 hint（防误导重写）。"""
        dim = SimpleNamespace(
            name="setting_consistency_high", label="设定一致", value=3.0,
            threshold=0.0, direction="<=", passed=False,
            evidence=SimpleNamespace(confidence=0.0, rationale="",
                                     issues=[{"desc": "臆测冲突", "quote": "xx"}]),
        )
        hint = build_rewrite_hint(
            SimpleNamespace(dimensions=[dim], escalated_reason="", repair=None), [5])
        assert "臆测冲突" not in hint, hint


class TestG2ScoreAggregation:
    def test_r5_aggregation_skips_zero_confidence(self) -> None:
        """R5：综合分聚合**必须**跳过 confidence=0 的维度（源级断言）。

        实测：readability conf=0 value=100 参与平均 ⇒ 综合分 85.71 混入假 100。
        no_data 不进分母 —— 与 A1 evidence_status 三态对齐。
        """
        src = (SRC / "agents" / "evaluator_dims.py").read_text(encoding="utf-8")
        # AST 级：norm 聚合循环体内存在 conf<=0 ⇒ continue 的守卫
        tree = ast.parse(src)
        ok = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.For):
                continue
            for sub in ast.walk(node):
                if isinstance(sub, ast.If):
                    test_src = ast.unparse(sub.test)
                    has_conf = "confidence" in test_src and "<=" in test_src
                    has_continue = any(
                        isinstance(s, ast.Continue) for s in ast.walk(sub)
                    )
                    if has_conf and has_continue:
                        ok = True
        assert ok, "聚合循环缺少 conf<=0 ⇒ continue 守卫（G2 回退）"

    def test_r6_conf_zero_value_not_safe_default_score(self) -> None:
        """R6：评分降级路径的 value（安全默认满分）**不得**被视为可信读数。

        `_record_degraded` 必须带 confidence=0 证据（既有语义，钉住防回退）。
        """
        src = (SRC / "core" / "quality" / "scoring" / "reader_appeal.py").read_text(
            encoding="utf-8")
        assert "build_degraded_evidence" in src, "降级证据构造缺失"


class TestG3TitleContractLabel:
    def _check(self, title_body: str) -> str | None:
        g = Guardrails()
        text = f"# 第 4 章 · {title_body}\n\n正文段落。"
        return g._check_title(text)

    def test_r7_tier_label_title_is_rejected(self) -> None:
        """R7：实测泄漏形态「档位=垫片」（恰 4 字符躲过 _TITLE_MIN_LEN）必须判失败。"""
        msg = self._check("档位=垫片")
        assert msg is not None and "契约标签" in msg, msg

    def test_r8_all_contract_fields_with_assign_are_rejected(self) -> None:
        """R8：任一契约字段名 + 赋值号的标题形态都拦得住（派生词表全覆盖）。"""
        for f in CONTRACT_FIELDS:
            assert _TITLE_CONTRACT_LABEL_RE.search(f"{f}=x"), f"字段 {f} 未被派生词表覆盖"

    def test_r9_normal_prose_titles_pass(self) -> None:
        """R9：正常场景化标题不得误杀（含含契约字面词但**无赋值号**的）。"""
        for t in ("矿道封门", "枯草根须", "平静的午后", "验收之日", "他站在场边"):
            assert self._check(t) is None, f"误杀正常标题：{t} -> {self._check(t)}"

    def test_r10_wordlist_derived_not_copied(self) -> None:
        """R10：标题词表必须由 ``CONTRACT_FIELDS`` **派生**（纪律 #19——
        禁止第二份手写字面量：prompt 改名即双向破裂）。"""
        assert _TITLE_CONTRACT_LABEL_RE.pattern, "词表为空"
        for f in CONTRACT_FIELDS:
            assert re.escape(f) in _TITLE_CONTRACT_LABEL_RE.pattern, (
                f"字段 {f} 不在派生词表中（疑似手写副本）"
            )

    def test_r11_short_label_title_not_saved_by_length_check_alone(self) -> None:
        """R11：无赋值号的 4 字契约值（如「高潮章」）**不拦**——长度已达标，
        避免把"档位值"误当标签（作用域收窄：只拦 ``字段名=`` 泄漏形态）。"""
        assert self._check("高潮篇章") is None


@pytest.mark.parametrize("bad", ["档位=垫片", "章首钩子=清晨点名", "爽点=打脸"])
def test_r12_parametrized_leak_forms(bad: str) -> None:
    g = Guardrails()
    msg = g._check_title(f"# 第 2 章 · {bad}\n\n正文。")
    assert msg is not None and "契约标签" in msg, f"{bad} 未被拦：{msg}"
