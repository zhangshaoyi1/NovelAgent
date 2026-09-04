# AGENTS.md - base/ 基础抽象层

## 职责

提供所有上层组件依赖的基础抽象，**不依赖任何上层**。

## 核心模块

| 文件 | 导出 | 作用 |
|------|------|------|
| `agent.py` | `Agent` | Agent 基类（统一接口） |
| `config.py` | `BaseConfig` | 配置基类工具 |
| `llm.py` | `LLMConfig`, `LLMError`, `LLMProvider`, `LLMResponse`, `register_provider` | LLM 协议层 |
| `message.py` | `Message`, `Role` | 消息协议定义 |
| `result.py` | `AgentResult` | 结果基类 |
| `types.py` | `AsyncCallback`, `JsonDict`, `ModelName`, `TokenCount` | 公共类型定义 |
| `utils.py` | `chunk_text`, `parse_llm_json`, `safe_remove` | 纯工具函数 |
| `structured_output.py` | `pydantic_to_json_schema`, `extract_json`, `StructuredOutputError` | 结构化输出 |

## 依赖规则

- `base/` **不依赖任何 agent 包内模块**
- 只依赖标准库和 pydantic
- 所有上层包（client/core/agents/workflows）都依赖 base

## 重要约束

- 打破单向依赖约束会导致循环导入，必须立即回滚
- 新增工具函数应放在 `utils.py`，新增类型放在 `types.py`