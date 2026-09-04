# AGENTS.md - client/ 统一 LLM 客户端层

## 职责

提供所有 LLM 调用的统一入口，屏蔽不同 LLM 提供商差异。

## 核心模块

| 文件 | 导出 | 作用 |
|------|------|------|
| `client.py` | `LLMClient`, `set_llm_event_hook` | 统一 LLM 客户端（对外唯一入口，内部使用原生 Gateway） |
| `provider.py` | `OpenAIProvider`, `OllamaProvider` | 具体 Provider 实现 |
| `router.py` | `ModelRouter`, `RouteCandidate`, `RouteDecision` | 动态模型路由（成功率熔断 + 回退） |
| `embeddings.py` | `EmbeddingProvider`, `OllamaEmbedding`, `OpenAICompatibleEmbedding`, `QwenLocalEmbedding` | 文本嵌入提供方 |
| `embedding_router.py` | `get_embedding_provider` | 嵌入路由选择 |

## LLM 调用方式

```python
from agent.client import LLMClient

client = LLMClient()
# 创作类调用（宽松解析）
resp = client.chat_creative([{"role": "user", "content": "..."}])
# 工具类调用（严格 JSON）
resp = client.chat_utility([{"role": "user", "content": "..."}])
```

## 依赖规则

- `client/` 只依赖 `base/`（消息协议、类型定义、LLM 协议）
- 不依赖 `core/` 或任何上层

## 向后兼容

- LLM 协议类型（`LLMConfig`/`LLMResponse`/`LLMError`/`LLMProvider`/`register_provider`）已下沉至 `agent.base.llm`，本包负责再导出
- Embedding 能力从 `core/llm` 下沉至本层