"""Phase 4 · 离线测试（生态与强化）

覆盖三块核心：
- MCPBridge（配置驱动 / MockTransport discover+call / 本地 manifest 暴露 / 优雅降级 / 不可用服务器调用）
- ModelRouter（creative→强模型 / utility→廉价 / 成功率熔断剔除 / 失败后回退 / 全 tripped 兜底）
- Guardrails（禁用词 / 超长 / 占位残留 / 空 / schema 缺失与通过 / enforce 抛错 / warn 不阻断）

全部零网络、零真实 LLM，纯规则与内存 transport。
"""

from __future__ import annotations

from agent.core.quality.guardrails import (
    GuardrailViolationError,
    Guardrails,
    GuardrailResult,
)
from agent.client import ModelRouter, RouteCandidate
from agent.core.tools.base import ToolResult
from agent.core.tools.mcp_bridge import (
    HTTPTransport,
    MCPBridge,
    MCPServerConfig,
    MockTransport,
    StdioTransport,
)


# ============================================================
# 1. MCPBridge
# ============================================================
class _FakeRegistry:
    """自包含本地注册表，避免污染全局 builtins 注册。"""

    def manifests(self):
        return [
            {"name": "local_a", "description": "本地工具A", "inputSchema": {}},
            {"name": "local_b", "description": "本地工具B", "inputSchema": {}},
        ]

    def call(self, name, **kwargs):
        return ToolResult(success=True, data={"name": name, "args": kwargs})


def _mock_transport(name="web_search", tools=None, handler=None):
    cfg = MCPServerConfig(name=name, transport="mock", enabled=True)
    return MockTransport(
        cfg,
        tools=tools or {"search": {"description": "搜索", "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}}}},
        handler=handler or {},
    )


def test_mcp_bridge_discover_and_call():
    reg = _FakeRegistry()
    mock = _mock_transport("web_search")
    bridge = MCPBridge(
        config={"servers": {"web_search": {"transport": "mock", "enabled": True}}},
        registry=reg,
        transports={"web_search": mock},
    )
    status = bridge.discover()
    assert status["web_search"]["status"] == "available"
    assert "web_search" in bridge.available_servers()

    # 远程调用（带 mcp__ 前缀）
    res = bridge.call("mcp__web_search__search", {"q": "三国"})
    assert res.success is True
    assert res.data == {"echo": {"q": "三国"}, "tool": "search"}


def test_mcp_bridge_local_manifest_and_combined():
    reg = _FakeRegistry()
    mock = _mock_transport("web_search")
    bridge = MCPBridge(
        config={"servers": {"web_search": {"transport": "mock", "enabled": True}}},
        registry=reg,
        transports={"web_search": mock},
    )
    bridge.discover()
    local = bridge.local_manifest()
    assert {t["name"] for t in local} == {"local_a", "local_b"}
    combined = bridge.combined_manifest()
    names = {t["name"] for t in combined}
    assert "local_a" in names
    assert "mcp__web_search__search" in names


def test_mcp_bridge_local_call_routes_to_registry():
    reg = _FakeRegistry()
    bridge = MCPBridge(config={}, registry=reg)  # 无远程服务器
    res = bridge.call("local_a", {"x": 1})
    assert res.success is True
    assert res.data == {"name": "local_a", "args": {"x": 1}}


def test_mcp_bridge_graceful_degradation_on_unavailable():
    reg = _FakeRegistry()
    # stdio 无命令 + http 不可达 + 禁用服务器 + 一个 mock 可用
    bridge = MCPBridge(
        config={
            "servers": {
                "broken_stdio": {"transport": "stdio", "enabled": True},  # 无命令 → unavailable
                "broken_http": {                                       # 不可达 → unavailable
                    "transport": "http",
                    "url": "http://127.0.0.1:1/mcp",
                    "enabled": True,
                },
                "disabled_srv": {"transport": "mock", "enabled": False},
                "ok_srv": {"transport": "mock", "enabled": True},
            }
        },
        registry=reg,
        transports={"ok_srv": _mock_transport("ok_srv")},
    )
    status = bridge.discover()
    assert status["broken_stdio"]["status"] == "unavailable"
    assert status["broken_http"]["status"] == "unavailable"
    assert status["disabled_srv"]["status"] == "disabled"
    assert status["ok_srv"]["status"] == "available"
    # 本地工具恒可用 → bridge 健康，不阻断
    assert bridge.is_healthy() is True


def test_mcp_bridge_call_unavailable_server_fails():
    reg = _FakeRegistry()
    bridge = MCPBridge(
        config={"servers": {"broken_stdio": {"transport": "stdio", "enabled": True}}},
        registry=reg,
    )
    bridge.discover()
    res = bridge.call("mcp__broken_stdio__anything", {})
    assert res.success is False
    assert "不可用" in res.error


def test_mcp_bridge_illegal_remote_name():
    bridge = MCPBridge(config={}, registry=_FakeRegistry())
    res = bridge.call("mcp__nope", {})
    assert res.success is False
    assert "非法" in res.error


def test_mcp_bridge_save_and_load_config(tmp_path):
    cfg_file = tmp_path / "mcp.json"
    bridge = MCPBridge(
        config={"servers": {"s1": {"transport": "mock", "enabled": True}}}
    )
    bridge.save_config(cfg_file)
    assert cfg_file.exists()
    bridge2 = MCPBridge(config_path=cfg_file)
    assert "s1" in bridge2._servers
    assert bridge2._servers["s1"].transport == "mock"


# ============================================================
# 2. ModelRouter
# ============================================================
def _router():
    return ModelRouter(
        candidates=[
            RouteCandidate(name="strong", model_id="m-strong", provider="openai",
                           use="creative", priority=1, cost_per_1k=0.02),
            RouteCandidate(name="mid", model_id="m-mid", provider="openai",
                           use="creative", priority=5, cost_per_1k=0.01),
            RouteCandidate(name="cheap", model_id="m-cheap", provider="openai",
                           use="utility", priority=2, cost_per_1k=0.001),
            RouteCandidate(name="cheap2", model_id="m-cheap2", provider="ollama",
                           use="utility", priority=4, cost_per_1k=0.0),
        ],
        circuit_breaker_threshold=0.5,
        min_samples=3,
    )


def test_router_creative_picks_strong():
    r = _router()
    d = r.route("creative")
    assert d.model_id == "m-strong"
    assert d.provider == "openai"
    assert d.source == "selected"


def test_router_utility_picks_cheapest():
    r = _router()
    d = r.route("utility")
    # cheap2 成本 0.0 最低（本地），故应选 cheap2
    assert d.model_id == "m-cheap2"
    assert d.provider == "ollama"


def test_router_circuit_breaker_trips_and_falls_back():
    r = _router()
    # 让 strong 连续失败（>= min_samples）触发熔断
    for _ in range(4):
        r.record_failure("m-strong")
    assert r.is_tripped("m-strong") is True
    # creative 路由应回退到下一个可用强模型 mid
    d = r.route("creative")
    assert d.model_id == "m-mid"
    assert d.source == "selected"


def test_router_all_tripped_falls_back_to_best_effort():
    r = _router()
    for m in ("m-strong", "m-mid"):
        for _ in range(4):
            r.record_failure(m)
    # 全部 creative 候选熔断 → 忽略熔断，退回最高 priority（strong）
    d = r.route("creative")
    assert d.model_id == "m-strong"
    assert d.tripped is True
    assert d.source == "fallback"


def test_router_report_reflects_stats():
    r = _router()
    r.record_success("m-strong")
    r.record_failure("m-strong")
    rep = r.report()
    assert rep["stats"]["m-strong"]["success"] == 1
    assert rep["stats"]["m-strong"]["fail"] == 1
    assert abs(rep["stats"]["m-strong"]["success_rate"] - 0.5) < 1e-9


# ============================================================
# 3. Guardrails
# ============================================================
def test_guardrails_empty_text_fails():
    g = Guardrails()
    res = g.check("   ")
    assert res.passed is False
    assert any(v.rule_id == "empty" for v in res.errors)


def test_guardrails_banned_word_fails():
    g = Guardrails(banned_words=["违规词X"])
    res = g.check("这是一段包含违规词X的内容")
    assert res.passed is False
    assert any(v.rule_id == "banned_word" for v in res.errors)


def test_guardrails_too_long_fails():
    g = Guardrails(max_chars=10)
    res = g.check("这一章写得非常非常长，远超十字符的上限限制条件")
    assert res.passed is False
    assert any(v.rule_id == "too_long" for v in res.errors)


def test_guardrails_placeholder_fails():
    g = Guardrails()
    res = g.check("主角出发了。[TODO] 这里后面要补战斗场面。")
    assert res.passed is False
    assert any(v.rule_id == "placeholder" for v in res.errors)


def test_guardrails_clean_text_passes():
    g = Guardrails(max_chars=100000, check_title=False)
    res = g.check("林轩推开沉重的大门，月光洒在他的肩头。")
    assert res.passed is True
    assert res.errors == []


def test_guardrails_too_short_is_warn_not_error():
    g = Guardrails(min_chars=1000, check_title=False)
    res = g.check("短章。")
    # 仅 warn → 默认 allow_warnings → passed 仍为 True
    assert res.passed is True
    assert any(v.rule_id == "too_short" for v in res.warnings)


def test_guardrails_schema_missing_field_fails():
    g = Guardrails()
    res = g.check('{"title": "x"}', required_fields=["title", "body"])
    assert res.passed is False
    assert any(v.rule_id == "missing_field" for v in res.errors)


def test_guardrails_schema_present_passes():
    g = Guardrails(check_title=False, check_junk=False)
    res = g.check('{"title": "x", "body": "y"}', required_fields=["title", "body"])
    assert res.passed is True


def test_guardrails_enforce_raises_on_failure():
    g = Guardrails(banned_words=["BAD"])
    try:
        g.enforce("含有 BAD 的章节")
        assert False, "应当抛出 GuardrailViolationError"
    except GuardrailViolationError as e:
        assert e.result.passed is False


def test_guardrails_enforce_ok_returns_result():
    g = Guardrails(max_chars=100000, check_title=False)
    res = g.enforce("正常的章节正文内容。")
    assert isinstance(res, GuardrailResult)
    assert res.passed is True
