"""T-5 QualityChecker 数据化落地测试

覆盖：
- 禁用词超限 → check() 返回 passed=False
- revise_loop 收敛（修订后通过）
- 题材层规则（GENRE_RULES）参与 check()
"""
from __future__ import annotations

from pathlib import Path

from agent.core.quality.quality_checker import (
    BANNED_WORDS,
    GENRE_RULES,
    Issue,
    QualityChecker,
    QualityRule,
    RuleLayer,
    Severity,
)


def _snapshot_genre_rules():
    return list(GENRE_RULES)


def test_banned_words_exceed_limit_fails(tmp_path: Path) -> None:
    """禁用词出现次数超过上限 → passed=False 且定位 banned_words 规则"""
    qc = QualityChecker(project_dir=tmp_path)
    text = "突然忽然就在这时微微一笑" * 2  # 4 类禁用词各 2 次 = 8 > 2
    report = qc.check(text)
    assert report.passed is False
    assert any(i.rule_id == "banned_words" for i in report.issues)


def test_banned_words_within_limit_passes(tmp_path: Path) -> None:
    """禁用词未超限 → passed=True"""
    qc = QualityChecker(project_dir=tmp_path)
    report = qc.check("主角拔剑，剑光如雪。", ctx={})
    assert report.passed is True


def test_revise_loop_converges(tmp_path: Path) -> None:
    """revise_loop 在修订后收敛为通过"""
    qc = QualityChecker(project_dir=tmp_path)
    bad = "突然忽然就在这时微微一笑" * 3

    def revise_fn(text: str, issues: list[Issue]) -> str:
        cleaned = text
        for w in BANNED_WORDS:
            cleaned = cleaned.replace(w, "")
        return cleaned

    final_text, report = qc.revise_loop(bad, {}, revise_fn)
    assert report.passed is True
    assert report.revision_attempts >= 1
    assert "突然" not in final_text


def test_genre_rules_participate_in_check(tmp_path: Path) -> None:
    """题材层规则（GENRE_RULES）被合并进 checker 并参与 check()"""
    original = _snapshot_genre_rules()
    GENRE_RULES.clear()
    try:
        genre_rule = QualityRule(
            id="G-test",
            name="测试题材规则",
            layer=RuleLayer.GENRE,
            severity=Severity.BLOCK,
            check=lambda chapter_text, ctx, llm: [  # noqa: ARG005
                Issue(
                    rule_id="G-test",
                    severity=Severity.BLOCK,
                    description="题材规则命中",
                )
            ],
            revise_hint="测试修订提示",
        )
        GENRE_RULES.append(genre_rule)
        qc = QualityChecker(project_dir=tmp_path)
        report = qc.check("普通正文，无禁用词", ctx={})
        assert any(i.rule_id == "G-test" for i in report.issues)
    finally:
        GENRE_RULES[:] = original
