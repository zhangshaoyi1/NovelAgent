"""提示词全文捕获（per-book）：开关存取 + PromptFileConsumer 落盘 + flag 开关"""

from __future__ import annotations

import json

import agent.client.llm_usage as llm_usage
from agent.client.llm_usage import set_llm_capture_prompts
from agent.core.event_sourcing.event_model import Event
from agent.core.event_sourcing.prompt_capture import (
    PromptFileConsumer,
    capture_enabled,
    set_capture_enabled,
)


def _event(**payload) -> Event:
    return Event(type="llm.usage", correlation_id="", payload=payload, context={})


def test_capture_flag_default_off_and_toggle() -> None:
    """client 层捕获 flag 默认关闭；可开关。"""
    try:
        assert llm_usage.llm_capture_prompts() is False
        set_llm_capture_prompts(True)
        assert llm_usage.llm_capture_prompts() is True
        set_llm_capture_prompts(False)
        assert llm_usage.llm_capture_prompts() is False
    finally:
        # 复位，避免污染同进程其他测试
        set_llm_capture_prompts(False)


def test_per_book_config_default_off_roundtrip(tmp_path) -> None:
    """per-book 开关文件读写；缺省关闭。"""
    assert capture_enabled(tmp_path) is False
    ok, msg = set_capture_enabled(tmp_path, True)
    assert ok and msg == "已开启"
    assert capture_enabled(tmp_path) is True
    cfg = json.loads((tmp_path / ".state" / "llmops.json").read_text(encoding="utf-8"))
    assert cfg["capture_prompts"] is True
    ok, _ = set_capture_enabled(tmp_path, False)
    assert ok
    assert capture_enabled(tmp_path) is False


def test_consumer_skips_without_prompt(tmp_path) -> None:
    """无全文的事件不写 prompts.jsonl（保持文件只含已捕获调用）。"""
    consumer = PromptFileConsumer(tmp_path)
    consumer.on_event(_event(ok=True, model="m", tokens_in=1))
    out = tmp_path / ".state" / "llmops" / "prompts.jsonl"
    assert not out.exists()


def test_consumer_writes_record_with_meta(tmp_path) -> None:
    """含全文的事件追加写 prompts.jsonl（含 meta/prompt/response）。"""
    consumer = PromptFileConsumer(tmp_path)
    consumer.on_event(
        _event(
            ok=True,
            provider="p",
            model="m1",
            tokens_in=10,
            tokens_out=5,
            tokens_cached=2,
            latency_ms=123.4,
            use="creative",
            prompt='[{"role":"user","content":"写一章"}]',
            response="这里是正文",
        )
    )
    consumer.on_event(
        _event(ok=True, model="m2", prompt="[second]", response="正文2")
    )
    out = tmp_path / ".state" / "llmops" / "prompts.jsonl"
    lines = [json.loads(ln) for ln in out.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 2
    rec = lines[0]
    assert rec["prompt"] == '[{"role":"user","content":"写一章"}]'
    assert rec["response"] == "这里是正文"
    assert rec["meta"]["model"] == "m1"
    assert rec["meta"]["use"] == "creative"
    assert rec["meta"]["tokens_in"] == 10
    assert rec["meta"]["latency_ms"] == 123.4
    assert rec["id"] and rec["timestamp"]