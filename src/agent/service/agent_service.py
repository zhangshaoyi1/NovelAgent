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
from typing import Any, Optional

import os

from rich.console import Console

from agent.client.gateway_adapter import create_gateway
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
        use_gateway: 是否使用 llmagent Gateway 后端（默认 True）。
        use_session: 是否启用 Session 管理（默认 True）。
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
        use_gateway: bool = True,
        use_session: bool = True,
        use_catalog: bool = True,
        use_memory_bridge: bool = True,
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
        # 接线：LLM 调用事件 → <project>/.events/events.jsonl（复用公共接线，避免复制）
        from agent.core.event_sourcing.llm_wiring import wire_llm_event_hook

        wire_llm_event_hook(self.project_dir)

        # Session 管理（Phase 2）
        self.session_manager: Optional[Any] = None
        if use_session:
            self.session_manager = self._create_session_manager()

        # EventBus（Phase 2：use_gateway + use_session 同时启用时自动接线）
        self.event_bus: Optional[Any] = None
        if use_gateway and use_session:
            self.event_bus = self._create_event_bus()

        # Catalog 初始化（Phase 3）
        self.catalog_setup: Optional[Any] = None
        if use_catalog:
            self.catalog_setup = self._create_catalog_setup()

        # 环境变量标记：让所有 create_gateway() 直接创建也使用 Gateway 后端
        if use_gateway:
            os.environ.setdefault("LLM_USE_GATEWAY", "true")

        # 记忆桥接（Phase 4）
        self.memory_manager: Optional[Any] = None
        if use_memory_bridge:
            self.memory_manager = self._create_memory_bridge()

        self.traced_llm = TracedLLMClient(
            self._create_llm(), model=model
        )

    def _create_llm(self) -> Any:
        """创建原生 llmagent Gateway 实例"""
        return create_gateway()

    def _create_session_manager(self) -> Any:
        """创建原生 llmagent SessionManager"""
        from llmagent.kernel.session import SessionManager

        session_db = self.project_dir / ".state" / "session.db"
        session_db.parent.mkdir(parents=True, exist_ok=True)
        return SessionManager(str(session_db))

    def _create_event_bus(self) -> Any:
        """创建原生 llmagent EventBus

        当 use_gateway=True 且 use_session=True 时自动接线。
        """
        from llmagent.kernel.event_bus import EventBus

        return EventBus(
            db_path=str(self.project_dir / ".state" / "events.db")
        )

    def _create_catalog_setup(self) -> Any:
        """创建 TaskRegistry：将 WorkflowRegistry 注册到 llmagent Catalog

        当 use_catalog=True 时自动接线。
        """
        from llmagent.kernel.catalog import Catalog
        from agent.core.engine import WorkflowOrchestrator
        from agent.tasks import TaskRegistry

        catalog = Catalog()
        orchestrator = WorkflowOrchestrator(self.project_dir)
        registry = TaskRegistry(orchestrator, catalog)

        # 导入所有 workflow 触发装饰器注册
        import agent.workflows  # noqa: F401

        # 执行注册
        count = registry.register_all()
        self.console.log(f"[Catalog] 已注册 {count} 个工作流")
        return registry

    def _create_memory_bridge(self) -> Any:
        """创建原生 llmagent MemoryManager

        use_memory_bridge=True 时启用。
        """
        from agent.memory import create_memory_manager

        mem_db = self.project_dir / ".state" / "memory.db"
        mem_db.parent.mkdir(parents=True, exist_ok=True)
        return create_memory_manager(str(mem_db))

    # ---------------------------------------------------------------- 自主写作
    def build_pipeline(self, **pipeline_kwargs: Any) -> Any:
        """构造 AgenticPipelineWorkflow（服务层唯一构造入口）。

        CLI（autowrite 命令）与本类 ``run_autowrite`` 均经此处构造，避免双入口
        各自组装参数。默认注入 TracedLLMClient（端到端打点）与服务档位；
        调用方可用同名参数覆盖。其余构造参数经 ``pipeline_kwargs`` 透传。
        """
        from agent.workflows.pipeline.agentic_pipeline import AgenticPipelineWorkflow

        pipeline_kwargs.setdefault("project_dir", self.project_dir)
        pipeline_kwargs.setdefault("llm_client", self.traced_llm)
        pipeline_kwargs.setdefault("tier", self.tier)
        pipeline_kwargs.setdefault("console", self.console)
        return AgenticPipelineWorkflow(**pipeline_kwargs)

    def run_autowrite(
        self,
        brief: str = "",
        target_chapters: int | None = None,
        eval_enabled: bool = True,
        rollback_window: int = 5,
        max_rollback_attempts: int = 3,
        **pipeline_kwargs: Any,
    ) -> dict[str, Any]:
        """全流程自主写作。返回 {pipeline, llmops, result}（result 为原始结果对象）。"""
        pipeline = self.build_pipeline(
            brief=brief,
            target_chapters=target_chapters,
            eval_enabled=eval_enabled,
            rollback_window=rollback_window,
            max_rollback_attempts=max_rollback_attempts,
            **pipeline_kwargs,
        )
        result = pipeline.run()
        rd = result.to_dict()
        # 评测回归记录
        if rd.get("health_report"):
            self.eval_harness.record(rd["health_report"], tags={"mode": "autowrite"})
        return {"pipeline": rd, "llmops": self._llmops_summary(), "result": result}

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
        from agent.workflows.evaluation.m10_rollback import M10RollbackWorkflow

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
                from agent.workflows.writing.agentic_write import AgenticWriteWorkflow

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
