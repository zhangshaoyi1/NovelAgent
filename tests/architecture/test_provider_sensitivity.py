"""provider 敏感判据登记表 + 抽检探针红线（2026-09-20）

锁三件事：

A. 登记表的**完整性**（风险面不得漏登记；僵尸条目必须清）
B. 探针的**结论纪律**：provider 数 < 2 时**禁止**给出任何"稳健/不稳健"结论
   —— 单 provider 数据上的跨 provider 结论是假结论，比"未验证"更坏
C. 探针脚本必须**真消费**登记表（防"建成未接线"，纪律 #7）
"""

from __future__ import annotations

import ast
from pathlib import Path

from agent.core.quality import provider_sensitivity as ps

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "agent"
SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "provider_judge_probe.py"

#: **必须**出现在登记表里的 LLM 分值判据（已知且已收口为单一真源）
REQUIRED_RISK_KEYS = {
    "golden_three_total", "golden_three_floor",
    "eval_coherence_min", "eval_readability_min",
}


def _span(prov: str | None, tin: int, tout: int) -> dict:
    meta = {} if prov is None else {"provider": prov}
    return {"tokens_in": tin, "tokens_out": tout, "meta": meta}


class TestRegistry:
    """A：登记表完整性。"""

    def test_r1_no_zombie_locations(self) -> None:
        """R1：每条登记指向的 ``路径::符号`` 必须真实存在（僵尸检查）。"""
        bad = []
        for j in ps.JUDGES:
            ok, why = ps.registry_path_valid(j.location, SRC_ROOT)
            if not ok:
                bad.append(f"{j.key}: {why}")
        assert bad == [], "登记表存在僵尸条目（符号已改名/删除 ⇒ 登记未同步）：\n  " + "\n  ".join(bad)

    def test_r2_keys_are_unique(self) -> None:
        keys = [j.key for j in ps.JUDGES]
        assert len(keys) == len(set(keys)), f"key 重复：{keys}"

    def test_r3_known_llm_score_judges_are_registered(self) -> None:
        """R3：已知的 LLM 分值判据**必须**登记（防漏登记 ⇒ 敏感面被低估）。"""
        got = {j.key for j in ps.llm_absolute_judges()}
        missing = REQUIRED_RISK_KEYS - got
        assert missing == set(), f"以下 LLM 分值判据未登记为敏感面：{missing}"

    def test_r4_rule_judges_are_not_flagged_sensitive(self) -> None:
        """R4：纯规则判据不得被标 provider 敏感（否则风险面被稀释）。"""
        bad = [j.key for j in ps.JUDGES
               if j.source in (ps.SOURCE_RULE, ps.SOURCE_RULE_RELATIVE) and j.provider_sensitive]
        assert bad == [], f"纯规则判据被误标为 provider 敏感：{bad}"

    def test_r5_relative_judges_explain_the_mechanism(self) -> None:
        """R5：标 `relative=True` 的必须说明相对化机制（防"自封相对化"）。"""
        for j in ps.relative_judges():
            assert "相对化" in j.evidence or "分位" in j.evidence or "decile" in j.evidence, (
                f"{j.key} 标了 relative 但 evidence 未说明机制：{j.evidence[:60]}"
            )

    def test_r6_every_judge_cites_evidence(self) -> None:
        for j in ps.JUDGES:
            assert len(j.evidence) >= 20, f"{j.key} 缺判定依据（纪律 #23：结论须绑取证）"


class TestProbeConclusionDiscipline:
    """B：探针的结论纪律（本节是本文件的核心）。"""

    def test_r7_single_provider_is_not_comparable(self) -> None:
        """R7：**单 provider 不得给出位移数值或"稳健"结论**。"""
        rep = ps.probe_provider_shift([_span("openai", 1000, 500), _span("openai", 1200, 600)])
        assert rep["comparable"] is False
        assert rep["token_p50_ratio"] is None, "单 provider 却给出了位移比值 ⇒ 假结论"
        assert "无法比较" in rep["verdict"]
        assert "未验证" in rep["verdict"]
        assert "稳健" not in rep["verdict"] or "不得读作" in rep["verdict"]
        # 分值类判据同样必须显式声明不可比较
        assert rep["score_judges_comparable"] is False
        assert rep["score_judges_blocker"]

    def test_r8_two_providers_yield_a_ratio(self) -> None:
        """R8：两个 provider 时才给出比值（且仅覆盖读数驱动类）。"""
        rep = ps.probe_provider_shift([
            _span("openai", 1000, 500), _span("openai", 1000, 500),
            _span("qwen", 2000, 900), _span("qwen", 2000, 900),
        ])
        assert rep["comparable"] is True
        assert set(rep["providers"]) == {"openai", "qwen"}
        assert rep["token_p50_ratio"]["qwen/openai"] == 2.0
        assert rep["score_judges_comparable"] is False, (
            "分值类判据在任何情况下都不可比较（审计记录缺 provider）"
        )

    def test_r9_unknown_bucket_is_not_a_provider(self) -> None:
        """R9：``<unknown>`` 不是 provider —— 不能靠它凑出"两个 provider"。"""
        rep = ps.probe_provider_shift([_span("openai", 100, 50), _span(None, 100, 50)])
        assert rep["comparable"] is False, "<unknown> 被当成了一个 provider（假可比较）"
        assert rep["unknown_bucket_calls"] == 1
        assert rep["by_provider"]["<unknown>"]["calls"] == 1

    def test_r9b_all_zero_token_provider_is_not_comparable(self) -> None:
        """R9b：**中位调用无 token 使用**的 provider 桶不得计入可比较性（实测教训）。

        2026-09-20 实测：trace 里有 ``provider="fake"`` 的 32 条调用，``p50=0``
        但 ``p90=10``（偶有非零）。若用"全为 0"判定会**漏过它**，把单 provider
        数据读成"2 个 provider 可比较" ⇒ **假结论**。故判据取 **p50**（中位）：
        真实 LLM 调用不可能一半以上不返回 usage。
        """
        rep = ps.probe_provider_shift([
            _span("openai", 1000, 500),
            # stub：多数 0，偶有非零（p50=0 但 max=10）
            *[_span("fake", 0, 0) for _ in range(32)],
            _span("fake", 10, 0),
        ])
        assert rep["comparable"] is False, "中位无读数的 stub provider 被当成了真 provider"
        assert rep["providers_without_reading"] == ["fake"]
        assert rep["token_p50_ratio"] is None
        assert "无法比较" in rep["verdict"] and "无有效读数" in rep["verdict"]

    def test_r9c_ratio_only_for_effective_providers(self) -> None:
        """R9c：两个**有效** provider + 一个 stub ⇒ 仍可比较，且 stub 不参与。"""
        rep = ps.probe_provider_shift([
            _span("openai", 1000, 500), _span("qwen", 2000, 900),
            *[_span("fake", 0, 0) for _ in range(5)],
        ])
        assert rep["comparable"] is True
        assert set(rep["providers"]) == {"openai", "qwen"}
        assert rep["providers_without_reading"] == ["fake"]

    def test_r10_empty_input_does_not_crash_or_invent(self) -> None:
        rep = ps.probe_provider_shift([])
        assert rep["comparable"] is False and rep["providers"] == []
        assert rep["token_p50_ratio"] is None


class TestWiring:
    """C：真消费 + 不新增静默豁免。"""

    def test_r11_script_consumes_the_registry(self) -> None:
        """R11：脚本必须真调用登记表与探针（防"建成未接线"）。"""
        src = SCRIPT.read_text(encoding="utf-8")
        for need in ("probe_provider_shift", "llm_absolute_judges",
                     "registry_path_valid", "by_source"):
            assert need in src, f"探针脚本未消费 {need}"
        assert "REPO_ROOT" in src and "sys.path.insert" in src, "脚本必须能独立运行"

    def test_r12_no_new_silent_degrade(self) -> None:
        for path in (SRC_ROOT / "core" / "quality" / "provider_sensitivity.py", SCRIPT):
            n = path.read_text(encoding="utf-8").count("noqa: SILENT_DEGRADE")
            assert n == 0, f"{path.name} 引入静默降级豁免 {n} 处"

    def test_r13_registry_module_is_dependency_free(self) -> None:
        """R13：登记表是纯数据+纯函数，不得依赖上层（否则探针会被拖进重依赖）。"""
        tree = ast.parse((SRC_ROOT / "core" / "quality" / "provider_sensitivity.py").read_text(encoding="utf-8"))
        bad = [
            n.module for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom) and n.module
            and (n.module.startswith("agent.workflows") or n.module.startswith("agent.cli"))
        ]
        assert bad == [], f"provider_sensitivity 反向依赖上层：{bad}"
