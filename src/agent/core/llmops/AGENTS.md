# AGENTS.md - core/llmops/ LLMOps 层

## 职责

可观测 · 成本 · 提示版本 · 评测回归（Phase 3）。

## 核心模块

| 文件 | 导出 | 作用 |
|------|------|------|
| `trace.py` | `TraceStore`, `TraceSpan`, `NullTracer`, `get_tracer`, `set_tracer` | 调用追踪 |
| `cost.py` | `CostModel`, `CostEstimate`, `DEFAULT_MODEL_PRICES`, `TIER_BASELINE_TOKENS_300`, `build_cost_summary` | 成本基线+告警 |
| `prompt_version.py` | `PromptRegistry` | 提示版本/漂移 |
| `eval_harness.py` | `EvalHarness`, `EvalRun`, `RegressionIssue` | 评测回归 |
| `traced_llm.py` | `TracedLLMClient` | 可追踪 LLM 包装 |
| `usage_reporter.py` | `UsageReporter`, `DEFAULT_USAGE_FILE` | 用量报告 |

## 依赖规则

- 依赖 base、client
- 通过延迟导入避免循环依赖