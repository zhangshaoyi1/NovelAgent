"""AgenticPipelineWorkflow 拆分的 Mixin（机械搬移，行为零改动）。

拆分背景：单文件 1600+ 行不利维护（对齐 m5 Mixin 拆分模式，架构评审 P2-9）。
主文件保留类定义、``__init__`` 与 ``run()``；本文件（P1-2 成本可观测 + 单步超时 + 档位降级）承载对应方法组。
仅供 ``AgenticPipelineWorkflow`` 继承组合，不要单独使用。
"""

from __future__ import annotations

from typing import Any

from agent.workflows.pipeline.agentic_pipeline_types import _DOWNGRADE_ORDER

class _PipelineCostMixin:
    # ---------------------------------------------------------------- P1-2 成本可观测 + 单步超时
    def _traced_llm(self) -> Any:
        """返回包着 ``self.llm`` 的 ``TracedLLMClient``（注入同 tracer，供 M1~M4 调用）。"""
        if self._traced_llm_cache is None:
            from agent.core.llmops import TraceStore, TracedLLMClient, set_tracer

            try:
                set_tracer(TraceStore(self.project_dir))
            except Exception:  # noqa: BLE001
                pass
            self._traced_llm_cache = TracedLLMClient(self.llm, model="creative-strong")
        return self._traced_llm_cache

    def _alert_cost(self, step: str) -> None:
        """成本告警（仅提示不拦截，拍板 #3；硬熔断归 G4）。"""
        try:
            from agent.core.llmops.cost import CostModel
            from agent.core.llmops.trace import get_tracer

            tracer = get_tracer()
            totals = tracer.totals()
            used = totals.get("tokens_total", 0)
            model = CostModel()
            msg = model.alert_if_over(used, "balanced", self._resolve_target())
            if msg:
                self.console.print(f"[yellow]{msg}（步骤：{step}）[/yellow]")
        except Exception:  # noqa: BLE001
            pass

    def _check_budget(self, step: str) -> bool:
        """检查预算/墙钟是否超限。

        G4 熔断检查：从 G3 _alert_cost 升级，复用 CostModel.baseline_tokens + get_tracer().totals()。

        Args:
            step: 检查点名称（用于日志）。

        Returns:
            True 表示超限，应熔断中止；False 表示预算内。
        """
        import time

        try:
            from agent.core.llmops.cost import CostModel
            from agent.core.llmops.trace import get_tracer

            tracer = get_tracer()
            totals = tracer.totals()
            used_tokens = totals.get("tokens_total", 0)

            # 1) Token 检查
            model = CostModel()
            _, token_limit = model.baseline_tokens(self._cost_tier, self._resolve_target())
            token_limit *= self._budget_margin

            if used_tokens > token_limit:
                self.console.print(
                    f"[red]✗ Token 预算超限熔断（{used_tokens/1_000_000:.2f}M > {token_limit/1_000_000:.2f}M）"
                    f"（步骤：{step}）[/red]"
                )
                return True

            # 2) 墙钟检查
            if self._max_time and self._start_time > 0:
                elapsed = time.monotonic() - self._start_time
                if elapsed > self._max_time:
                    self.console.print(
                        f"[red]✗ 墙钟超时熔断（{elapsed:.0f}s > {self._max_time}s）"
                        f"（步骤：{step}）[/red]"
                    )
                    return True

        except Exception:  # noqa: BLE001
            # 检查失败不阻断（避免熔断本身异常）
            pass

        return False

    # ================================================================
    # G10：写中成本视图 + 超预算自动降档（拍板 2/3/4，只增不改）
    # ================================================================
    def _current_cost_fields(self) -> dict[str, Any]:
        """G10（拍板 2）：当前成本视图（tokens_used/budget/remaining）。

        数据源与 _check_budget **同源**（不新造统计）：used = get_tracer().totals()["tokens_total"]；
        budget = baseline_tokens(tier, target)[1] * budget_margin；
        remaining = budget - used（保留原始差值，可为负；渲染层钳制 ≥0）。
        全 try/except：任何异常返回 {}（事件不带成本字段，不阻断发射）。
        """
        try:
            from agent.core.llmops.cost import CostModel
            from agent.core.llmops.trace import get_tracer

            tracer = get_tracer()
            used = float((tracer.totals().get("tokens_total", 0) or 0))
            model = CostModel()
            _, token_limit = model.baseline_tokens(self._cost_tier, self._resolve_target())
            budget = token_limit * self._budget_margin
            return {
                "tokens_used": used,
                "tokens_budget": budget,
                "tokens_remaining": budget - used,
            }
        except Exception:  # noqa: BLE001 - 成本视图失败降级 {}，不阻断
            return {}

    def _maybe_downgrade_tier(self) -> bool:
        """G10（拍板 3/4）：超预算降档判定（纯确定性，全 try/except 降级不阻断）。

        Returns:
            True  = 本次检查点**已降档** → 外层继续写章（不熔断）
            False = 不应/不能降档 → 外层走既有 G4 熔断（tripped=True + break）

        返回语义是零回归关键：仅「已降档」返回 True；其余（token 预算内 / 最低档 /
        未知档位 / 墙钟超时 / auto 关 / 异常）一律 False → 保证 test_breaker_*
        （monkeypatch _check_budget=True 模拟墙钟超时）仍走 G4 熔断。
        """
        try:
            if not self._auto_downgrade:
                return False  # --no-auto-downgrade：G4 行为
            from agent.core.llmops.cost import CostModel
            from agent.core.llmops.trace import get_tracer

            used = float(get_tracer().totals().get("tokens_total", 0) or 0)
            model = CostModel()
            _, token_limit = model.baseline_tokens(self._cost_tier, self._resolve_target())
            token_limit *= self._budget_margin
            if used <= token_limit:
                return False  # token 预算内 → 超限来自墙钟 → 熔断
            if self._cost_tier not in _DOWNGRADE_ORDER:
                return False  # 未知档位 → 熔断（保守）
            idx = _DOWNGRADE_ORDER.index(self._cost_tier)
            if idx >= len(_DOWNGRADE_ORDER) - 1:
                return False  # 最低档仍超限 → 既有 G4 熔断
            old_tier = self._cost_tier
            new_tier = _DOWNGRADE_ORDER[idx + 1]
            self._cost_tier = new_tier  # 仅改预算档位，writer tier 不动（拍板 3）
            self._emit_event(
                "cost_downgrade",
                from_tier=old_tier,
                to_tier=new_tier,
                reason=f"Token 预算超限：{used / 1_000_000:.2f}M > {token_limit / 1_000_000:.2f}M",
            )
            self.console.print(
                f"[yellow]已自动降档至 {new_tier}，后续按新预算续跑（此前 {old_tier}）[/yellow]"
            )
            return True  # 已降档 → 继续写章
        except Exception:  # noqa: BLE001 - 降档判定异常 → 保守走 G4 熔断，不静默超支
            return False

