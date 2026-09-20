"""B1 红线：计数器/账本→动作链条的普查工具必须可用、可收敛、不静默失效。

纪律 #31：凡「读数驱动动作」的链条（预算/评分/计数 → 熔断/降档/回退），
须证①计数器只在成功后自增②失败不动或显性上报。本仓已发生两起（方向相反）：
token 虚增 100% ⇒ 假熔断；记账失败被吞 ⇒ 静默漏记。

本红线锁的是**普查工具本身**（不判对错，但必须：能识别真链条、不静默失效、
台账无僵尸、未复核数不回升）：
    ① 合成样本：计数器自增 + 阈值比较 ⇒ 判为候选（真阳性）；
    ② 无阈值比较 ⇒ 不判为候选（控噪）；
    ③ 台账无僵尸条目（复核过的链条必须仍扫得到）；
    ④ 真实 src 扫描的候选数 ≥ 当前基线（**防扫描器被削弱后"未复核数下降"的假收敛**）；
    ⑤ 未复核数不超过当前台账（棘轮只减不增）；
    ⑥ 脚本存在且真调用扫描逻辑（纪律 #7）。
"""

from __future__ import annotations

from pathlib import Path

from agent.core.infra import counter_action_audit as caa

SRC = Path(__file__).resolve().parents[2] / "src" / "agent"

#: ④ 真实扫描的候选数下界（2026-09-20 实测 11）。扫描器被削弱时本断言会失败。
CANDIDATE_FLOOR = 11
#: ⑤ 未复核数上限（2026-09-20 实测 8 = 11 候选 − 3 已复核）。
#: 只减不增：复核完一条就把它写进 KNOWN_CHAINS 并下调本值。
UNREVIEWED_CEILING = 8


def _scan_all() -> list[caa.Chain]:
    out: list[caa.Chain] = []
    for p in sorted(SRC.rglob("*.py")):
        rel = p.relative_to(SRC).as_posix()
        out.extend(caa.scan_chains(p.read_text(encoding="utf-8", errors="replace"), rel))
    return out


class TestScannerDetectsRealChains:
    def test_counter_plus_threshold_is_a_candidate(self) -> None:
        """①：自增 + 阈值比较 ⇒ 候选（这就是 #31 关注的形态）。"""
        src = (
            "def run(self):\n"
            "    self.budget_used += 1\n"
            "    if self.budget_used > self.token_limit:\n"
            "        return True\n"
            "    return False\n"
        )
        chains = caa.scan_chains(src, "x.py")
        assert [c.func for c in chains] == ["run"]

    def test_plain_calculation_is_not_a_candidate(self) -> None:
        """②：没有阈值比较的纯累加不产生候选（控噪）。"""
        src = "def total(xs):\n    total = 0\n    total += 1\n    return total\n"
        assert caa.scan_chains(src, "x.py") == []

    def test_class_method_key_shape(self) -> None:
        src = (
            "class A:\n"
            "    def bump(self):\n"
            "        self.retry_count += 1\n"
            "        if self.retry_count > self.max_retry:\n"
            "            pass\n"
        )
        chains = caa.scan_chains(src, "m.py")
        assert chains and chains[0].key == "m.py::A.bump"

    def test_syntax_error_is_tolerated(self) -> None:
        assert caa.scan_chains("def broken(:\n", "x.py") == []


class TestTriageLedger:
    def test_no_zombie_entries(self) -> None:
        """③：台账条目必须仍能被扫到（否则是过期结论）。"""
        keys = {c.key for c in _scan_all()}
        zombies = sorted(k for k in caa.KNOWN_CHAINS if k not in keys)
        assert not zombies, f"台账僵尸条目（已扫不到）：{zombies}"

    def test_candidate_floor_not_weakened(self) -> None:
        """④：候选数不得低于基线——防'扫描器坏了 ⇒ 未复核数归零'的假收敛。"""
        n = len(_scan_all())
        assert n >= CANDIDATE_FLOOR, (
            f"候选数 {n} < 基线 {CANDIDATE_FLOOR}：扫描器可能被削弱（假收敛）"
        )

    def test_unreviewed_ratchet_only_decreases(self) -> None:
        """⑤：未复核数只减不增（复核完请把结论写进 KNOWN_CHAINS 并下调上限）。"""
        keys = {c.key for c in _scan_all()}
        unreviewed = sorted(k for k in keys if k not in caa.KNOWN_CHAINS)
        assert len(unreviewed) <= UNREVIEWED_CEILING, (
            f"未复核候选回升：{len(unreviewed)} > {UNREVIEWED_CEILING}\n"
            + "\n".join(f"  · {k}" for k in unreviewed)
        )

    def test_historical_incidents_documented(self) -> None:
        """两起真实事故必须留档（后来人先看方向相反的两种故障形态）。"""
        assert len(caa.HISTORICAL_INCIDENTS) >= 2
        assert any("traced_llm" in k for k in caa.HISTORICAL_INCIDENTS)
        assert any("llm_usage" in k for k in caa.HISTORICAL_INCIDENTS)


class TestToolIsWired:
    def test_script_calls_scanner(self) -> None:
        """⑥（纪律 #7）：普查脚本必须真调用扫描逻辑。"""
        script = SRC.parents[1] / "scripts" / "audit_counter_action_chains.py"
        assert script.exists(), "普查脚本不存在"
        text = script.read_text(encoding="utf-8")
        assert "scan_chains(" in text and "KNOWN_CHAINS" in text
