"""LLM 抽象层

职责：屏蔽不同 LLM 提供商，支持多模型分工。

多模型策略（PRD 决策 3）：
    - 创作用强模型（高质量）：章节生成、架构生成、角色设计
    - 校验/摘要用轻量模型（低成本）：质量校验、一致性检查、章节摘要

Provider 抽象（E1 增强）：
    - openai: OpenAI 兼容协议（默认）
    - ollama: 本地 Ollama 部署，零成本离线写作

配置来源（.env）：
    LLM_PROVIDER        - 提供商（openai | ollama，默认 openai）
    LLM_API_KEY         - API 密钥（ollama 不需要）
    LLM_BASE_URL        - 服务地址（openai 为 https://api.openai.com/v1，ollama 为 http://localhost:11434）
    LLM_MODEL_ID        - 主模型（创作用）
    LLM_MODEL_UTILITY   - 轻量模型（可选，未设置则与主模型相同）
    LLM_TIMEOUT         - 超时秒数（默认 120）
    LLM_MAX_RETRIES     - 重试次数（默认 3）
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar
from urllib.error import URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv
from pydantic import BaseModel

from agent.core.exceptions import LLMError
from agent.core.structured_output import (
    StructuredOutputError,
    extract_json,
    pydantic_to_json_schema,
)


# ============================================================
# 数据模型
# ============================================================
@dataclass
class LLMConfig:
    """LLM 配置"""

    provider: str = "openai"              # openai | ollama
    api_key: str = ""
    base_url: str = ""
    model: str = "glm-5.2"                # 主模型（创作）
    model_utility: str = ""               # 轻量模型（校验/摘要），空则用主模型
    embedding_model: str = ""             # 嵌入模型（RAG 用），空则回退 model
    # 路径 B：独立的嵌入端点（可选）。设置后 embedding 走专用 base/key，
    # 与 chat 端点解耦；缺省回退到下面的 base_url / api_key（即走 chat 同一端点）。
    embedding_base_url: str = ""           # 嵌入服务 base_url（OpenAI 兼容 /embeddings）
    embedding_api_key: str = ""            # 嵌入服务 api_key
    timeout: int = 120
    max_retries: int = 3
    retry_base_delay: float = 1.0
    # E1.4 无网络回退：主 Provider 不可达时回退到的备用 Provider
    # TODO(kou): fallback_provider 为兼容旧字段（字符串）。后续调用方统一迁移到
    # fallback_providers 列表后删除此字段（T-7）。
    fallback_provider: str = ""           # 如 "ollama"（兼容旧接口）
    fallback_model: str = ""              # 备用 Provider 使用的模型名
    # T-7：回退 Provider 列表（多候选回退链）。字符串/列表兼容，__post_init__ 归一化。
    fallback_providers: list[str] = field(default_factory=list)
    # 思考开关：None=不干预（走模型默认），False=强制关闭思考（提速、省 token，
    # 适合批量写长篇），True=强制开启。经 .env 的 LLM_ENABLE_THINKING 读取。
    enable_thinking: bool | None = None

    def __post_init__(self) -> None:
        """归一化 fallback_providers（兼容字符串入参与旧 fallback_provider 字段）"""
        if isinstance(self.fallback_providers, str):
            self.fallback_providers = [
                p.strip() for p in self.fallback_providers.split(",") if p.strip()
            ]
        if self.fallback_provider and not self.fallback_providers:
            self.fallback_providers = [
                p.strip() for p in self.fallback_provider.split(",") if p.strip()
            ]


@dataclass
class LLMResponse:
    """LLM 响应"""

    text: str
    usage: dict[str, int] = field(default_factory=dict)
    model: str = ""
    raw: Any = None


# ============================================================
# Provider 抽象层
# ============================================================
class LLMProvider(ABC):
    """LLM 提供商抽象接口"""

    # T-7：Provider 注册表（单一注册点）。新增 provider = 继承 LLMProvider 并注册到此，
    # 禁在 create() 中写 if/else。模块底部统一注册内置 provider。
    PROVIDERS: ClassVar[dict[str, type["LLMProvider"]]] = {}

    @abstractmethod
    def chat(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float,
        max_tokens: int | None,
        enable_thinking: bool | None,
        timeout: int,
        **kwargs: Any,
    ) -> LLMResponse:
        ...

    def embed(self, texts: list[str]) -> list[list[float]]:
        """生成文本嵌入向量（RAG 语义检索用）

        基类统一委托实现：依据 ``self.config.provider`` 选择 OpenAI 兼容或 Ollama
        嵌入端点（复用同一套 ``.env`` 配置 EMBEDDING_MODEL_ID / LLM_BASE_URL /
        LLM_API_KEY），避免在每个 Provider 子类重复 HTTP 逻辑。不可达时由
        ``LLMClient.embed`` 捕获并返回空列表降级（绝不阻断写章）。

        Args:
            texts: 待嵌入的文本列表

        Returns:
            与 ``texts`` 等长的向量列表；失败时返回 ``[]``。
        """
        from agent.core.rag.embeddings import (
            OllamaEmbedding,
            OpenAICompatibleEmbedding,
        )

        if self.config.provider == "ollama":
            provider = OllamaEmbedding(
                model=self.config.embedding_model or self.config.model,
                base_url=self.config.base_url or "http://localhost:11434",
            )
        else:
            provider = OpenAICompatibleEmbedding(
                model=self.config.embedding_model or self.config.model,
                base_url=self.config.base_url,
                api_key=self.config.api_key,
            )
        return provider.embed(texts)

    @staticmethod
    def create(config: LLMConfig) -> "LLMProvider":
        """根据配置创建 Provider（T-7：经 PROVIDERS 注册表，禁 if/else）

        Args:
            config: LLM 配置

        Raises:
            LLMError: provider 名称（非空）不在 PROVIDERS 注册表中
        """
        provider_name = config.provider.lower()
        # TODO(kou): 空 provider 视为 openai 为兼容旧默认行为（见
        # tests/test_llm_client.py::test_ollama_is_default_when_empty_string），
        # 后续可改为显式默认 provider 配置项。
        if provider_name == "":
            provider_name = "openai"
        try:
            provider_cls = LLMProvider.PROVIDERS[provider_name]
        except KeyError:
            available = ", ".join(sorted(LLMProvider.PROVIDERS)) or "(空)"
            raise LLMError(
                f"未知 LLM provider: {provider_name!r}。"
                f"可用 provider: {available}"
            ) from None
        return provider_cls(config)


class OpenAIProvider(LLMProvider):
    """OpenAI 兼容协议 Provider（默认）"""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            if not self.config.api_key:
                raise LLMError("LLM_API_KEY 未配置，请检查 .env")
            try:
                from openai import OpenAI
            except ImportError as e:
                raise LLMError("openai 包未安装，请运行 pip install openai") from e
            self._client = OpenAI(
                api_key=self.config.api_key,
                base_url=self.config.base_url or None,
                timeout=self.config.timeout,
            )
        return self._client

    def chat(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float,
        max_tokens: int | None,
        enable_thinking: bool | None,
        timeout: int,
        **kwargs: Any,
    ) -> LLMResponse:
        client = self._get_client()

        if enable_thinking is not None:
            kwargs.setdefault("extra_body", {})["enable_thinking"] = enable_thinking

        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            **kwargs,
        )
        return self._parse_response(resp, model)

    @staticmethod
    def _parse_response(resp: Any, model: str) -> LLMResponse:
        choice = resp.choices[0]
        text = choice.message.content or ""
        usage = {}
        if resp.usage is not None:
            usage = {
                "prompt_tokens": getattr(resp.usage, "prompt_tokens", 0),
                "completion_tokens": getattr(resp.usage, "completion_tokens", 0),
                "total_tokens": getattr(resp.usage, "total_tokens", 0),
            }
        return LLMResponse(text=text, usage=usage, model=model, raw=resp)

    def embed(self, texts: list[str]) -> list[list[float]]:
        """委托到 ``agent.core.rag.embeddings.OpenAICompatibleEmbedding``

        单一实现（避免双份 HTTP 逻辑）；懒导入规避与 ``rag`` 包的循环依赖。
        优先使用 ``embedding_base_url`` / ``embedding_api_key``（路径 B），
        缺省回退 ``base_url`` / ``api_key``（即 chat 同一端点，保持旧行为）。
        """
        from agent.core.rag.embeddings import OpenAICompatibleEmbedding

        provider = OpenAICompatibleEmbedding(
            model=self.config.embedding_model or self.config.model,
            # 路径 B：优先用独立嵌入端点，缺省回退 chat 端点（保持旧行为）
            base_url=self.config.embedding_base_url or self.config.base_url,
            api_key=self.config.embedding_api_key or self.config.api_key,
        )
        return provider.embed(texts)


class OllamaProvider(LLMProvider):
    """Ollama 本地部署 Provider

    对接本地 Ollama HTTP API（无需鉴权）。
    """

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        # Ollama 默认地址
        self.base_url = config.base_url or "http://localhost:11434"

    def chat(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float,
        max_tokens: int | None,
        enable_thinking: bool | None,
        timeout: int,
        **kwargs: Any,
    ) -> LLMResponse:
        import urllib.error
        import urllib.request

        url = f"{self.base_url.rstrip('/')}/api/chat"

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
            },
        }
        if max_tokens:
            payload["options"]["num_predict"] = max_tokens

        data = json.dumps(payload).encode("utf-8")
        req = Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            resp = urlopen(req, timeout=timeout)
            body = resp.read().decode("utf-8")
            result = json.loads(body)
        except URLError as e:
            raise LLMError(
                f"Ollama 连接失败（{url}）：{e}。请确认 Ollama 已启动（ollama serve）"
            ) from e
        except urllib.error.HTTPError as e:
            raise LLMError(
                f"Ollama HTTP 错误（{e.code}）：{e.read().decode('utf-8', errors='replace')}"
            ) from e
        except json.JSONDecodeError as e:
            raise LLMError(f"Ollama 响应解析失败：{e}") from e

        text = result.get("message", {}).get("content", "")
        usage = {}
        if "usage" in result:
            u = result["usage"]
            usage = {
                "prompt_tokens": u.get("prompt_eval_count", 0),
                "completion_tokens": u.get("eval_count", 0),
                "total_tokens": u.get("prompt_eval_count", 0) + u.get("eval_count", 0),
            }
        # Ollama 兼容返回 choices 格式
        raw = {"choices": [{"message": {"content": text}}], "usage": result.get("usage")}
        return LLMResponse(text=text, usage=usage, model=model, raw=raw)

    def embed(self, texts: list[str]) -> list[list[float]]:
        """委托到 ``agent.core.rag.embeddings.OllamaEmbedding``

        单一实现（避免双份 HTTP 逻辑）；懒导入规避与 ``rag`` 包的循环依赖。
        """
        from agent.core.rag.embeddings import OllamaEmbedding

        provider = OllamaEmbedding(
            model=self.config.embedding_model or self.config.model,
            base_url=self.config.base_url or "http://localhost:11434",
        )
        return provider.embed(texts)


# ============================================================
# Provider 注册表初始化（T-7：单一注册点）
# ============================================================
# 内置 provider 在此统一登记；扩展时只需新增 LLMProvider 子类并追加到此字典，
# create() 不再含 if/else 分支。
LLMProvider.PROVIDERS = {
    "openai": OpenAIProvider,
    "ollama": OllamaProvider,
}


# ============================================================
# LLMClient（保持向后兼容）
# ============================================================
class LLMClient:
    """LLM 客户端（支持多 Provider）

    用法：
        # 默认（openai 兼容协议）
        client = LLMClient()
        resp = client.chat_creative([{"role": "user", "content": "..."}])

        # Ollama 本地部署
        client = LLMClient(config=LLMConfig(provider="ollama", model="qwen2.5"))
        resp = client.chat_creative([{"role": "user", "content": "..."}])

        # .env 配置切换
        # LLM_PROVIDER=ollama
        # LLM_MODEL_ID=qwen2.5
        resp = client.chat_creative(...)
    """

    def __init__(
        self,
        config: LLMConfig | None = None,
        console: "Console | None" = None,
        primary_provider: "LLMProvider | None" = None,
        fallback_provider: "LLMProvider | None" = None,
        env_file: str | None = None,
    ) -> None:
        # D：.env 透传。优先级：显式 env_file 参数 > 环境变量 NOVEL_AGENT_DOTENV > 默认 load_dotenv()
        if config is None:
            resolved_env = env_file or os.environ.get("NOVEL_AGENT_DOTENV")
            self.config = self._load_from_env(env_file=resolved_env)
        else:
            self.config = config
        self.console = console
        # 主 Provider（可直接注入，便于测试）
        self._provider = primary_provider or LLMProvider.create(self.config)
        # 备用 Provider（E1.4 无网络回退），可注入或在需要时按 config 构建
        self._fallback_provider = fallback_provider
        # 回退日志（供测试/观测）
        self.fallback_log: list[str] = []

    @staticmethod
    def _load_from_env(env_file: str | None = None) -> LLMConfig:
        if env_file:
            load_dotenv(env_file)
        else:
            load_dotenv()  # cwd 及祖先目录中的 .env（兼容旧布局：.env 在仓库根）
            # 回退：与代码同目录的 agent/.env（.env 现已随代码归入 agent/）。
            # load_dotenv 默认不覆盖已存在的环境变量，故根目录 .env 仍优先。
            try:
                import agent as _agent_pkg

                _pkg_dir = os.path.dirname(_agent_pkg.__file__)  # .../src/agent
                # .env 可能在：包目录(src/agent/.env)、src 目录、或仓库根(agent/.env)。
                # 兼容 src 布局：.env 放在仓库根、不进包、不随 wheel 发布。
                for _cand in (
                    os.path.join(_pkg_dir, ".env"),
                    os.path.join(os.path.dirname(_pkg_dir), ".env"),
                    os.path.join(os.path.dirname(os.path.dirname(_pkg_dir)), ".env"),
                ):
                    if os.path.exists(_cand):
                        load_dotenv(_cand)
                        break
            except Exception:
                pass

        model = os.getenv("LLM_MODEL_ID", "glm-5.2")
        model_utility = os.getenv("LLM_MODEL_UTILITY", "") or model
        embedding_model = os.getenv("EMBEDDING_MODEL_ID", "") or model
        provider = os.getenv("LLM_PROVIDER", "openai").lower()
        # 路径 B：独立的嵌入端点（可选）。设置后 embedding 与 chat 解耦，
        # 否则在 OpenAIProvider.embed 中回退到 LLM_BASE_URL / LLM_API_KEY。
        embedding_base_url = os.getenv("EMBEDDING_BASE_URL", "")
        embedding_api_key = os.getenv("EMBEDDING_API_KEY", "")

        # 思考开关（仅对支持 reasoning 的模型有效，如 glm-4.7）。
        _et_raw = os.getenv("LLM_ENABLE_THINKING", "").strip().lower()
        enable_thinking: bool | None = None
        if _et_raw in ("false", "0", "no", "off"):
            enable_thinking = False
        elif _et_raw in ("true", "1", "yes", "on"):
            enable_thinking = True

        return LLMConfig(
            provider=provider,
            api_key=os.getenv("LLM_API_KEY", ""),
            base_url=os.getenv("LLM_BASE_URL", ""),
            model=model,
            model_utility=model_utility,
            embedding_model=embedding_model,
            embedding_base_url=embedding_base_url,
            embedding_api_key=embedding_api_key,
            timeout=int(os.getenv("LLM_TIMEOUT", "120")),
            max_retries=int(os.getenv("LLM_MAX_RETRIES", "3")),
            retry_base_delay=float(os.getenv("LLM_RETRY_BASE_DELAY", "1.0")),
            # T-7：LLM_FALLBACK_PROVIDER 支持逗号分隔的多候选回退链
            fallback_providers=[
                p.strip()
                for p in os.getenv("LLM_FALLBACK_PROVIDER", "").split(",")
                if p.strip()
            ],
            fallback_model=os.getenv("LLM_FALLBACK_MODEL", ""),
            enable_thinking=enable_thinking,
        )

    @property
    def provider_name(self) -> str:
        """当前 Provider 名称"""
        return self.config.provider

    @property
    def is_local(self) -> bool:
        """是否为本地 Provider"""
        return self.config.provider == "ollama"

    def preflight(self) -> dict[str, Any]:
        """返回当前 Provider 配置摘要（供 CLI 诊断，不发起网络调用）"""
        return {
            "provider": self.config.provider,
            "model": self.config.model,
            "model_utility": self.config.model_utility or self.config.model,
            "is_local": self.is_local,
            "fallback_provider": self.config.fallback_provider or None,
            "fallback_providers": list(self.config.fallback_providers),
            "fallback_model": self.config.fallback_model or None,
            "has_fallback": self._get_fallback_provider() is not None,
        }

    # ============================================================
    # E1.4 无网络回退
    # ============================================================
    @staticmethod
    def _is_network_error(exc: Exception) -> bool:
        """判断异常是否为网络不可达类错误

        包括：URLError / ConnectionError / TimeoutError，
        以及 OpenAI 等 SDK 抛出的 *ConnectionError / *TimeoutError 子类
        （通过类型名匹配，避免硬依赖 openai 包）。
        """
        if isinstance(exc, (URLError, ConnectionError, TimeoutError)):
            return True
        name = type(exc).__name__
        return "Connection" in name or "Connect" in name or "Timeout" in name

    def _get_fallback_provider(self) -> "LLMProvider | None":
        """按 config.fallback_providers 回退链构建（或返回已注入的）备用 Provider

        T-7：遍历 fallback_providers 列表，返回第一个与主 Provider 不同且可成功构建
        的备用 Provider（其余候选作为回退链后续项，由 chat() 在网络错误时按需尝试）。
        """
        if self._fallback_provider is not None:
            return self._fallback_provider
        for fb_name in self.config.fallback_providers:
            fb_name = fb_name.strip().lower()
            if not fb_name or fb_name == self.config.provider:
                continue
            try:
                self._fallback_provider = LLMProvider.create(
                    LLMConfig(
                        provider=fb_name,
                        api_key=self.config.api_key,
                        base_url=self.config.base_url,
                        model=self.config.fallback_model or self.config.model,
                        model_utility=(
                            self.config.fallback_model
                            or self.config.model_utility
                            or self.config.model
                        ),
                        timeout=self.config.timeout,
                        max_retries=1,  # 回退只尝试一次
                    )
                )
                return self._fallback_provider
            except LLMError:
                # 回退链中某个 provider 不可用，尝试下一个候选
                continue
        return self._fallback_provider

    def _warn_fallback(self, exc: Exception) -> None:
        fb_name = self.config.fallback_providers[0] if self.config.fallback_providers else ""
        if self._fallback_provider is not None and self._fallback_provider.config.provider:
            fb_name = self._fallback_provider.config.provider
        msg = (
            f"主 Provider({self.config.provider}) 不可达：{exc}。"
            f"已回退到备用 Provider({fb_name})"
        )
        self.fallback_log.append(msg)
        if self.console is not None:
            self.console.print(f"[yellow]⚠ {msg}[/yellow]")

    def _select_model(self, model: str | None, use: str) -> str:
        if model:
            return model
        if use == "utility":
            return self.config.model_utility or self.config.model
        return self.config.model

    def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.8,
        max_tokens: int | None = None,
        use: str = "creative",
        enable_thinking: bool | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        target_model = self._select_model(model, use)
        # 配置级默认：调用方未显式指定时，采用 LLMConfig.enable_thinking
        # （由 .env 的 LLM_ENABLE_THINKING 控制，用于关闭思考模型以提升批量写作速度）。
        if enable_thinking is None:
            enable_thinking = self.config.enable_thinking

        last_exc: Exception | None = None
        # 主 Provider 调用（指数退避重试）
        for attempt in range(self.config.max_retries + 1):
            try:
                return self._provider.chat(
                    messages=messages,
                    model=target_model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    enable_thinking=enable_thinking,
                    timeout=self.config.timeout,
                    **kwargs,
                )
            except Exception as e:
                last_exc = e
                # 网络不可达：不再对同一个死端点重试，直接进入回退分支
                if self._is_network_error(e):
                    break
                if attempt < self.config.max_retries:
                    delay = self.config.retry_base_delay * (2 ** attempt)
                    time.sleep(delay)
                    continue
                break

        # E1.4 无网络回退：主 Provider 网络错误且配置了备用 Provider 时自动切换
        fb = self._get_fallback_provider()
        if fb is not None and last_exc is not None and self._is_network_error(last_exc):
            self._warn_fallback(last_exc)
            # 备用 Provider 使用自身模型（创作/校验分工）
            fb_model = fb.config.model_utility or fb.config.model if use == "utility" else fb.config.model
            if not fb_model:
                fb_model = target_model
            try:
                return fb.chat(
                    messages=messages,
                    model=fb_model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    enable_thinking=enable_thinking,
                    timeout=self.config.timeout,
                    **kwargs,
                )
            except Exception as e2:
                last_exc = e2

        raise LLMError(
            f"LLM 调用失败（重试 {self.config.max_retries} 次后仍报错）: {last_exc}"
        ) from last_exc

    def chat_creative(
        self, messages: list[dict[str, str]], **kwargs: Any
    ) -> LLMResponse:
        return self.chat(messages, use="creative", **kwargs)

    def chat_utility(
        self, messages: list[dict[str, str]], **kwargs: Any
    ) -> LLMResponse:
        kwargs.setdefault("temperature", 0.2)
        return self.chat(messages, use="utility", **kwargs)

    def complete(
        self,
        prompt: str,
        system: str | None = None,
        use: str = "creative",
        **kwargs: Any,
    ) -> str:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = self.chat(messages, use=use, **kwargs)
        return resp.text

    def chat_structured(
        self,
        messages: list[dict[str, str]],
        schema: "type[BaseModel] | dict[str, Any]",
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        use: str = "utility",
        name: str = "structured_output",
        strict: bool = False,
        enable_thinking: bool | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """结构化输出：把模型回复约束为给定 JSON Schema（OpenAI 兼容 response_format）。

        首选 ``response_format={"type":"json_schema",...}``；若 provider 不支持
        （抛错），回退为"无 response_format + 文本解析"（extract_json）。

        Args:
            messages: 对话消息
            schema: pydantic 模型类 或 既有 dict JSON Schema
            model / temperature / max_tokens / use: 同 ``chat``
            name: schema 名称（部分端点需要）
            strict: 是否开启 strict 模式（要求 required 全 + additionalProperties=false）
            enable_thinking: 思考开关覆盖

        Returns:
            解析后的 dict。

        Raises:
            StructuredOutputError: 主路径与回退路径均失败。
        """
        json_schema = (
            pydantic_to_json_schema(schema)
            if not isinstance(schema, dict)
            else schema
        )
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": name, "schema": json_schema, "strict": strict},
        }
        try:
            resp = self.chat(
                messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                use=use,
                enable_thinking=enable_thinking,
                response_format=response_format,
                **kwargs,
            )
            return extract_json(resp.text)
        except (LLMError, StructuredOutputError, ValueError) as e:
            # 回退：去掉 response_format 再请求一次，文本解析兜底
            try:
                resp2 = self.chat(
                    messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    use=use,
                    enable_thinking=enable_thinking,
                    **kwargs,
                )
                return extract_json(resp2.text)
            except Exception as e2:  # noqa: BLE001
                raise StructuredOutputError(
                    f"结构化输出失败（含回退）: {e} | {e2}"
                ) from e2

    async def chat_structured_async(
        self,
        messages: list[dict[str, str]],
        schema: "type[BaseModel] | dict[str, Any]",
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        use: str = "utility",
        name: str = "structured_output",
        strict: bool = False,
        enable_thinking: bool | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """``chat_structured`` 的异步包装（线程卸载，不阻塞事件循环）。

        注：当前 Provider 的底层 ``chat`` 为同步实现，这里用 ``asyncio.to_thread``
        卸载到线程，使 Agentic Loop 的 ``run_async`` 可在异步编排中复用同一客户端，
        而无需为每个 Provider 单独实现原生异步 IO（原生异步 Provider 属 Phase 3/4 范畴）。

        Returns / Raises：与 :meth:`chat_structured` 一致。
        """
        return await asyncio.to_thread(
            self.chat_structured,
            messages,
            schema,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            use=use,
            name=name,
            strict=strict,
            enable_thinking=enable_thinking,
            **kwargs,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        """生成文本嵌入向量（RAG 语义检索用）

        复用 ``.env`` 配置（``EMBEDDING_MODEL_ID`` / ``LLM_BASE_URL`` / ``LLM_API_KEY``）。
        若设置独立的 ``EMBEDDING_BASE_URL`` / ``EMBEDDING_API_KEY``（路径 B），则
        embedding 优先走专用端点，与 chat 端点解耦。
        内部委托给当前 Provider 的 ``embed`` 实现（进而到 ``rag.embeddings``）。
        不可达时返回**空列表**，调用方据此降级为 BM25-only 召回，**绝不阻断写章**。

        Args:
            texts: 待嵌入的文本列表

        Returns:
            与 ``texts`` 等长的向量列表；``embed`` 不可达时返回 ``[]``。
        """
        try:
            return self._provider.embed(texts)
        except Exception as e:  # noqa: BLE001 - 嵌入失败不应阻断写作主路径
            if self.console is not None:
                self.console.print(
                    f"[yellow]⚠ embed 失败，降级为无向量召回：{e}[/yellow]"
                )
            return []
