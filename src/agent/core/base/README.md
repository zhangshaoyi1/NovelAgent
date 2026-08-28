# base/ — 基础基础设施层

## 职责
提供不依赖 Core 业务语义的基础设施，被所有上层包依赖。

## 包含文件
| 文件 | 职责 |
|------|------|
| `exceptions.py` | 自定义异常定义（LLMError, FrozenFieldError, PreValidationBlocked） |
| `registry.py` | BaseRegistry 通用注册表基类 |
| `retry.py` | 统一重试机制（retry, retry_transport, retry_parse） |
| `structured_output.py` | 结构化输出（JSON Schema 生成、提取） |

## 依赖规则
- 仅依赖标准库
- 不依赖任何 agent 包内模块

## 依赖它的包
- engine/ (状态机、Agent 循环)
- story/ (设定管理、伏笔管理)
- quality/ (质量检查器)
- llm/ (LLM 客户端)
- registry/ (技能/题材注册表)
- client/ (LLM 客户端)
- workflows/ (所有工作流)