"""M2 组件单元测试：完整校验器 / 完整失败处理 / 统治理"""

from __future__ import annotations

import pytest

from llmagent.kernel.catalog import (
    Catalog,
    LineageGraph,
    PolicyLoader,
    SchemaGate,
    Versioner,
)
from llmagent.kernel.failure import (
    Catcher,
    Compensator,
    ErrorClassifier,
    Escalator,
    FailureAction,
    FailureContext,
    FailureHandler,
    FailurePolicy,
    Mutator,
    PolicyResolver,
    RedLineGuard,
    TaskStatus,
)
from llmagent.kernel.task import TaskKind, TaskRun, TaskSpec, ValidationPolicy
from llmagent.kernel.validator import (
    AllOfValidator,
    AnyOfValidator,
    ChainValidator,
    Composer,
    JsonSchemaValidator,
    ModelRunner,
    NoOpValidator,
    PolicyResolver,
    PureRunner,
    QualityScoreValidator,
    ResultLedger,
    ValidatorRegistry,
    ValidatorRunner,
    WeightedValidator,
    WordCountValidator,
)


# ===== M2.1 完整校验器 =====


class TestValidatorRegistry:
    def test_register_and_get(self):
        reg = ValidatorRegistry()
        reg.register("noop", NoOpValidator())
        assert reg.has("noop") is True
        v = reg.get("noop")
        assert v.name == "noop"

    def test_get_unregistered(self):
        reg = ValidatorRegistry()
        with pytest.raises(KeyError, match="未注册"):
            reg.get("nonexistent")


class TestBuiltinValidators:
    def test_noop_validator(self):
        v = NoOpValidator()
        assert v.validate({}).passed is True

    def test_json_schema_validator_passes(self):
        schema = {
            "title": "test",
            "type": "object",
            "required": ["name", "age"],
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
        }
        v = JsonSchemaValidator(schema)
        result = v.validate({"name": "test", "age": 25})
        assert result.passed is True

    def test_json_schema_validator_missing_field(self):
        schema = {
            "title": "test",
            "required": ["name"],
            "properties": {"name": {"type": "string"}},
        }
        v = JsonSchemaValidator(schema)
        result = v.validate({"age": 25})
        assert result.passed is False
        assert "name" in result.repair_hint

    def test_word_count_validator(self):
        v = WordCountValidator(min_words=5, max_words=100)
        assert v.validate({"content": "hello world"}).passed is True
        assert v.validate({"content": "hi"}).passed is False

    def test_quality_score_validator(self):
        v = QualityScoreValidator(min_score=3)
        assert v.validate({"score": 5}).passed is True
        assert v.validate({"score": 1}).passed is False


class TestComposers:
    def test_chain_short_circuit(self):
        """chain: 第一个失败即停止"""
        class FailValidator:
            name = "fail"
            @staticmethod
            def validate(ctx):
                from llmagent.kernel.validator import ValidationResult
                return ValidationResult(passed=False, error_class="TEST")

        class NeverCalled:
            name = "never"
            @staticmethod
            def validate(ctx):
                raise AssertionError("不应该被调用")

        chain = ChainValidator([FailValidator(), NeverCalled()])
        result = chain.validate({})
        assert result.passed is False

    def test_all_of_all_pass(self):
        v1 = NoOpValidator()
        v2 = NoOpValidator()
        all_of = AllOfValidator([v1, v2])
        assert all_of.validate({}).passed is True

    def test_any_of_one_pass(self):
        class FailValidator:
            name = "fail"
            @staticmethod
            def validate(ctx):
                from llmagent.kernel.validator import ValidationResult
                return ValidationResult(passed=False, error_class="TEST")

        any_of = AnyOfValidator([FailValidator(), NoOpValidator()])
        assert any_of.validate({}).passed is True

    def test_weighted_above_threshold(self):
        v1 = NoOpValidator()
        v2 = NoOpValidator()
        weighted = WeightedValidator([(v1, 0.6), (v2, 0.4)], threshold=0.5)
        assert weighted.validate({}).passed is True

    def test_composer_single_returns_original(self):
        v = NoOpValidator()
        composed = Composer.compose([v])
        assert composed is v


class TestResultLedger:
    def test_record_and_statistics(self):
        ledger = ResultLedger()
        from llmagent.kernel.validator import ValidationResult
        ledger.record("run-1", "word_count", ValidationResult(passed=True))
        ledger.record("run-1", "quality", ValidationResult(passed=False, error_class="SEMANTIC"))
        stats = ledger.statistics()
        assert stats["total"] == 2
        assert stats["passed"] == 1
        assert stats["failed"] == 1

    def test_get_records(self):
        ledger = ResultLedger()
        from llmagent.kernel.validator import ValidationResult
        ledger.record("run-1", "test", ValidationResult(passed=True))
        records = ledger.get_records("run-1")
        assert len(records) == 1

    def test_records_by_error(self):
        ledger = ResultLedger()
        from llmagent.kernel.validator import ValidationResult
        ledger.record("run-1", "v1", ValidationResult(passed=False, error_class="SEMANTIC"))
        ledger.record("run-2", "v2", ValidationResult(passed=False, error_class="BUDGET"))
        semantic_records = ledger.get_records_by_error("SEMANTIC")
        assert len(semantic_records) == 1


class TestValidatorRunner:
    def test_run_with_policy(self):
        reg = ValidatorRegistry()
        reg.register("word_count", WordCountValidator(min_words=5, max_words=100))
        resolver = PolicyResolver(reg)
        runner = ValidatorRunner(policy_resolver=resolver)
        policy = ValidationPolicy(validators=["word_count"])
        result = runner.run(policy, {"content": "hello world"}, run_id="vr-1")
        assert result.passed is True

    def test_run_without_policy_falls_back_to_noop(self):
        runner = ValidatorRunner()
        result = runner.run(None, {}, run_id="vr-2")
        assert result.passed is True


# ===== M2.2 完整失败处理 =====


class TestCatcher:
    def test_catch_exception(self):
        caught = Catcher.catch("run-1", ValueError("test error"))
        assert caught.source == "exception"
        assert "test error" in caught.raw

    def test_from_validation(self):
        caught = Catcher.from_validation("run-1", "SEMANTIC", "字数不足", ["需要更多字数"])
        assert caught.source == "validation"
        assert caught.error_class == "SEMANTIC"


class TestErrorClassifier:
    def test_classify_by_keyword(self):
        assert ErrorClassifier.classify("timeout after 30s") == "TIMED_OUT"
        assert ErrorClassifier.classify("rate limit exceeded") == "RATE_LIMIT"
        assert ErrorClassifier.classify(ValueError("invalid param")) == "DETERMINISTIC"
        assert ErrorClassifier.classify("semantic error in output") == "SEMANTIC"

    def test_severity_weight(self):
        assert ErrorClassifier.severity_weight("BUDGET") == 1.0
        assert ErrorClassifier.severity_weight("UNKNOWN") == 0.5


class TestMutator:
    def test_mutate_semantic(self):
        action = FailureAction(action="stop", status=TaskStatus.FAILED)
        ctx = FailureContext(
            run=TaskRun(run_id="m", spec=TaskSpec(name="t", kind=TaskKind.LLM)),
            error=Catcher.from_validation("m", "SEMANTIC", "输出格式错误"),
        )
        result = Mutator.mutate(ctx, action)
        assert result is not None
        assert result.action == "retry"

    def test_mutate_unsupported_class(self):
        action = FailureAction(action="stop", status=TaskStatus.FAILED)
        ctx = FailureContext(
            run=TaskRun(run_id="m", spec=TaskSpec(name="t", kind=TaskKind.LLM)),
            error=Catcher.from_validation("m", "BUDGET", "预算不足"),
        )
        result = Mutator.mutate(ctx, action)
        assert result is None


class TestCompensator:
    def test_compensate_budget(self):
        comp = Compensator()
        action = FailureAction(action="stop", status=TaskStatus.FAILED)
        ctx = FailureContext(
            run=TaskRun(run_id="c", spec=TaskSpec(name="t", kind=TaskKind.LLM)),
            error=Catcher.from_validation("c", "BUDGET", "预算不足"),
            compensation_count=0,
        )
        result = comp.compensate(ctx, action)
        assert result.action == "retry"

    def test_compensate_overflow(self):
        comp = Compensator(max_compensations=2)
        action = FailureAction(action="stop", status=TaskStatus.FAILED)
        ctx = FailureContext(
            run=TaskRun(run_id="c", spec=TaskSpec(name="t", kind=TaskKind.LLM)),
            error=Catcher.from_validation("c", "BUDGET", "预算不足"),
            compensation_count=3,
        )
        result = comp.compensate(ctx, action)
        assert result.action == "escalate"


class TestEscalator:
    def test_escalate_low_severity(self):
        ctx = FailureContext(
            run=TaskRun(run_id="e", spec=TaskSpec(name="t", kind=TaskKind.LLM)),
            error=Catcher.from_validation("e", "RATE_LIMIT", "限流"),
        )
        action = FailureAction(action="compensate", status=TaskStatus.FAILED)
        result = Escalator.escalate(ctx, action)
        assert result.action == "ignore"

    def test_escalate_high_severity(self):
        ctx = FailureContext(
            run=TaskRun(run_id="e", spec=TaskSpec(name="t", kind=TaskKind.LLM)),
            error=Catcher.from_validation("e", "BUDGET", "预算熔断"),
        )
        action = FailureAction(action="compensate", status=TaskStatus.FAILED)
        result = Escalator.escalate(ctx, action)
        assert result.action == "stop"


class TestFailureHandler:
    def test_retry_policy(self):
        handler = FailureHandler()
        spec = TaskSpec(name="t", kind=TaskKind.LLM, failure_policy=FailurePolicy(max_retries=3))
        run = TaskRun(run_id="f-1", spec=spec, attempt=0)
        action = handler.handle(run, ValueError("临时错误"))
        assert action.action == "retry"

    def test_stop_when_retries_exhausted(self):
        handler = FailureHandler()
        spec = TaskSpec(name="t", kind=TaskKind.LLM, failure_policy=FailurePolicy(max_retries=2))
        run = TaskRun(run_id="f-2", spec=spec, attempt=2)
        action = handler.handle(run, RuntimeError("仍然失败"))
        assert action.action in ("stop", "escalate")

    def test_escalate_on_specific_error(self):
        handler = FailureHandler()
        spec = TaskSpec(name="t", kind=TaskKind.LLM, failure_policy=FailurePolicy(escalate_on=["BUDGET"]))
        run = TaskRun(run_id="f-3", spec=spec)
        action = handler.handle(run, RuntimeError("budget exceeded"))
        assert action.action == "escalate"

    def test_validation_error_handling(self):
        handler = FailureHandler()
        spec = TaskSpec(name="t", kind=TaskKind.LLM, failure_policy=FailurePolicy(ignore_failure=True))
        run = TaskRun(run_id="f-4", spec=spec)
        action = handler.handle(run, validation_error=("SEMANTIC", "字数不足", ["需要更多字数"]))
        assert action.action == "ignore"


# ===== M2.3 统治理 =====


class TestCatalogVersionManagement:
    def test_version_on_register(self):
        catalog = Catalog()
        spec = TaskSpec(name="test", kind=TaskKind.LLM, timeout_s=60.0)
        ref = catalog.register(spec)
        assert ref != ""
        assert catalog.get_version("test") == ref

    def test_version_history(self):
        catalog = Catalog()
        spec1 = TaskSpec(name="vtest", kind=TaskKind.LLM, timeout_s=60.0)
        catalog.register(spec1)
        spec2 = TaskSpec(name="vtest", kind=TaskKind.LLM, timeout_s=120.0)
        catalog.register(spec2)
        history = catalog.get_version_history("vtest")
        assert len(history) >= 1

    def test_get_with_version(self):
        catalog = Catalog()
        spec = TaskSpec(name="ver", kind=TaskKind.LLM, timeout_s=60.0)
        ref = catalog.register(spec)
        retrieved = catalog.get("ver", version=ref)
        assert retrieved.name == "ver"

    def test_get_with_wrong_version(self):
        catalog = Catalog()
        spec = TaskSpec(name="ver", kind=TaskKind.LLM, timeout_s=60.0)
        catalog.register(spec)
        with pytest.raises(KeyError, match="版本"):
            catalog.get("ver", version="wrong-version")

    def test_list_all(self):
        catalog = Catalog()
        catalog.register(TaskSpec(name="t1", kind=TaskKind.LLM, timeout_s=60.0))
        catalog.register(TaskSpec(name="t2", kind=TaskKind.TOOL, timeout_s=30.0))
        items = catalog.list_all()
        assert len(items) == 2
        names = [i["name"] for i in items]
        assert "t1" in names
        assert "t2" in names


class TestLineageGraph:
    def test_add_and_query_dependency(self):
        graph = LineageGraph()
        graph.add_dependency("chapter_writer", "outline_generator")
        graph.add_dependency("chapter_writer", "character_db")
        deps = graph.dependencies_of("chapter_writer")
        assert len(deps) == 2
        dependents = graph.dependents_of("outline_generator")
        assert len(dependents) == 1
        assert dependents[0].from_task == "chapter_writer"

    def test_impact_analysis(self):
        graph = LineageGraph()
        graph.add_dependency("A", "B")
        graph.add_dependency("B", "C")
        graph.add_dependency("D", "C")
        impact = graph.impact_analysis("C")
        assert "B" in impact["direct_dependents"]
        assert "A" in impact["all_affected"]

    def test_to_dot(self):
        graph = LineageGraph()
        graph.add_dependency("A", "B")
        dot = graph.to_dot()
        assert 'A"' in dot
        assert 'B"' in dot
        assert "digraph" in dot

    def test_remove_dependency(self):
        graph = LineageGraph()
        graph.add_dependency("A", "B")
        graph.remove_dependency("A", "B")
        assert len(graph.dependencies_of("A")) == 0


class TestPolicyLoader:
    def test_set_and_get_override(self):
        loader = PolicyLoader()
        loader.set_override("write_chapter", "timeout_s", 600.0)
        assert loader.get_override("write_chapter", "timeout_s") == 600.0

    def test_apply_overrides(self):
        loader = PolicyLoader()
        loader.set_override("test", "timeout_s", 999.0)
        spec = TaskSpec(name="test", kind=TaskKind.LLM, timeout_s=60.0)
        modified = loader.apply_overrides(spec)
        assert modified.timeout_s == 999.0

    def test_no_overrides_returns_original(self):
        loader = PolicyLoader()
        spec = TaskSpec(name="test", kind=TaskKind.LLM, timeout_s=60.0)
        modified = loader.apply_overrides(spec)
        assert modified is spec  # 未覆盖时返回原对象

    def test_policy_loader_with_catalog(self):
        catalog = Catalog()
        catalog.policy_loader.set_override("test", "timeout_s", 500.0)
        spec = TaskSpec(name="test", kind=TaskKind.LLM, timeout_s=60.0)
        catalog.register(spec)
        retrieved = catalog.get("test")
        assert retrieved.timeout_s == 500.0


class TestRedlineReport:
    def test_record_and_report(self):
        catalog = Catalog()
        catalog.record_redline_violation("run-1", "红线1", "预算熔断")
        catalog.record_redline_violation("run-2", "红线2", "重试超限")
        report = catalog.get_redline_report()
        assert report["total"] == 2
        assert report["by_redline"]["红线1"] == 1
        assert report["by_redline"]["红线2"] == 1