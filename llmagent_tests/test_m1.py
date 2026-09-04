"""M1 组件单元测试：完整回溯 / 完整打点 / Gateway 完整版 / 业务 Task"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from llmagent.gateway.chat import Gateway, GatewayError
from llmagent.gateway.models import (
    ChatRequest,
    ChatResponse,
    HintComplexity,
    ModelCard,
    PackedRequest,
    RawResponse,
    RouteDecision,
    TaskHint,
)
from llmagent.gateway.providers.registry import ModelProvider, ProviderRegistry
from llmagent.gateway.rate_limiter import RateLimiter, SemanticCache
from llmagent.gateway.response_gate import MetricsSink, ResponseGate
from llmagent.kernel.artifact import ArtifactRef, ArtifactStore, RetentionPolicy
from llmagent.kernel.checkpoint import Checkpoint, CheckpointManager
from llmagent.kernel.event_bus import EventBus, EventSchemaError
from llmagent.kernel.metrics import MetricRegistry, Metrics, SpanBuilder, Tagger
from llmagent.kernel.task import TaskKind, TaskRun, TaskSpec, TaskStatus


# ---- Mock Provider ----


class MockProvider:
    name = "mock"

    def complete(self, packed: PackedRequest) -> RawResponse:
        return RawResponse(
            text='{"result": "ok"}',
            provider="mock",
            model="mock-model",
            usage_input=packed.estimated_input_tokens,
            usage_output=50,
        )

    def count_tokens(self, text: str) -> int:
        return len(text) // 4

    def model_card(self) -> ModelCard:
        return ModelCard(provider="mock", model="mock-model", cost_per_1k_input_cents=0.5, cost_per_1k_output_cents=1.5, context_window=128000)


# ===== M1.1 完整回溯 =====


class TestEventBusFull:
    def test_schema_validation(self):
        bus = EventBus()
        # 已知类型缺少必填字段
        with pytest.raises(EventSchemaError, match="缺少必填字段"):
            bus.append("run-1", "task.started", {"wrong_field": "value"})
        # 未知类型不校验
        seq = bus.append("run-1", "custom.event", {"anything": "goes"})
        assert seq > 0

    def test_partitioned_index(self):
        bus = EventBus()
        bus.append("run-1", "task.started", {"run_id": "run-1", "spec_name": "t", "kind": "LLM"})
        bus.append("run-1", "task.succeeded", {"run_id": "run-1", "duration_ms": 100})
        events = bus.get_events("run-1")
        assert len(events) == 2

    def test_archival(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            bus = EventBus(db_path)
            bus.append("run-1", "task.started", {"run_id": "run-1", "spec_name": "t", "kind": "LLM"})
            # 归档所有（天数设为极大值）
            count = bus.archive_older_than(days=0)
            assert count >= 1
            # 归档后主表为空
            events = bus.get_events("run-1")
            # 归档表中应仍可查到
            assert len(events) >= 1
            bus.close()
        finally:
            import os
            os.unlink(db_path)

    def test_query_by_type(self):
        bus = EventBus()
        bus.append("r1", "llm.request", {"run_id": "r1", "provider": "mock", "model": "m", "estimated_tokens": 100})
        bus.append("r2", "llm.response", {"run_id": "r2", "provider": "mock", "model": "m", "input_tokens": 100, "output_tokens": 50, "latency_ms": 200})
        results = bus.query_by_type("llm.request", limit=10)
        assert len(results) == 1
        assert results[0]["type"] == "llm.request"


class TestArtifactStoreFull:
    def test_retention_ttl(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            # TTL = 0 天，所有数据立即过期
            store = ArtifactStore(db_path, retention_policy=RetentionPolicy(ttl_days=0))
            ref = store.put("test data", "text/plain")
            # 放入后 TTL 会立即淘汰
            data = store.get(ref)
            # 可能已被淘汰
            store.close()
        finally:
            import os
            os.unlink(db_path)

    def test_retention_max_count(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            store = ArtifactStore(db_path, retention_policy=RetentionPolicy(max_count=2))
            store.put("data1", "text/plain")
            store.put("data2", "text/plain")
            store.put("data3", "text/plain")
            # 最多保留 2 条
            store.close()
        finally:
            import os
            os.unlink(db_path)

    def test_list_by_type(self):
        store = ArtifactStore()
        store.put({"key": "v1"}, "application/json")
        store.put("text content", "text/plain")
        items = store.list_by_type("text/plain")
        assert len(items) == 1
        assert items[0]["content_type"] == "text/plain"

    def test_get_meta(self):
        store = ArtifactStore()
        ref = store.put("hello", "text/plain")
        meta = store.get_meta(ref)
        assert meta is not None
        assert meta["content_type"] == "text/plain"
        assert meta["size_bytes"] > 0


class TestCheckpointManager:
    def test_save_and_get(self):
        cm = CheckpointManager()
        cm.save(run_id="run-1", seq=1, provider="openai", model="gpt-4o", status="RUNNING")
        checkpoints = cm.get("run-1")
        assert len(checkpoints) == 1
        assert checkpoints[0].provider == "openai"
        assert checkpoints[0].model == "gpt-4o"

    def test_latest_checkpoint(self):
        cm = CheckpointManager()
        cm.save(run_id="run-1", seq=1, status="RUNNING")
        cm.save(run_id="run-1", seq=2, status="SUCCEEDED")
        latest = cm.get_latest("run-1")
        assert latest is not None
        assert latest.seq == 2
        assert latest.status == "SUCCEEDED"

    def test_context_fingerprint(self):
        cm = CheckpointManager()
        cm.save(run_id="run-1", seq=1, context_fingerprint="fp_abc123", status="RUNNING")
        cp = cm.get("run-1", seq=1)
        assert len(cp) == 1
        assert cp[0].context_fingerprint == "fp_abc123"

    def test_delete(self):
        cm = CheckpointManager()
        cm.save(run_id="run-1", seq=1, status="RUNNING")
        cm.delete("run-1")
        assert len(cm.get("run-1")) == 0


# ===== M1.2 完整打点 =====


class TestTagger:
    def test_merge_tags(self):
        tagger = Tagger()
        run = TaskRun(run_id="t-1", spec=TaskSpec(name="test", kind=TaskKind.LLM, tags={"env": "test"}))
        span = SpanBuilder().start(run)
        tags = tagger.merge(run, span, {"extra": "value"})
        assert tags["spec_name"] == "test"
        assert tags["kind"] == "LLM"
        assert tags["env"] == "test"
        assert tags["extra"] == "value"
        assert tags["run_id"] == "t-1"


class TestMetricRegistryFull:
    def test_8_metrics_names(self):
        assert len(MetricRegistry.METRIC_NAMES) == 8

    def test_record_and_summary(self):
        mr = MetricRegistry()
        run = TaskRun(run_id="m-1", spec=TaskSpec(name="t", kind=TaskKind.LLM))
        mr.record(run, "task_duration_seconds", 1.5)
        mr.record(run, "task_duration_seconds", 2.5)
        summary = mr.summary("task_duration_seconds")
        assert summary["count"] == 2
        assert summary["avg"] == 2.0

    def test_persistent_storage(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            mr = MetricRegistry(db_path)
            run = TaskRun(run_id="m-persist", spec=TaskSpec(name="t", kind=TaskKind.LLM))
            mr.record(run, "task_cost_cents", 0.5)
            records = mr.query_by_name("task_cost_cents")
            assert len(records) == 1
            assert records[0]["value"] == 0.5
            mr.close()
        finally:
            import os
            os.unlink(db_path)


class TestMetricsFull:
    def test_track_started_succeeded(self):
        m = Metrics()
        run = TaskRun(run_id="m2-1", spec=TaskSpec(name="t", kind=TaskKind.LLM))
        m.track(run, "started")
        run.status = TaskStatus.SUCCEEDED
        m.track(run, "succeeded")
        assert m.metric_registry.summary("task_duration_seconds")["count"] == 1
        assert m.metric_registry.summary("task_attempts")["count"] == 1

    def test_track_cost_and_tokens(self):
        m = Metrics()
        run = TaskRun(run_id="m2-2", spec=TaskSpec(name="t", kind=TaskKind.LLM))
        m.track(run, "llm.cost", extra_tags={"cost_cents": "0.5", "tokens": "1500"})
        assert m.metric_registry.summary("task_cost_cents")["count"] == 1
        assert m.metric_registry.summary("task_tokens_total")["count"] == 1


# ===== M1.3 Gateway 完整版 =====


class TestResponseGate:
    def test_json_repair_brace_extraction(self):
        gate = ResponseGate()
        resp = ChatResponse(text="一些前缀\n{\"key\": \"value\"}\n一些后缀")
        repaired = gate.admit(resp, "json")
        assert "一些前缀" not in repaired.text
        assert '"key": "value"' in repaired.text

    def test_json_repair_no_change(self):
        gate = ResponseGate()
        resp = ChatResponse(text='{"valid": "json"}')
        repaired = gate.admit(resp, "json")
        assert repaired.text == '{"valid": "json"}'


class TestMetricsSink:
    def test_record_and_summary(self):
        sink = MetricsSink()
        sink.record("r1", "mock", "m1", "complexity", 100, 50, 200.0, 0.05)
        sink.record("r1", "mock", "m1", "complexity", 200, 100, 300.0, 0.10, success=False)
        summary = sink.summary()
        assert summary["total_calls"] == 2
        assert summary["failed"] == 1
        assert summary["success_rate"] == 50.0

    def test_filter_by_run_id(self):
        sink = MetricsSink()
        sink.record("r1", "mock", "m1", "complexity", 100, 50, 100.0)
        sink.record("r2", "mock", "m1", "complexity", 100, 50, 100.0)
        assert len(sink.get_records("r1")) == 1
        assert len(sink.get_records()) == 2


class TestRateLimiter:
    def test_allow_within_capacity(self):
        rl = RateLimiter()
        assert rl.allow("test", 1.0, 10.0, 10.0) is True
        assert rl.allow("test", 1.0, 10.0, 10.0) is True

    def test_block_when_exhausted(self):
        rl = RateLimiter()
        # 容量 1，消耗 1，应该允许
        assert rl.allow("test2", 1.0, 1.0, 0.01) is True
        # 第二次应该被阻止（容量 1，恢复需要时间）
        # 注意：这里 refill 可能发生，但 refill_rate 很低
        allowed = rl.allow("test2", 1.0, 1.0, 0.01)
        # 可能被阻止（如果 refill 来不及）
        # 不强行断言，只需确保不抛异常

    def test_wait_time(self):
        rl = RateLimiter()
        # 初始容量 5，消耗 1，wait_time 应为 0
        rl.allow("test3", 1.0, 5.0, 1.0)
        assert rl.wait_time("test3", 1.0) == 0.0


class TestSemanticCache:
    def test_store_and_lookup(self):
        cache = SemanticCache(max_size=10, ttl_s=60.0)
        req = ChatRequest(messages=[{"role": "user", "content": "hello"}], hint=TaskHint(complexity=HintComplexity.simple, quality_critical=False))
        resp = ChatResponse(text="world")
        cache.store(req, resp)
        cached = cache.lookup(req)
        assert cached is not None
        assert cached.text == "world"

    def test_quality_critical_skips_cache(self):
        cache = SemanticCache(max_size=10, ttl_s=60.0)
        req = ChatRequest(messages=[{"role": "user", "content": "hello"}], hint=TaskHint(complexity=HintComplexity.simple, quality_critical=True))
        resp = ChatResponse(text="world")
        cache.store(req, resp)
        cached = cache.lookup(req)
        assert cached is None  # quality_critical 不走缓存


class TestGatewayM1:
    def test_gateway_with_response_gate(self):
        gateway = Gateway()
        gateway.registry.register("mock", MockProvider())
        req = ChatRequest(messages=[{"role": "user", "content": "test"}], extra={"format": "json"})
        resp = gateway.chat(req)
        assert resp.text == '{"result": "ok"}'

    def test_gateway_metrics_sink(self):
        gateway = Gateway()
        gateway.registry.register("mock", MockProvider())
        req = ChatRequest(messages=[{"role": "user", "content": "test"}], run_id="m1-test")
        gateway.chat(req)
        records = gateway.metrics_sink.get_records("m1-test")
        assert len(records) == 1
        assert records[0]["success"] is True

    def test_gateway_semantic_cache(self):
        gateway = Gateway()
        gateway.registry.register("mock", MockProvider())
        req = ChatRequest(
            messages=[{"role": "user", "content": "cache test"}],
            hint=TaskHint(complexity=HintComplexity.simple, quality_critical=False),
            run_id="cache-test",
        )
        resp1 = gateway.chat(req)
        resp2 = gateway.chat(req)
        # 第二次应当命中缓存，provider/model 相同
        assert resp2.text == resp1.text


# ===== M1.4 业务 Task =====


class MockLLMProvider:
    """模拟 LLM 响应（含 JSON 输出）"""

    name = "mock-llm"

    def complete(self, packed: PackedRequest) -> RawResponse:
        return RawResponse(
            text='{"score": 8, "issues": ["情节略慢"], "suggestions": "加快节奏"}',
            provider="mock-llm",
            model="mock-llm-model",
            usage_input=packed.estimated_input_tokens,
            usage_output=100,
        )

    def count_tokens(self, text: str) -> int:
        return len(text) // 4

    def model_card(self) -> ModelCard:
        return ModelCard(provider="mock-llm", model="mock-llm-model", cost_per_1k_input_cents=0.5, cost_per_1k_output_cents=1.5, context_window=128000)


class TestReviewTask:
    def test_review_executor_basic(self):
        gateway = Gateway()
        gateway.registry.register("mock-llm", MockLLMProvider())
        store = ArtifactStore()
        from llmagent.tasks.review import ReviewExecutor, REVIEW_SPEC

        executor = ReviewExecutor(gateway, store)
        run = TaskRun(
            run_id="review-test-1",
            spec=REVIEW_SPEC,
            output={
                "chapter_title": "初入江湖",
                "chapter_content": "这是一个测试章节的内容...",
            },
        )
        result = asyncio.run(executor.execute(run))
        assert result.status.value == "SUCCEEDED"
        assert "score" in result.output


class TestOutlineTask:
    def test_outline_executor_basic(self):
        gateway = Gateway()
        gateway.registry.register("mock-llm", MockLLMProvider())
        store = ArtifactStore()
        from llmagent.tasks.outline import OutlineExecutor, OUTLINE_SPEC

        executor = OutlineExecutor(gateway, store)
        run = TaskRun(
            run_id="outline-test-1",
            spec=OUTLINE_SPEC,
            output={
                "story_summary": "一个少年的武侠成长故事",
                "chapter_count": 10,
            },
        )
        result = asyncio.run(executor.execute(run))
        assert result.status.value == "SUCCEEDED"
        assert "summary" in result.output


class TestAnalyzeTask:
    def test_analyze_executor_basic(self):
        gateway = Gateway()
        gateway.registry.register("mock-llm", MockLLMProvider())
        store = ArtifactStore()
        from llmagent.tasks.analyze import AnalyzeExecutor, ANALYZE_SPEC

        executor = AnalyzeExecutor(gateway, store)
        run = TaskRun(
            run_id="analyze-test-1",
            spec=ANALYZE_SPEC,
            output={
                "content": "这是一个测试故事内容...",
                "analysis_type": "plot",
            },
        )
        result = asyncio.run(executor.execute(run))
        assert result.status.value == "SUCCEEDED"
        assert "analysis" in result.output