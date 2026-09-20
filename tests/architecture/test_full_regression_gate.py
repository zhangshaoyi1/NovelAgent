"""B2（G1）红线：全量回归必须有关卡——「看起来绿」不等于「可信」。

缺口：全量回归（约 19 分钟）此前只在人记得时手动跑，且存在**取证不完整**的
真实事故形态：输出被截断/超时中断、只看退出码不看 FAILED 行、"no tests ran" 被
当成绿。本关卡把"是否可信"变成可判定读数（有汇总行 / 无 failed·errors /
不低于基线），且结论**必须绑 commit**（纪律 #23③）。

锁死的不变式：
    ① 缺汇总行 ⇒ 不可信（不是"没问题"，是"没证据"）；
    ② ``no tests ran`` 不算汇总行；
    ③ 有 failed/errors ⇒ 不可信（看 FAILED 行，不看退出码）；
    ④ 通过数低于基线 ⇒ 不可信（漏跑伪装成"更干净"）；
    ⑤ 判定结论带 commit；基线落盘含 commit；
    ⑥ 关卡脚本存在且真调用判定逻辑（非孤儿设施）。
"""

from __future__ import annotations

from pathlib import Path

from agent.core.infra import regression_gate as rg

CLEAN = "2866 passed, 9 skipped, 56 warnings in 1154.07s (0:19:14)"
ONE_FAIL = "1 failed, 2865 passed, 9 skipped, 56 warnings in 1115.56s (0:18:35)"
TRUNCATED = "tests/foo.py::test_bar\n================ short test summary info ================"


class TestSummaryParsing:
    def test_parses_clean_summary(self) -> None:
        counts = rg.parse_summary(CLEAN)
        assert counts["passed"] == 2866
        assert counts["skipped"] == 9
        assert "failed" not in counts

    def test_parses_failed_summary(self) -> None:
        counts = rg.parse_summary(ONE_FAIL)
        assert counts["failed"] == 1 and counts["passed"] == 2865

    def test_no_summary_returns_empty(self) -> None:
        """R1：无汇总行 ⇒ 空读数（取证不完整）。"""
        assert rg.parse_summary(TRUNCATED) == {}
        assert rg.summary_line(TRUNCATED) is None

    def test_no_tests_ran_is_not_a_summary(self) -> None:
        """R2：``no tests ran`` 是"什么都没跑"，不是"全绿"。"""
        assert rg.summary_line("no tests ran in 0.01s") is None
        assert rg.parse_summary("no tests ran in 0.01s") == {}


class TestVerdict:
    def test_truncated_output_is_untrusted(self) -> None:
        v = rg.evaluate(TRUNCATED, commit="abc1234")
        assert v.ok is False
        assert any("汇总行" in r for r in v.reasons)

    def test_failed_run_is_untrusted(self) -> None:
        """R3：有 FAILED ⇒ 不可信，即使 rc 被误读为 0。"""
        v = rg.evaluate(ONE_FAIL, commit="abc1234")
        assert v.ok is False
        assert any("失败" in r for r in v.reasons)

    def test_clean_run_is_trusted(self) -> None:
        assert rg.evaluate(CLEAN, commit="abc1234").ok is True

    def test_below_baseline_is_untrusted(self) -> None:
        """R4：通过数掉到基线以下 ⇒ 漏跑伪装成"更干净"。"""
        v = rg.evaluate(CLEAN, commit="abc1234", baseline={"passed": 2900, "skipped": 9})
        assert v.ok is False
        assert any("低于基线" in r for r in v.reasons)

    def test_above_baseline_is_trusted_but_noted(self) -> None:
        v = rg.evaluate(CLEAN, commit="abc1234", baseline={"passed": 2800, "skipped": 9})
        assert v.ok is True
        assert any("高于基线" in r for r in v.reasons)

    def test_extra_skips_are_noted(self) -> None:
        v = rg.evaluate(CLEAN, commit="abc1234", baseline={"passed": 2866, "skipped": 0})
        assert any("跳过数高于基线" in r for r in v.reasons)

    def test_verdict_binds_commit(self, tmp_path: Path) -> None:
        """R5：结论必须绑 commit（纪律 #23③）。"""
        v = rg.evaluate(CLEAN, commit="deadbee")
        assert v.to_dict()["commit"] == "deadbee"
        p = rg.record_baseline(tmp_path / "b.json", v)
        assert '"deadbee"' in p.read_text(encoding="utf-8")


class TestGateIsWired:
    def test_script_exists_and_uses_verdict(self) -> None:
        """R6（纪律 #7）：关卡脚本必须真调用判定逻辑，否则是装饰品。"""
        script = Path(__file__).resolve().parents[2] / "scripts" / "full_regression.py"
        assert script.exists(), "全量回归关卡脚本不存在"
        src = script.read_text(encoding="utf-8")
        assert "evaluate(" in src and "run_pytest(" in src
        assert "return 0 if verdict.ok else 1" in src, (
            "关卡未以判定结果决定退出码——不可信结果会被放过"
        )
        assert "--save-raw" in src, (
            "缺少原始输出落盘选项：判定为『不可信』时没有输出就无法归因"
            "（2026-09-20 实测一次 rc=1 且无汇总行的运行因未留存而无法定位）"
        )


class TestPrePushHookUsesTheGate:
    """⑥ 关卡必须真的挂在流程上，且判据是"证据型"而非只判退出码。

    事实（2026-09-20 取证）：pre-push 钩子**早就存在**并会跑全量，但判据是
    ``rc != 0`` ⇒ ① 无法区分"测试失败"与"取证不完整/被截断"；
    ② 挡不住"通过数掉下基线"（漏跑看起来更干净）。本次把关卡接进钩子。
    """

    def test_hook_delegates_to_evidence_gate(self) -> None:
        hook = Path(__file__).resolve().parents[2] / "scripts" / "githooks" / "pre-push"
        assert hook.exists(), "受版本控制的 pre-push 不见了"
        src = hook.read_text(encoding="utf-8")
        assert "scripts/full_regression.py" in src, (
            "pre-push 未走证据型关卡——退回只判退出码的旧判据"
        )
        assert "--save-raw" in src, "pre-push 未保存原始输出 ⇒ 不可信时无法归因"
        assert "--baseline" in src, "pre-push 未接基线比对 ⇒ 通过数掉下基线不会被发现"
        assert "NOVELAGENT_SKIP_FULL" in src, "缺少显式跳过开关（跳过必须是显性行为）"
