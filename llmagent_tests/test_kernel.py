"""Kernel 单元测试：Task / EventBus / ArtifactStore / Monitor / Metrics / Validator / Failure"""

from __future__ import annotations

import pytest

from llmagent.kernel.artifact import ArtifactStore
from llmagent.kernel.event_bus import EventBus
from llmagent.kernel.executor import MountResolver
from llmagent.kernel.failure import (
    Catcher,
    ErrorClassifier,
    FailureHandler,
    FailurePolicy,
    PolicyResolver,
    RedLineGuard,
    TaskStatus,
)
from llmagent.kernel.metrics import MetricRegistry, Metrics, SpanBuilder
from llmagent.kernel.monitor import (
    BudgetWatcher,
    Heartbeater,
    LoopDetector,
    Monitor,
    MonitorEvent,
)
from llmagent.kernel.task import TaskKind, TaskRun, TaskSpec
from llmagent.kernel.validator import (
    ChainValidator,
    Composer,
    NoOpValidator,
    PureRunner,
    ResultLedger,
    ValidationResult,
    ValidatorRunner,
)


# ---- Task ----


class TestTask:
    def test_task_spec_basic(self):
        spec = TaskSpec(name="write_chapter", kind=TaskKind.LLM)
        assert spec.kind == TaskKind.LLM
        assert spec.name == "write_chapter"

    def test_task_run_state_machine(self):
        run = TaskRun(run_id="test-001", spec=TaskSpec(name="test", kind=TaskKind.LLM))
        assert run.status == TaskStatus.PENDING
        run.status = TaskStatus.RUNNING
        assert run.status == TaskStatus.RUNNING


# ---- MountResolver ----


class TestMountResolver:
    def test_resolve_registered(self):
        resolver = MountResolver()

        class MockExecutor:
            kind = TaskKind.LLM

        resolver.mount(TaskKind.LLM, MockExecutor)
        cls = resolver.resolve(TaskKind.LLM)
        assert cls == MockExecutor

    def test_resolve_unregistered(self):
        resolver = MountResolver()
        with pytest.raises(ValueError, match="未注册"):
            resolver.resolve(TaskKind.LLM)


# ---- EventBus ----


class TestEventBus:
    def test_append_and_query(self):
        bus = EventBus()
        seq = bus.append("run-1", "test.event", {"key": "value"})
        assert seq > 0
        events = bus.get_events("run-1")
        assert len(events) == 1
        assert events[0]["type"] == "test.event"
        assert events[0]["payload"]["key"] == "value"

    def test_append_only(self):
        bus = EventBus()
        bus.append("run-1", "e1", {"msg": "first"})
        bus.append("run-1", "e2", {"msg": "second"})
        events = bus.get_events("run-1")
        assert len(events) == 2
        assert events[0]["seq"] < events[1]["seq"]


# ---- ArtifactStore ----


class TestArtifactStore:
    def test_put_and_get(self):
        store = ArtifactStore()
        ref = store.put({"hello": "world"})
        data = store.get(ref)
        assert data is not None
        assert b"world" in data

    def test_dedup(self):
        store = ArtifactStore()
        ref1 = store.put("same content")
        ref2 = store.put("same content")
        assert ref1.sha256 == ref2.sha256


# ---- Monitor ----


class TestHeartbeater:
    def test_heartbeat_alive(self):
        hb = Heartbeater(timeout_s=60.0)
        hb.start("run-1")
        assert hb.is_alive("run-1") is True

    def test_heartbeat_timed_out(self):
        hb = Heartbeater(timeout_s=0.0)
        hb.start("run-1")
        import time
        time.sleep(0.01)
        assert hb.is_alive("run-1") is False


class TestBudgetWatcher:
    def test_warn_at_80_percent(self):
        bw = BudgetWatcher()
        bw.watch("ledger-1", total_cents=100.0)
        assert bw.check("ledger-1", 80.0) == "warn"
        assert bw.check("ledger-1", 50.0) == ""

    def test_melt_at_100_percent(self):
        bw = BudgetWatcher()
        bw.watch("ledger-1", total_cents=100.0)
        assert bw.check("ledger-1", 100.0) == "melt"


class TestLoopDetector:
    def test_detect_loop(self):
        ld = LoopDetector(max_duplicates=3)
        assert ld.observe("act-1", "run-1") is False
        assert ld.observe("act-1", "run-1") is False
        assert ld.observe("act-1", "run-1") is True  # 第三次相同 → 检测到环

    def test_no_loop_for_different_actions(self):
        ld = LoopDetector(max_duplicates=3)
        assert ld.observe("act-1", "run-1") is False
        assert ld.observe("act-2", "run-1") is False
        assert ld.observe("act-3", "run-1") is False


class TestMonitor:
    def test_signal_started(self):
        m = Monitor()
        m.signal("run-1", MonitorEvent(run_id="run-1", type="started"))
        assert m.heartbeater.is_alive("run-1") is True


# ---- Metrics ----


class TestMetrics:
    def test_track_duration(self):
        metrics = Metrics()
        run = TaskRun(run_id="m-1", spec=TaskSpec(name="t", kind=TaskKind.LLM))
        metrics.track(run, "started")
        run.status = TaskStatus.SUCCEEDED
        metrics.track(run, "succeeded")
        summary = metrics.metric_registry.summary("task_duration_seconds")
        assert summary["count"] == 1

    def test_span_builder(self):
        sb = SpanBuilder()
        run = TaskRun(run_id="s-1", spec=TaskSpec(name="t", kind=TaskKind.LLM))
        span = sb.start(run)
        assert span.span_id == "s-1"
        sb.finish(span, TaskStatus.SUCCEEDED)
        assert span.finished_at > 0


# ---- Validator ----


class TestValidatorRunner:
    def test_noop_validator_default(self):
        runner = ValidatorRunner()
        result = runner.run(None, {}, "LLM")
        assert result.passed is True

    def test_chain_validator_short_circuit(self):
        class FailValidator:
            name = "fail"
            @staticmethod
            def validate(ctx):
                return ValidationResult(passed=False, error_class="TEST")

        class NeverCalledValidator:
            name = "never"
            @staticmethod
            def validate(ctx):
                raise AssertionError("不应该被调用")

        chain = ChainValidator([FailValidator(), NeverCalledValidator()])
        result = chain.validate({})
        assert result.passed is False
        assert result.error_class == "TEST"


# ---- FailureHandler ----


class TestFailureHandler:
    def test_retry_policy(self):
        handler = FailureHandler()
        spec = TaskSpec(
            name="test",
            kind=TaskKind.LLM,
            failure_policy=FailurePolicy(max_retries=3),
        )
        run = TaskRun(run_id="f-1", spec=spec, attempt=0)
        action = handler.handle(run, RuntimeError("临时错误"))
        assert action.action == "retry"

    def test_ignore_failure(self):
        handler = FailureHandler()
        spec = TaskSpec(
            name="test",
            kind=TaskKind.LLM,
            failure_policy=FailurePolicy(ignore_failure=True),
        )
        run = TaskRun(run_id="f-2", spec=spec)
        action = handler.handle(run, RuntimeError("忽略"))
        assert action.action == "ignore"
        assert action.status == TaskStatus.SKIPPED

    def test_redline_max_retry(self):
        handler = FailureHandler()
        spec = TaskSpec(
            name="test",
            kind=TaskKind.LLM,
            failure_policy=FailurePolicy(max_retries=100),
        )
        run = TaskRun(run_id="f-3", spec=spec, attempt=8)
        action = handler.handle(run, RuntimeError("多次重试"))
        # 红线限制：重试超上限
        assert action.action in ("escalate", "stop")

    def test_error_classifier(self):
        assert ErrorClassifier.classify(RuntimeError("timeout")) == "TIMED_OUT"
        assert ErrorClassifier.classify(ValueError("invalid param")) == "DETERMINISTIC"
        assert ErrorClassifier.classify(RuntimeError("rate limit exceeded")) == "RATE_LIMIT"