"""llmagent 组装入口：将七统一门面 + Task 运行时装配为可运行实例"""

from __future__ import annotations

from pathlib import Path

from llmagent.gateway.chat import Gateway
from llmagent.gateway.packer import Packer
from llmagent.gateway.providers.registry import ProviderRegistry
from llmagent.gateway.rate_limiter import RateLimiter, SemanticCache
from llmagent.gateway.request_gate import RequestGate
from llmagent.gateway.response_gate import MetricsSink, ResponseGate
from llmagent.gateway.router import ComplexityRouter
from llmagent.kernel.artifact import ArtifactStore, RetentionPolicy
from llmagent.kernel.catalog import Catalog
from llmagent.kernel.checkpoint import CheckpointManager
from llmagent.kernel.event_bus import EventBus
from llmagent.kernel.failure import FailureHandler
from llmagent.kernel.metrics import MetricRegistry, Metrics, SpanBuilder, Tagger
from llmagent.kernel.monitor import Monitor
from llmagent.kernel.validator import ValidatorRunner


class LLMApp:
    """llmagent 应用实例：组装所有门面"""

    def __init__(self, data_dir: str | Path | None = None) -> None:
        self.data_dir = Path(data_dir) if data_dir else Path.cwd() / ".llmagent"
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Gateway（M1 完整版）
        self.gateway = Gateway(
            request_gate=RequestGate(),
            router=ComplexityRouter(),
            packer=Packer(),
            registry=ProviderRegistry(),
            response_gate=ResponseGate(),
            metrics_sink=MetricsSink(),
            rate_limiter=RateLimiter(),
            semantic_cache=SemanticCache(),
        )

        # 回溯（M1 完整版）
        self.event_bus = EventBus(str(self.data_dir / "events.db"))
        self.artifact_store = ArtifactStore(
            str(self.data_dir / "artifacts.db"),
            retention_policy=RetentionPolicy(ttl_days=30, max_count=5000),
        )
        self.checkpoint_manager = CheckpointManager(str(self.data_dir / "checkpoints.db"))

        # 监控
        self.monitor = Monitor()

        # 打点（M1 完整版）
        self.metric_registry = MetricRegistry(str(self.data_dir / "metrics.db"))
        self.metrics = Metrics(
            span_builder=SpanBuilder(),
            metric_registry=self.metric_registry,
            tagger=Tagger(),
        )

        # 校验
        self.validator = ValidatorRunner()

        # 失败处理
        self.failure_handler = FailureHandler()

        # 管理
        self.catalog = Catalog()

    def close(self) -> None:
        self.event_bus.close()
        self.artifact_store.close()
        self.checkpoint_manager.close()
        self.metric_registry.close()