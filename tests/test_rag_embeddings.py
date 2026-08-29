"""EmbeddingProvider 测试（增量 A / T02）

通过 mock HTTP 层验证 OpenAICompatibleEmbedding / OllamaEmbedding 的协议契约，
不发起真实网络调用。
"""

from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import MagicMock, patch

from agent.client.embeddings import (
    EmbeddingProvider,
    OllamaEmbedding,
    OpenAICompatibleEmbedding,
)


class _FakeResp:
    """模拟 urllib urlopen 的上下文管理器返回"""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._data


class TestEmbeddingProviderABC:
    def test_abc_cannot_instantiate(self) -> None:
        import pytest

        with pytest.raises(TypeError):
            EmbeddingProvider()  # type: ignore[abstract]


class TestOpenAICompatibleEmbedding:
    def test_embed_returns_vectors(self) -> None:
        fake_client = MagicMock()
        fake_resp = MagicMock()
        d1 = MagicMock()
        d1.embedding = [0.1, 0.2]
        d2 = MagicMock()
        d2.embedding = [0.3, 0.4]
        fake_resp.data = [d1, d2]
        fake_client.embeddings.create.return_value = fake_resp

        with patch("openai.OpenAI", return_value=fake_client):
            emb = OpenAICompatibleEmbedding(model="m", base_url="http://x", api_key="k")
            out = emb.embed(["a", "b"])

        assert out == [[0.1, 0.2], [0.3, 0.4]]
        fake_client.embeddings.create.assert_called_once()


class TestOllamaEmbedding:
    def test_embed_returns_vectors(self) -> None:
        resp_body = json.dumps({"embedding": [0.5, 0.6]}).encode("utf-8")
        with patch(
            "agent.client.embeddings.urllib.request.urlopen",
            return_value=_FakeResp(resp_body),
        ):
            emb = OllamaEmbedding(model="m")
            out = emb.embed(["a", "b"])

        assert len(out) == 2
        assert out[0] == [0.5, 0.6]
        assert out[1] == [0.5, 0.6]

    def test_embed_single_failure_yields_empty_vector(self) -> None:
        # 第一次成功，第二次 urlopen 抛 URLError → 返回空向量（降级）
        good = json.dumps({"embedding": [0.7]}).encode("utf-8")
        with patch(
            "agent.client.embeddings.urllib.request.urlopen",
            side_effect=[_FakeResp(good), urllib.error.URLError("boom")],
        ):
            emb = OllamaEmbedding(model="m")
            out = emb.embed(["ok", "bad"])

        assert out[0] == [0.7]
        assert out[1] == []  # 失败降级为空向量
