"""每本书的 LLM 提示词全文捕获（Prompt Capture）

把「每次发给 LLM 的 chat 输入 + 输出全文」按书落盘，供复盘/调试。默认关闭。

## 数据流（与既有事件链路正交叠加，不改变存量行为）

1. client 层埋点（``gateway_adapter``）：仅当进程捕获开关打开时，在用量事件
   payload 里附带 ``prompt``（messages 序列化）与 ``response``（模型输出）。
2. 本模块 ``PromptFileConsumer`` 消费 ``llm.usage`` 事件：若 payload 含全文，
   追加写 `<book>/.state/llmops/prompts.jsonl`（JSONL，每条含元数据 + 全文）。

默认（开关关闭）时既不附带全文、也不注册消费者 ⇒ events.jsonl / trace.jsonl
内容保持精简，零额外开销。

## per-book 开关存储

``<book>/.state/llmops.json`` 的 ``capture_prompts`` 布尔，默认 false。
``AgentService`` 初始化时读该开关：开启才 ``set_llm_capture_prompts(True)``
并注册本消费者；关闭为 no-op。Web 端「每本书」开关读写同一文件。
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.core.event_sourcing.event_consumer import EventConsumer
from agent.core.event_sourcing.event_model import Event

# 全文落地文件（相对项目目录）
_PROMPTS_REL = Path(".state") / "llmops" / "prompts.jsonl"
# per-book 开关文件（相对项目目录）
_CONFIG_REL = Path(".state") / "llmops.json"

_WRITE_LOCK = threading.Lock()


# ============================================================
# per-book 开关存取（Web 与 AgentService 共用）
# ============================================================

def capture_config_path(project_dir: str | Path) -> Path:
    return Path(project_dir) / _CONFIG_REL


def capture_enabled(project_dir: str | Path) -> bool:
    """读取当前书是否开启全文捕获（缺失/损坏降级为 False，绝不阻断）。"""
    try:
        data = json.loads(capture_config_path(project_dir).read_text(encoding="utf-8"))
        return bool(data.get("capture_prompts", False))
    except Exception:  # noqa: BLE001 - 配置读取失败降级为关闭
        return False


def set_capture_enabled(project_dir: str | Path, enabled: bool) -> tuple[bool, str]:
    """写入当前书的全文捕获开关。

    Returns:
        (ok, message)
    """
    path = capture_config_path(project_dir)
    with _WRITE_LOCK:
        try:
            data: dict[str, Any] = {}
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8")) or {}
                except (OSError, ValueError):
                    data = {}  # noqa: SILENT_DEGRADE - 损坏则重建
            data["capture_prompts"] = bool(enabled)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(path)
        except OSError as e:
            return False, f"保存失败：{e}"
    return True, "已开启" if enabled else "已关闭"


# ============================================================
# 消费者：全文 → prompts.jsonl
# ============================================================

class PromptFileConsumer(EventConsumer):
    """把含全文的 ``llm.usage`` 事件追加写入 prompts.jsonl（JSONL）。

    仅在 per-book 开关开启且 client 层真正附带全文时才写；无全文事件静默跳过。
    写盘失败绝不阻断业务（降级不阻断哲学）。
    """

    def __init__(self, project_dir: str | Path) -> None:
        self._pdir = Path(project_dir)
        self._file = self._pdir / _PROMPTS_REL

    @property
    def name(self) -> str:
        return "prompt_file"

    def handles(self, event_type: str) -> bool:
        return event_type == "llm.usage"

    def on_event(self, event: Event) -> None:
        payload = event.payload or {}
        if not payload.get("prompt"):
            # 未开启捕获 / 无全文：不写（保持 prompts.jsonl 只含已捕获调用）。
            return
        record = {
            "id": uuid.uuid4().hex,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "meta": {
                "provider": payload.get("provider", ""),
                "model": payload.get("model", ""),
                "tokens_in": int(payload.get("tokens_in", 0) or 0),
                "tokens_out": int(payload.get("tokens_out", 0) or 0),
                "tokens_cached": int(payload.get("tokens_cached", 0) or 0),
                "latency_ms": float(payload.get("latency_ms", 0) or 0),
                "ok": bool(payload.get("ok", True)),
                "error": payload.get("error", ""),
                "use": payload.get("use", "chat"),
            },
            "prompt": payload.get("prompt", ""),
            "response": payload.get("response"),
        }
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            with _WRITE_LOCK:
                with self._file.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                    fh.flush()
        except Exception:  # noqa: BLE001 - 全文落盘失败不阻断业务
            return


__all__ = [
    "PromptFileConsumer",
    "capture_config_path",
    "capture_enabled",
    "set_capture_enabled",
]