# llmops/ — LLM 运营

## 职责
LLM 调用成本统计、追踪、提示版本管理和评测回归。

## 包含文件
| 文件 | 职责 |
|------|------|
| `cost.py` | 成本模型（CostModel, build_cost_summary） |
| `eval_harness.py` | 评测框架（EvalHarness） |
| `prompt_version.py` | 提示版本管理（PromptRegistry） |
| `trace.py` | 调用追踪（TraceStore, get_tracer, set_tracer） |
| `traced_llm.py` | LLM 客户端包装（TracedLLMClient） |

## 依赖规则
- 依赖 base/、client/

## 被依赖
- service/ (AgentService 接线 tracer)
- cli/ (appeal 命令)
- workflows/ (agentic_pipeline)