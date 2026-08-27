"""配置基类工具

提供配置加载的基础能力，各个具体配置继承此基类。
"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv
import os


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
        load_dotenv()
        return cls()

    @staticmethod
    def get_env(key: str, default: Optional[str] = None) -> Optional[str]:
        """读取环境变量

        Args:
            key: 环境变量键
            default: 默认值（如果不存在）

        Returns:
            环境变量值或默认值
        """
        return os.getenv(key, default)
