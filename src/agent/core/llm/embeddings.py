"""EmbeddingProvider（增量 A / T02）

文本嵌入提供方：
- ``EmbeddingProvider``：抽象基类，统一 ``embed(texts) -> list[list[float]]``。
- ``OpenAICompatibleEmbedding``：走 OpenAI 兼容 ``/embeddings`` 协议（复用 ``.env`` 的
  ``LLM_BASE_URL`` / ``LLM_API_KEY`` / ``EMBEDDING_MODEL_ID``）。
- ``OllamaEmbedding``：本地 Ollama ``/api/embeddings``（零成本离线 embedding）。
- ``QwenLocalEmbedding``：本地 Qwen 模型（transformers 离线推理，通过 ``HF_ENDPOINT``
  镜像下载）。

归属 llm/（embedding 是模型调用能力，属 LLM 基础设施）；
``LLMProvider.embed`` / ``LLMClient.embed`` / ``llm/embedding_router`` 均委托到本模块，
rag/ 的 Indexer 经 ``embed_fn`` 参数注入使用，不直接依赖本模块。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import Any


class EmbeddingProvider(ABC):
    """嵌入提供方抽象接口"""

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """批量生成文本嵌入向量

        Args:
            texts: 待嵌入文本列表

        Returns:
            与 ``texts`` 等长的向量列表；元素为 float 向量。
        """
        ...


class OpenAICompatibleEmbedding(EmbeddingProvider):
    """OpenAI 兼容协议嵌入（默认）

    默认复用 ``.env`` 的 ``LLM_BASE_URL`` / ``LLM_API_KEY``，模型取 ``EMBEDDING_MODEL_ID``
    （缺省回退主模型 ``LLM_MODEL_ID``）。若设置独立的 ``EMBEDDING_BASE_URL`` /
    ``EMBEDDING_API_KEY``（路径 B），embedding 与 chat 端点解耦，可让向量化走另一家
    支持 ``/embeddings`` 的服务（如 dashscope/OpenAI），而 chat 仍用原 MaaS 部署。
    """

    def __init__(self, model: str, base_url: str, api_key: str) -> None:
        self.model = model
        self.base_url = base_url
        self.api_key = api_key

    def embed(self, texts: list[str]) -> list[list[float]]:
        from openai import OpenAI

        client = OpenAI(
            api_key=self.api_key or "EMPTY",
            base_url=self.base_url or None,
            timeout=120,
        )
        resp = client.embeddings.create(model=self.model, input=texts)
        return [list(item.embedding) for item in resp.data]


class OllamaEmbedding(EmbeddingProvider):
    """Ollama 本地嵌入（零成本离线）

    走 ``{base_url}/api/embeddings``（单条推理，兼容无 batch 端点）。
    """

    def __init__(self, model: str, base_url: str = "http://localhost:11434") -> None:
        self.model = model
        self.base_url = base_url.rstrip("/") or "http://localhost:11434"

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            payload = json.dumps(
                {"model": self.model, "prompt": text},
                ensure_ascii=False,
            ).encode("utf-8")
            req = urllib.request.Request(
                self.base_url + "/api/embeddings",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    body = json.loads(r.read().decode("utf-8"))
                embedding = body.get("embedding")
                if not embedding:
                    out.append([])
                else:
                    out.append([float(x) for x in embedding])
            except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
                # 单条失败不影响其它条；返回空向量，由调用方降级为 BM25-only
                out.append([])
        return out


class QwenLocalEmbedding(EmbeddingProvider):
    """本地 Qwen 模型嵌入（transformers 离线推理）

    使用 Hugging Face ``transformers`` 加载 Qwen 模型，通过 mean pooling
    提取文本嵌入向量。模型首次使用自动通过 ``HF_ENDPOINT`` 镜像下载。

    配置（.env）：
        EMBEDDING_MODEL_ID=Qwen/Qwen2.5-0.5B-Instruct  # 模型名，默认最小 Qwen
        EMBEDDING_DEVICE=cpu                            # cpu / cuda，默认自动检测
    """

    def __init__(
        self,
        model_name: str = "Qwen/Qwen2.5-0.5B-Instruct",
        device: str | None = None,
        max_length: int = 512,
        batch_size: int = 8,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.max_length = max_length
        self.batch_size = batch_size
        self._model = None
        self._tokenizer = None

    def _load(self) -> None:
        if self._model is not None:
            return
        import torch
        from transformers import AutoModel, AutoTokenizer

        self._tokenizer = AutoTokenizer.from_pretrained(
            self.model_name, trust_remote_code=True
        )
        self._model = AutoModel.from_pretrained(
            self.model_name, trust_remote_code=True
        )
        if self.device is None:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self._model.to(self.device)
        self._model.eval()

    @staticmethod
    def _mean_pooling(
        token_embeddings: "torch.Tensor",
        attention_mask: "torch.Tensor",
    ) -> "torch.Tensor":
        """Mean pooling 加权平均 token 嵌入"""
        import torch

        mask = attention_mask.unsqueeze(-1).expand(token_embeddings.size()).float()
        return torch.sum(token_embeddings * mask, 1) / torch.clamp(
            mask.sum(1), min=1e-9
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        import torch

        self._load()
        all_embeddings: list[list[float]] = []

        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            inputs = self._tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=self.max_length,
                return_tensors="pt",
            ).to(self.device)

            with torch.no_grad():
                outputs = self._model(**inputs)
                embeddings = self._mean_pooling(
                    outputs.last_hidden_state, inputs["attention_mask"]
                )
                # L2 归一化
                embeddings = torch.nn.functional.normalize(embeddings, p=2, dim=1)

            all_embeddings.extend(embeddings.cpu().tolist())

        return all_embeddings
