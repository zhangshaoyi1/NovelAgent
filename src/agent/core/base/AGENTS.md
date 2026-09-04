# AGENTS.md - core/base/ 基础基础设施层

## 职责

提供不依赖 Core 业务语义的基础设施，被所有上层包依赖。

## 核心模块

| 文件 | 导出 | 作用 |
|------|------|------|
| `exceptions.py` | `FrozenFieldError`, `PreValidationBlocked` | 自定义异常定义 |
| `registry.py` | `BaseRegistry` | 注册表基类 |
| `retry.py` | `retry`, `RetryError`, `retry_transport`, `retry_parse` | 统一重试机制 |
| `structured_output.py` | `pydantic_to_json_schema`, `extract_json`, `StructuredOutputError` | 结构化输出（JSON Schema 生成） |
| `validation.py` | `validate_many`, `validate_model` | 边界校验（G15 P0-5） |

## 依赖规则

- 仅依赖标准库和 pydantic
- 不依赖任何 agent 包内模块