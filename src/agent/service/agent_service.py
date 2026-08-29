"""Service 层（Phase 3 · 接口预留，Web UI 本期不做）

把「全流程自主写作」与「不崩体检」封装为一个**进程内、框架无关**的服务接口
``AgentService``，供 CLI 命令与（未来的）FastAPI / WebSocket 层共用。

设计对应设计文档 §2.1 Service/UX 层与 §3：本期**只做接口与可观测接线**，不引入
Web 框架、不实现前端（产品决策：Web UI 暂缓）。要接入 FastAPI，只需把本类的
``run_autowrite`` / ``run_evaluate`` / ``summarize`` 方法挂载到路由即可——接口已就绪。

可观测接线：Service 会把全局 Tracer 指向本项目 ``TraceStore``，并用
``TracedLLMClient`` 包装 LLM 注入 Pipeline，从而端到端记录每次 LLM 调用
（模型/用途/token/成本/延迟/成败），并落地成本基线告警、提示版本、评测回归。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.console import Console

from agent.client import LLMClient
from agent.core.llmops import (
    CostModel,
    EvalHarness,
    PromptRegistry,
    TraceStore,
    TracedLLMClient,
    get_tracer,
    set_tracer,
)
from agent.client import ModelRouter
from agent.core.tools.mcp_bridge import MCPBridge
from agent.core.quality.guardrails import build_guardrails


class AgentService:
    """自主写作服务接口（进程内）。

    Args:
        project_dir: 小说项目目录。
        tier: 写章引擎档位。
        model: 记录用模型名（TracedLLMClient）。
        cost_model: 成本模型（可注入，默认占位单价）。
        console: rich 控制台。
    """

    def __init__(
        self,
        project_dir: str | Path,
        tier: str = "auto",
        model: str = "creative-strong",
        cost_model: CostModel | None = None,
        model_router: ModelRouter | None = None,
        mcp_bridge: MCPBridge | None = None,
        console: Console | None = None,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.tier = tier
        self.model = model
        self.console = console or Console()

        # LLMOps 组件（均按项目持久化到 .state/llmops/）
        self.trace_store = TraceStore(self.project_dir)
        self.cost_model = cost_model or CostModel()
        self.prompt_registry = PromptRegistry(self.project_dir)
        self.eval_harness = EvalHarness(self.project_dir)

        # Phase 4 · 生态与强化（可选注入；默认按项目配置构造）
        self.model_router = model_router or ModelRouter()
        self.mcp_bridge = mcp_bridge or MCPBridge(
            config_path=self.project_dir / ".state" / "mcp.json"
        )
        # 探测 MCP 服务器可用性（配置为空时瞬时返回，真实服务器不可达则优雅降级）
        try:
            self.mcp_bridge.discover()
        except Exception:  # noqa: BLE001
            pass

        # 接线：全局 Tracer 指向本项目 TraceStore
        set_tracer(self.trace_store)
        self.traced_llm = TracedLLMClient(LLMClient(), model=model)

    # ---------------------------------------------------------------- 自主写作
    def run_autowrite(
        self,
        brief: str = "",
        target_chapters: int | None = None,
        eval_enabled: bool = True,
        rollback_window: int = 5,
        max_rollback_attempts: int = 3,
    ) -> dict[str, Any]:
        """全流程自主写作。返回 {pipeline, llmops}。"""
        from agent.workflows.agentic_pipeline import AgenticPipelineWorkflow

        pipeline = AgenticPipelineWorkflow(
            project_dir=self.project_dir,
            llm_client=self.traced_llm,
            tier=self.tier,
            brief=brief,
            target_chapters=target_chapters,
            eval_enabled=eval_enabled,
            rollback_window=rollback_window,
            max_rollback_attempts=max_rollback_attempts,
            console=self.console,
        )
        result = pipeline.run()
        rd = result.to_dict()
        # 评测回归记录
        if rd.get("health_report"):
            self.eval_harness.record(rd["health_report"], tags={"mode": "autowrite"})
        return {"pipeline": rd, "llmops": self._llmops_summary()}

    # ---------------------------------------------------------------- 体检
    def run_evaluate(
        self,
        no_rollback: bool = False,
        auto_repair: bool = False,
        rollback_window: int = 5,
        max_rollback_attempts: int = 3,
        real_score: bool = True,
    ) -> dict[str, Any]:
        """全书「不崩」体检。返回 {report, llmops}。

        ``real_score``（默认 True）：真 LLM 评分（B1），把 Evaluator 的人设/设定/连贯/追读/逻辑
        维度由 LLM 实判，替代离线时的满分安全默认。传 False 可强制离线（CI/测试）。
        LLM 不可用时自动降级为离线安全默认。
        """
        from agent.agents.evaluator import EvaluatorAgent
        from agent.core.quality.scoring.reader_appeal import ReaderAppealScorer

        score_fn = None
        if real_score:
            score_fn = ReaderAppealScorer(llm_client=self.traced_llm).score

        # D-J：service 侧（高于 workflows）构造并注入回退能力，agents 不再直接 import workflows
        from agent.workflows.m10_rollback import M10RollbackWorkflow

        evaluator = EvaluatorAgent(
            self.project_dir,
            auto_rollback=not no_rollback,
            rollback_window=rollback_window,
            max_rollback_attempts=max_rollback_attempts,
            score_fn=score_fn,
            rollback_provider=M10RollbackWorkflow(self.project_dir),
        )
        if auto_repair:

            def rewriter(nums: list[int]) -> None:
                from agent.workflows.agentic_write import AgenticWriteWorkflow

                w = AgenticWriteWorkflow(project_dir=self.project_dir, console=self.console)
                for _ in nums:
                    w.run()

            report = evaluator.evaluate_with_repair(rewriter)
        else:
            report = evaluator.evaluate()
        rd = report.to_dict()
        self.eval_harness.record(rd, tags={"mode": "evaluate"})
        return {"report": rd, "llmops": self._llmops_summary()}

    # ---------------------------------------------------------------- 看板
    def _llmops_summary(self) -> dict[str, Any]:
        totals = self.trace_store.totals()
        by_use = self.trace_store.by_use()
        alert = self.cost_model.alert_if_over(
            totals["tokens_total"], "balanced", 300
        )
        return {
            "trace_totals": totals,
            "trace_by_use": by_use,
            "cost_alert": alert,
            "eval_runs": len(self.eval_harness.history()),
            "prompt_versions": self.prompt_registry.all(),
            "model_routing": self.model_router.report(),
            "mcp": {
                "servers": self.mcp_bridge.servers,
                "local_tools": len(self.mcp_bridge.local_manifest()),
                "remote_tools": len(self.mcp_bridge.remote_manifest()),
            },
        }

    def summarize(self) -> dict[str, Any]:
        """返回当前可观测快照（CLI 成本看板用）。"""
        return self._llmops_summary()

    def register_prompt(self, key: str, text: str) -> dict[str, Any]:
        """登记/校验提示版本（漂移检测用）。"""
        return self.prompt_registry.register(key, text)

    # ---------------------------------------------------------------- A3 反馈改写
    def rewrite_chapter(
        self,
        chapter_num: int,
        feedback: str,
        *,
        backup: bool = True,
        gate_mode: str = "advisory",
        record_learning: bool = True,
    ) -> dict[str, Any]:
        """A3 反馈→定向重写（用户好用闭环）。

        把用户针对某章的反馈变成局部定向重写，而非整章回退/重跑。
        返回 RewriteResult.to_dict()。
        """
        from agent.core.quality.rewrite.feedback_rewriter import FeedbackRewriter

        rewriter = FeedbackRewriter(
            project_dir=self.project_dir,
            llm_client=self.traced_llm,
            guardrails=build_guardrails(),
            console=self.console,
        )
        result = rewriter.rewrite(
            chapter_num,
            feedback,
            backup=backup,
            gate_mode=gate_mode,
            record_learning=record_learning,
        )
        return result.to_dict()
