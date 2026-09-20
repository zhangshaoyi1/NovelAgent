"""LLMOps 可观测性红线：provider/model 维度 + trace 完整性（2026-09-20）

覆盖两件事：

A. **双记（同一物理调用被记两次）的检测口径**
   —— 熔断读 `totals()`；若读数被虚增，阈值就成了"摧毁扳机"（纪律 #16）。
   本组锁「检测正确 + **不静默改数**（熔断行为不得被悄悄改变）」。

B. **provider / model 维度 + 两个写入者的 meta 键契约一致**
   —— 补记路径曾只写 `cache_hit`/`cache_key`，而唯一收口无条件写 `provider`
   ⇒ 按 provider 分组的分析把补记 span 整体归入"缺失"且**无任何报错**。
"""

from __future__ import annotations

import ast
from pathlib import Path

from agent.core.llmops.trace import (
    DEDUPE_WINDOW_S,
    TraceSpan,
    TraceStore,
    find_duplicate_pairs,
)

SRC = Path(__file__).resolve().parents[1] / "src" / "agent"
TRACED = SRC / "core" / "llmops" / "traced_llm.py"
WIRING = SRC / "core" / "event_sourcing" / "llm_wiring.py"
COST_CLI = SRC / "cli" / "commands" / "cost.py"

#: 真实双记指纹（9 项目实测）：token 逐字相同、at 相差 ≈89ms
REAL_GAP_S = 0.089
T0 = 1789233469.3995488


def _span(at: float, tin: int, tout: int, use: str = "chat",
          provider: str | None = "openai", model: str = "auto", ok: bool = True) -> TraceSpan:
    meta: dict[str, object] = {"cache_hit": False, "cache_key": ""}
    if provider is not None:
        meta["provider"] = provider
    return TraceSpan(model=model, use=use, tokens_in=tin, tokens_out=tout,
                     latency_ms=10.0, ok=ok, at=at, meta=meta)


class TestDuplicateDetection:
    """A：双记检测口径。"""

    def test_r1_real_fingerprint_is_detected(self) -> None:
        """R1：真实指纹（token 相同 + at 差 89ms）必须被识别为一对。"""
        spans = [_span(T0, 4363, 3252, use="chat"),
                 _span(T0 + REAL_GAP_S, 4363, 3252, use="creative", provider=None,
                       model="creative-strong")]
        assert len(find_duplicate_pairs(spans)) == 1

    def test_r2_rounding_based_dedupe_would_miss_it(self) -> None:
        """R2：回归守卫 —— 旧口径 ``round(at, 1)`` 对本指纹**折叠不掉**。

        这是本组存在的理由：旧口径写在项目记忆里（"按 (in,out,round(at,1)) 去重"），
        实测在真实数据上失效（89ms 跨越 0.1s 取整边界）。
        """
        a, b = T0, T0 + REAL_GAP_S
        assert round(a, 1) != round(b, 1), (
            "若这两个时间戳取整后相同，本用例已失去鉴别力，需换真实样本"
        )
        assert abs(b - a) <= DEDUPE_WINDOW_S, "窗口须覆盖真实的包装层开销（≈89ms）"
        assert DEDUPE_WINDOW_S > 0.09, "窗口不得小于实测开销，否则检测失效"

    def test_r3_no_false_positive(self) -> None:
        """R3：token 不同 或 超出窗口 ⇒ 不得折叠（防把真实调用吃掉）。"""
        # token 不同
        assert find_duplicate_pairs([_span(T0, 100, 50), _span(T0 + 0.01, 200, 50)]) == []
        # 超出窗口（同一 token 但明显是两次独立调用）
        assert find_duplicate_pairs(
            [_span(T0, 100, 50), _span(T0 + DEDUPE_WINDOW_S + 1.0, 100, 50)]
        ) == []

    def test_r4_totals_is_not_silently_deduped(self, tmp_path: Path) -> None:
        """R4：**熔断读数行为不得被悄悄改变** —— totals() 仍按原始 span 求和。

        有意为之：静默去重会让「虚增」与「漏记」两种相反的失真同时不可见。
        正确姿态是**披露**（见 build_cost_summary.trace_integrity）。
        """
        ts = TraceStore(tmp_path)
        ts.record(_span(T0, 100, 50))
        ts.record(_span(T0 + REAL_GAP_S, 100, 50))
        assert ts.totals()["tokens_in"] == 200, "totals 被静默去重了 —— 改变了熔断行为"
        assert ts.totals()["calls"] == 2
        assert len(ts.duplicate_pairs()) == 1, "重复对必须可观测"

    def test_r5_accepts_raw_jsonl_rows(self) -> None:
        """R5：分析脚本常直接吃 jsonl 行 ⇒ 必须兼容 dict（不要求 TraceSpan 对象）。"""
        rows = [
            {"at": T0, "tokens_in": 10, "tokens_out": 5},
            {"at": T0 + REAL_GAP_S, "tokens_in": 10, "tokens_out": 5},
        ]
        assert len(find_duplicate_pairs(rows)) == 1


class TestProviderModelDimension:
    """B：分维度聚合 + 写入者契约一致。"""

    def test_r6_group_by_provider_and_model(self, tmp_path: Path) -> None:
        ts = TraceStore(tmp_path)
        ts.record(_span(T0, 100, 50, provider="openai", model="auto"))
        ts.record(_span(T0 + 1, 200, 60, provider="qwen", model="qwen-max"))
        ts.record(_span(T0 + 2, 10, 5, provider=None, model="creative-strong"))
        by_p = ts.by_provider()
        assert by_p["openai"]["tokens_total"] == 150
        assert by_p["qwen"]["tokens_total"] == 260
        assert by_p["<unknown>"]["calls"] == 1, "缺 provider 必须归入 <unknown>（不编造）"
        by_m = ts.by_model()
        assert set(by_m) == {"auto", "qwen-max", "creative-strong"}

    def test_r7_both_writers_share_the_same_meta_key_contract(self) -> None:
        """R7：两个写入者的 meta **键集必须一致**（纪律 #19 同族：跨模块契约核对）。

        唯一收口（`llm_wiring`）与补记路径（`traced_llm._span_meta`）都要写 provider。
        """
        wiring = WIRING.read_text(encoding="utf-8")
        assert '"provider": str(payload.get("provider", ""))' in wiring, (
            "唯一收口的 meta 未写 provider（契约的另一半）"
        )
        traced = TRACED.read_text(encoding="utf-8")
        assert "_span_meta(" in traced, "补记路径未使用统一的 meta 构造器"
        # 补记处不得再手写只含 cache_* 的 meta
        assert 'meta={"cache_hit": cache_hit, "cache_key": cache_key}' not in traced, (
            "补记路径又写回了缺 provider 的 meta ⇒ 两个写入者键集分叉"
        )

    def test_r8_span_meta_helper_emits_provider_key(self) -> None:
        """R8：`_span_meta` 必须**无条件**产出 provider 键（含失败路径）。"""
        from agent.core.llmops.traced_llm import _span_meta

        class _R:
            provider = "openai"
            model = "auto"

        assert _span_meta(_R(), True, "k")["provider"] == "openai"
        assert _span_meta(None, False, "")["provider"] == "", "失败路径应记空串，不编造"

    def test_r9_span_model_prefers_real_routed_model(self) -> None:
        from agent.core.llmops.traced_llm import _span_model

        class _Routed:
            model = "qwen-max"

        class _NoModel:
            model = ""

        assert _span_model(_Routed(), "creative-strong") == "qwen-max"
        assert _span_model(_NoModel(), "creative-strong") == "creative-strong"
        assert _span_model(None, "creative-strong") == "creative-strong"  # 显性回落


class TestDisclosureAndConsumption:
    """C：披露字段 + 真实消费者（防"建成未接线"）。"""

    def test_r10_cost_summary_discloses_integrity(self) -> None:
        """R10：成本汇总必须披露 trace 完整性（读数可信性），且降级路径为 None。"""
        from agent.core.llmops import cost as cost_mod

        src = Path(cost_mod.__file__).read_text(encoding="utf-8")
        assert "trace_integrity" in src
        assert "trustworthy" in src
        assert '"trustworthy": None' in src, (
            "降级路径把「不知道」写成了 True/False ⇒ 等于把无从判断读成可信（纪律 #1）"
        )
        assert "by_provider" in src and "by_model" in src

    def test_r11_cost_cli_consumes_the_new_dimensions(self) -> None:
        """R11：有真实消费者 —— CLI 看板必须呈现 provider/model 与完整性。"""
        src = COST_CLI.read_text(encoding="utf-8")
        assert "trace.by_provider()" in src
        assert "trace.by_model()" in src
        assert '"trace_by_provider"' in src and '"trace_by_model"' in src
        assert "trace.duplicate_pairs()" in src

    def test_r12_no_new_silent_degrade_in_touched_files(self) -> None:
        """R12：本次触碰的文件不得**新增**静默降级豁免（棘轮：只减不增）。

        不能断言"源码里不存在" —— ``trace.py`` 有一处**既有**豁免
        （``_load`` 的 JSON 解析失败重置）。故按**逐文件计数**做棘轮，
        这也是新增豁免唯一的机器可核形态。
        """
        baseline = {"trace.py": 1, "traced_llm.py": 0, "cost.py": 0,
                    "event_sourcing/llm_wiring.py": 2}
        files = [TRACED, SRC / "core" / "llmops" / "trace.py",
                 SRC / "core" / "llmops" / "cost.py", WIRING]
        for path in files:
            if path == COST_CLI:  # CLI 不参与棘轮（无豁免基线）
                continue
            n = path.read_text(encoding="utf-8").count("noqa: SILENT_DEGRADE")
            key = path.name if path.name != "llm_wiring.py" else "event_sourcing/llm_wiring.py"
            assert n <= baseline[key], (
                f"{key} 静默降级豁免 {baseline[key]} → {n}（棘轮只减不增）"
                f" —— 新增的 except 应走 degrade() 显性上报"
            )

    def test_r13_trace_module_has_no_heavy_imports(self) -> None:
        """R13：trace 是底层观测面，不得反向依赖上层（保导入环安全）。"""
        tree = ast.parse((SRC / "core" / "llmops" / "trace.py").read_text(encoding="utf-8"))
        bad = [
            n.module for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom) and n.module
            and (n.module.startswith("agent.workflows") or n.module.startswith("agent.cli"))
        ]
        assert bad == [], f"trace.py 反向依赖上层：{bad}"
