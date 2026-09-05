"""配置基类工具与统一配置加载器

提供配置加载的基础能力，以及全局统一的 ``ConfigLoader``（集中管理 .env 搜索、
缓存、LLMConfig 构建）。所有需要读取 .env 的模块应使用 ``ConfigLoader``，
禁止直接调用 ``load_dotenv()`` 或 ``os.getenv("LLM_*")``。
"""

from __future__ import annotations

import os
from abc import ABC
from dataclasses import dataclass
from typing import Any, Optional

from dotenv import load_dotenv


# =============================================================================
# 统一配置加载器（唯一 .env 入口）
# =============================================================================


class ConfigLoader:
    """统一配置加载器

    集中管理 .env 文件搜索、加载与缓存，是全局唯一的 ``load_dotenv`` 入口。

    用法::

        from agent.base.config import ConfigLoader

        config = ConfigLoader.get_llm_config()
        gateway = create_gateway(config)

    特性:
    - **幂等**：首次调用后缓存，后续调用零开销
    - **统一搜索路径**：从包目录向上搜索 .env
    - **线程安全**：加载完成后才返回
    """

    _loaded: bool = False
    _env_file: str | None = None

    @classmethod
    def load(cls, env_file: str | None = None) -> None:
        """加载 .env 文件（幂等，只加载一次）

        Args:
            env_file: 显式指定 .env 路径。为 ``None`` 时自动搜索。
        """
        if cls._loaded:
            return
        cls._env_file = env_file

        if env_file:
            load_dotenv(env_file, override=False)
        else:
            # 默认加载当前目录 .env
            load_dotenv(override=False)
            # 从包目录向上搜索
            try:
                import agent as _agent_pkg

                _pkg_dir = os.path.dirname(_agent_pkg.__file__)
                for _cand in (
                    os.path.join(_pkg_dir, ".env"),
                    os.path.join(os.path.dirname(_pkg_dir), ".env"),
                    os.path.join(os.path.dirname(os.path.dirname(_pkg_dir)), ".env"),
                ):
                    if os.path.exists(_cand):
                        load_dotenv(_cand, override=False)
                        break
            except Exception:
                pass

        cls._loaded = True

    @classmethod
    def get_llm_config(cls, env_file: str | None = None) -> "LLMConfig":
        """返回 ``LLMConfig`` 实例（首次调用自动加载 .env）

        Args:
            env_file: 可选，显式指定 .env 路径。

        Returns:
            填充了环境变量值的 ``LLMConfig`` 实例。
        """
        cls.load(env_file)
        return _build_llm_config_from_env()

    @classmethod
    def reset(cls) -> None:
        """重置加载状态（测试用）"""
        cls._loaded = False
        cls._env_file = None


def _build_llm_config_from_env() -> "LLMConfig":
    """构建 LLMConfig（模型档案优先，.env 兜底）

    解析优先级：
        1. NOVEL_MODEL_PROFILE 环境变量指定的模型档案（Web 端按次运行指定）
        2. 档案库（models.json）中激活的档案（Web UI「设为默认」）
        3. 纯环境变量 / .env（原有行为，向后兼容）
    档案未填的字段逐项回退到环境变量值；无档案时行为与旧版完全一致。
    """
    from agent.base.llm import LLMConfig
    from agent.base import model_profiles

    profile: dict | None = None
    try:
        profile = model_profiles.resolve_profile()
    except Exception:  # noqa: BLE001 - 档案库异常降级走 env，不阻断
        profile = None

    model = os.getenv("LLM_MODEL_ID", "glm-5.2")
    model_utility = os.getenv("LLM_MODEL_UTILITY", "") or model
    embedding_model = os.getenv("EMBEDDING_MODEL_ID", "") or model
    provider = os.getenv("LLM_PROVIDER", "openai").lower()
    embedding_base_url = os.getenv("EMBEDDING_BASE_URL", "")
    embedding_api_key = os.getenv("EMBEDDING_API_KEY", "")
    embedding_provider = os.getenv("EMBEDDING_PROVIDER", "").lower()

    _et_raw = os.getenv("LLM_ENABLE_THINKING", "").strip().lower()
    enable_thinking: bool | None = None
    if _et_raw in ("false", "0", "no", "off"):
        enable_thinking = False
    elif _et_raw in ("true", "1", "yes", "on"):
        enable_thinking = True

    api_key = os.getenv("LLM_API_KEY", "")
    base_url = os.getenv("LLM_BASE_URL", "")
    timeout = int(os.getenv("LLM_TIMEOUT", "120"))
    max_retries = int(os.getenv("LLM_MAX_RETRIES", "3"))

    # 模型档案覆盖（仅覆盖档案中显式填写的字段，空字段保持 env 值）
    if profile:
        if profile.get("provider"):
            provider = str(profile["provider"]).lower()
        if profile.get("api_key"):
            api_key = profile["api_key"]
        if profile.get("base_url"):
            base_url = profile["base_url"]
        if profile.get("model"):
            model = profile["model"]
        if profile.get("enable_thinking") is not None:
            enable_thinking = bool(profile["enable_thinking"])
        if profile.get("timeout"):
            timeout = int(profile["timeout"])
        if profile.get("max_retries") is not None and str(profile.get("max_retries")).strip() != "":
            max_retries = int(profile["max_retries"])

    return LLMConfig(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        model=model,
        model_utility=model_utility,
        embedding_model=embedding_model,
        embedding_base_url=embedding_base_url,
        embedding_api_key=embedding_api_key,
        embedding_provider=embedding_provider,
        timeout=timeout,
        max_retries=max_retries,
        retry_base_delay=float(os.getenv("LLM_RETRY_BASE_DELAY", "1.0")),
        fallback_providers=[
            p.strip()
            for p in os.getenv("LLM_FALLBACK_PROVIDER", "").split(",")
            if p.strip()
        ],
        fallback_model=os.getenv("LLM_FALLBACK_MODEL", ""),
        enable_thinking=enable_thinking,
    )


# =============================================================================
# 旧 BaseConfig（保持向后兼容）
# =============================================================================


@dataclass
class BaseConfig(ABC):
    """配置基类

    提供从环境变量加载配置的基础能力。
    具体配置类继承此类，添加自己的字段。
    """

    @classmethod
    def from_env(cls) -> "BaseConfig":
        """从环境变量加载配置

        子类应该覆盖此方法，读取对应环境变量并返回实例。
        """
        ConfigLoader.load()
        return cls()

    @staticmethod
    def get_env(key: str, default: Optional[str] = None) -> Optional[str]:
        """读取环境变量"""
        ConfigLoader.load()
        return os.getenv(key, default)
