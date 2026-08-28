"""D 多维 LLM 质量审查测试（增量 D / T04）

覆盖：
- LLMQualityRule._check：llm=None 降级空 / 正常解析 blocking / 异常降级空
- LLMBackedChecker.run_rules：合并单次调用 / llm=None 空 / 异常降级空 / 超时降级空
- QualityChecker.check：跳过 LLM 规则（仅通用层规则生效，无需 LLM）
- QualityChecker.llm_rules：注册 4 个维度规则
"""

from __future__ import annotations

import json
from pathlib import Path

from agent.client import LLMResponse
from agent.core.quality.quality_checker import (
    Issue,
    LLMBackedChecker,
    LLMQualityRule,
    QualityChecker,
    Severity,
)


# ============================================================
# 假 LLM：返回固定 JSON 负载
# ============================================================
class _JsonLLM:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.calls = 0

    def chat_utility(self, messages, **kwargs) -> LLMResponse:
        self.calls += 1
        return LLMResponse(
            text=json.dumps(self._payload, ensure_ascii=False),
            raw={},
            usage={},
        )


class _RaisingLLM:
    def chat_utility(self, messages, **kwargs) -> LLMResponse:
        raise RuntimeError("boom")


# ============================================================
# LLMQualityRule
# ============================================================
class TestLLMQualityRule:
    def _rule(self) -> LLMQualityRule:
        return LLMQualityRule(
            id="d_ooc",
            name="角色一致性(OOC)",
            layer=__import__("agent.core.quality_checker", fromlist=["RuleLayer"]).RuleLayer.COMMON,
            severity=Severity.BLOCK,
            dimension="ooc",
            prompt_template="OOC 判定",
        )

    def test_check_llm_none_returns_empty(self) -> None:
        rule = self._rule()
        assert rule.check is not None
        assert rule._check("正文", {}, None) == []

    def test_check_blocking_issue(self) -> None:
        llm = _JsonLLM({"ooc": {"score": 1, "pass": False, "blocking": True, "issue": "OOC 崩坏"}})
        rule = self._rule()
        issues = rule._check("正文", {}, llm)
        assert len(issues) == 1
        assert issues[0].rule_id == "d_ooc"
        assert issues[0].severity == Severity.BLOCK
        assert "OOC" in issues[0].description

    def test_check_pass_returns_empty(self) -> None:
        llm = _JsonLLM({"ooc": {"score": 9, "pass": True, "blocking": False, "issue": ""}})
        rule = self._rule()
        assert rule._check("正文", {}, llm) == []

    def test_check_exception_degrades_empty(self) -> None:
        rule = self._rule()
        assert rule._check("正文", {}, _RaisingLLM()) == []


# ============================================================
# LLMBackedChecker（合并单次调用）
# ============================================================
_MERGED_JSON = {
    "cool_point": {"score": 8, "pass": True, "blocking": False, "issue": ""},
    "ooc": {"score": 2, "pass": False, "blocking": True, "issue": "OOC 崩坏"},
    "coherence": {"score": 7, "pass": True, "blocking": False, "issue": ""},
    "pacing_hook": {"score": 8, "pass": True, "blocking": False, "issue": ""},
}


class TestLLMBackedChecker:
    def _rules(self) -> list[LLMQualityRule]:
        qc = QualityChecker(Path("/tmp/x"))
        return qc.llm_rules

    def test_run_rules_llm_none_returns_empty(self) -> None:
        checker = LLMBackedChecker(None)
        assert checker.run_rules(self._rules(), "正文", {}) == []

    def test_run_rules_merges_blocking(self) -> None:
        checker = LLMBackedChecker(_JsonLLM(_MERGED_JSON))
        issues = checker.run_rules(self._rules(), "正文", {})
        # 仅 ooc 维度 blocking
        assert len(issues) == 1
        assert issues[0].rule_id == "d_ooc"
        assert issues[0].severity == Severity.BLOCK

    def test_run_rules_warn_not_blocking(self) -> None:
        payload = {
            "cool_point": {"score": 8, "pass": True, "blocking": False, "issue": ""},
            "ooc": {"score": 9, "pass": True, "blocking": False, "issue": ""},
            "coherence": {"score": 5, "pass": False, "blocking": False, "issue": "略显跳跃"},
            "pacing_hook": {"score": 8, "pass": True, "blocking": False, "issue": ""},
        }
        checker = LLMBackedChecker(_JsonLLM(payload))
        issues = checker.run_rules(self._rules(), "正文", {})
        # coherence 仅 WARN（非 blocking）→ 进入 issues 但不阻断
        assert any(i.rule_id == "d_coherence" for i in issues)
        assert all(i.severity == Severity.WARN for i in issues)

    def test_run_rules_exception_degrades_empty(self) -> None:
        checker = LLMBackedChecker(_RaisingLLM())
        assert checker.run_rules(self._rules(), "正文", {}) == []

    def test_run_rules_empty_rules_returns_empty(self) -> None:
        checker = LLMBackedChecker(_JsonLLM(_MERGED_JSON))
        assert checker.run_rules([], "正文", {}) == []


# ============================================================
# QualityChecker 集成
# ============================================================
class TestQualityCheckerD:
    def test_llm_rules_count(self) -> None:
        qc = QualityChecker(Path("/tmp/x"))
        assert len(qc.llm_rules) == 4
        assert all(isinstance(r, LLMQualityRule) for r in qc.llm_rules)

    def test_check_skips_llm_rules_no_network(self) -> None:
        # 禁用 LLM（llm=None），check 仅跑通用层规则，LLM 维度规则被跳过
        qc = QualityChecker(Path("/tmp/x"), llm=None)
        # 超过禁用词上限 → 通用层 banned_words 报 BLOCK
        report = qc.check("突然突然突然突然突然", {})
        assert report.passed is False
        assert any(i.rule_id == "banned_words" for i in report.issues)
        # 不触发任何 LLM 调用（维度规则被跳过）
        for r in qc.llm_rules:
            assert r._check("正文", {}, None) == []

    def test_check_pass_when_clean(self) -> None:
        qc = QualityChecker(Path("/tmp/x"), llm=None)
        report = qc.check("林寻 逃亡 推演 撕开缺口，追兵已至。", {})
        # 无禁用词超限 → 通用层通过；维度规则跳过 → 整体通过
        assert report.passed is True
