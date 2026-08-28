"""MCP 桥接（Phase 4 · Tool / MCP 层）

把 NovelAgent 接入外部 MCP 生态（Web 搜索、代码执行、浏览器等），并把**本地内置工具**
以 MCP 兼容 manifest 暴露出去，构成「本地 + 远程」统一工具面，供 Agentic Loop 调用。

设计要点（与项目「最大化复用、降级不阻断」原则一致）：
- **配置驱动**：MCP 服务器清单来自 dict / JSON 配置（``.state/mcp.json`` 或显式传入），
  每项含 transport（stdio / http / mock）、启用开关与连接参数。
- **优雅降级**：``mcp`` SDK 缺失、或某服务器不可达 / 握手失败时，标记为 ``unavailable``，
  本地工具照常工作，**绝不阻断写作主路径**（与 RAG / embedding 降级策略一致）。
- **可插拔 transport**：真实连接（stdio / http）在 ``connect()`` 时才尝试；测试用
  ``MockTransport`` 内存实现，无需网络即可验证 discover / call 全流程。
- **复用 Phase 0**：本地工具经 ``Tool.to_mcp_manifest`` 导出为 MCP tool 描述
  （``agent.core.engine.tool_contracts``）。

注：``genre_pack.mount_mcp`` 仍是**题材级 v2 钩子**（保留 NotImplemented 占位），本模块是
Tool / MCP 层的通用实现，二者不冲突——题材包可后续内部调用本桥接器。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from agent.core.engine.tool_contracts import ToolResult


# ============================================================
# 配置 / 数据结构
# ============================================================
@dataclass
class MCPServerConfig:
    """单个 MCP 服务器配置。"""

    name: str
    transport: str = "stdio"          # stdio | http | mock
    command: list[str] = field(default_factory=list)   # stdio：启动命令
    url: str = ""                     # http：端点
    headers: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    timeout: float = 30.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "transport": self.transport,
            "command": self.command,
            "url": self.url,
            "headers": self.headers,
            "enabled": self.enabled,
            "timeout": self.timeout,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MCPServerConfig":
        return cls(
            name=d.get("name", ""),
            transport=d.get("transport", "stdio"),
            command=list(d.get("command", []) or []),
            url=d.get("url", "") or "",
            headers=dict(d.get("headers", {}) or {}),
            enabled=bool(d.get("enabled", True)),
            timeout=float(d.get("timeout", 30.0)),
        )


@dataclass
class MCPTool:
    """一个 MCP 工具描述（统一本地 / 远程）。"""

    name: str                       # 完整可调用名（远程带 mcp__{server}__ 前缀）
    raw_name: str                   # 服务端原始工具名
    server: str = ""                # 远程服务器名（本地为空）
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)

    def to_manifest(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


# ============================================================
# Transport（可插拔；真实连接懒加载，失败优雅降级）
# ============================================================
class BaseTransport:
    """Transport 抽象：连接 / 列工具 / 调工具。"""

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self.connected = False
        self.status: str = "idle"          # idle | available | unavailable
        self.status_reason: str = ""

    def connect(self) -> bool:
        """返回是否成功连接；失败置 status=unavailable 并填 reason。"""
        raise NotImplementedError

    def list_tools(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        raise NotImplementedError


class MockTransport(BaseTransport):
    """内存 transport（测试 / 无网络自包含）。

    通过 ``tools``（{name: {"description","inputSchema"}}）与 ``handler``
    （name -> callable）模拟一个 MCP 服务器，无需真实 SDK / 网络。
    """

    def __init__(
        self,
        config: MCPServerConfig,
        tools: dict[str, dict[str, Any]] | None = None,
        handler: dict[str, Callable[..., Any]] | None = None,
    ) -> None:
        super().__init__(config)
        self._tools = tools or {}
        self._handler = handler or {}

    def connect(self) -> bool:
        self.connected = True
        self.status = "available"
        self.status_reason = "mock"
        return True

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": n,
                "description": t.get("description", ""),
                "inputSchema": t.get("inputSchema", {"type": "object", "properties": {}}),
            }
            for n, t in self._tools.items()
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if name not in self._tools:
            raise KeyError(f"Mock MCP 无此工具：{name}")
        fn = self._handler.get(name)
        if fn is None:
            return {"echo": arguments, "tool": name}
        return fn(arguments)


class StdioTransport(BaseTransport):
    """stdio transport（真实 MCP server 子进程）。

    仅当 ``mcp`` SDK 可用时尝试建立连接；否则优雅降级为 unavailable。
    真实子进程拉起与 stdio 协议交互较重，这里**只做连接探测 + 工具清单获取**，
    不实现完整的 stdio 编解码（生产接入时按官方 SDK 补全）。
    """

    def connect(self) -> bool:
        try:
            import importlib.util  # noqa: F401

            if importlib.util.find_spec("mcp") is None:
                self.status = "unavailable"
                self.status_reason = "mcp SDK 未安装（pip install mcp）"
                return False
        except Exception as e:  # noqa: BLE001
            self.status = "unavailable"
            self.status_reason = f"探测失败：{e}"
            return False
        # SDK 存在：真实连接逻辑（启动子进程 + 初始化握手）在生产补全；
        # 当前若未提供就绪的 command，视为不可用，避免拉起未知进程。
        if not self.config.command:
            self.status = "unavailable"
            self.status_reason = "stdio 未配置启动命令"
            return False
        self.status = "unavailable"
        self.status_reason = "stdio 真实连接需 mcp SDK 运行时（生产补全）"
        return False

    def list_tools(self) -> list[dict[str, Any]]:
        return []

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        raise RuntimeError("StdioTransport 未建立真实连接")


class HTTPTransport(BaseTransport):
    """http transport（远程 MCP 端点）。

    真实 GET / POST 交互在生产补全；当前**只做可达性探测**，不通则优雅降级。
    """

    def connect(self) -> bool:
        if not self.config.url:
            self.status = "unavailable"
            self.status_reason = "http 未配置 url"
            return False
        try:
            import urllib.request

            req = urllib.request.Request(self.config.url, method="GET")
            with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:  # noqa: S310
                if resp.status < 400:
                    self.status = "available"
                    self.status_reason = "http 可达（工具清单需生产补全）"
                    self.connected = True
                    return True
                self.status = "unavailable"
                self.status_reason = f"http 返回 {resp.status}"
                return False
        except Exception as e:  # noqa: BLE001 - 不可达即降级
            self.status = "unavailable"
            self.status_reason = f"http 不可达：{e}"
            return False

    def list_tools(self) -> list[dict[str, Any]]:
        # 生产：调用 MCP tools/list；当前返回空（清单获取需 SDK）
        return []

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        raise RuntimeError("HTTPTransport 未建立真实连接")


def _build_transport(config: MCPServerConfig) -> BaseTransport:
    if config.transport == "mock":
        return MockTransport(config)
    if config.transport == "http":
        return HTTPTransport(config)
    return StdioTransport(config)


# ============================================================
# MCPBridge（统一工具面）
# ============================================================
class MCPBridge:
    """MCP 桥接器：聚合本地工具 + 远程 MCP 服务器，提供统一发现与调用。

    Args:
        config: 服务器清单 dict（见 ``MCPServerConfig``），可含 ``servers`` 键。
        config_path: 配置 JSON 路径（``.state/mcp.json``），与 ``config`` 二选一。
        registry: 本地 ``ToolRegistry``（默认用全局 ``agent.core.engine.tool_contracts.registry``）。
        transports: 测试注入的预建 transport（server 名 -> BaseTransport）。
    """

    REMOTE_PREFIX = "mcp__"

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        config_path: str | Path | None = None,
        registry: Any = None,
        transports: dict[str, BaseTransport] | None = None,
    ) -> None:
        self._servers: dict[str, MCPServerConfig] = {}
        self._transports: dict[str, BaseTransport] = {}
        self._server_status: dict[str, dict[str, Any]] = {}
        self._server_tools: dict[str, list[MCPTool]] = {}

        if registry is None:
            from agent.core.engine.tool_contracts import registry as _reg

            registry = _reg
        self.registry = registry

        if config_path is not None and config is None:
            p = Path(config_path)
            if p.exists():
                try:
                    config = json.loads(p.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    config = None
        if config:
            self.load_config(config)

        # 注入的 transport（测试 / 预建连接）需在 load_config 之后设置，
        # 否则会被 load_config 的「配置变更重置」清空。
        self._transports = dict(transports or {})

    # ---------------------------------------------------------------- 配置
    def load_config(self, config: dict[str, Any]) -> None:
        servers = config.get("servers", config) if isinstance(config, dict) else {}
        for name, sc in servers.items():
            if isinstance(sc, MCPServerConfig):
                sc.name = sc.name or name
                self._servers[name] = sc
            elif isinstance(sc, dict):
                sc = dict(sc)
                sc["name"] = sc.get("name") or name
                self._servers[name] = MCPServerConfig.from_dict(sc)
        # 重置已建 transport（配置变更需重连）
        self._transports = {}

    def register_server(self, config: MCPServerConfig | dict[str, Any]) -> None:
        if isinstance(config, dict):
            config = MCPServerConfig.from_dict(config)
        self._servers[config.name] = config
        self._transports.pop(config.name, None)

    def save_config(self, path: str | Path) -> None:
        data = {"servers": {n: c.to_dict() for n, c in self._servers.items()}}
        Path(path).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ---------------------------------------------------------------- 发现
    def discover(self) -> dict[str, dict[str, Any]]:
        """尝试连接所有 enabled 服务器，填充状态与工具清单。返回服务器状态表。"""
        self._server_status = {}
        self._server_tools = {}
        for name, cfg in self._servers.items():
            if not cfg.enabled:
                self._server_status[name] = {"status": "disabled", "reason": "未启用"}
                continue
            transport = self._transports.get(name) or _build_transport(cfg)
            self._transports[name] = transport
            try:
                ok = transport.connect()
            except Exception as e:  # noqa: BLE001 - 任何异常都降级
                ok = False
                transport.status = "unavailable"
                transport.status_reason = f"连接异常：{e}"
            if ok:
                self._server_status[name] = {
                    "status": transport.status,
                    "reason": transport.status_reason,
                }
                try:
                    raw = transport.list_tools()
                except Exception:  # noqa: BLE001
                    raw = []
                self._server_tools[name] = [
                    MCPTool(
                        name=f"{self.REMOTE_PREFIX}{name}__{t['name']}",
                        raw_name=t["name"],
                        server=name,
                        description=t.get("description", ""),
                        input_schema=t.get(
                            "inputSchema", {"type": "object", "properties": {}}
                        ),
                    )
                    for t in raw
                ]
            else:
                self._server_status[name] = {
                    "status": transport.status or "unavailable",
                    "reason": transport.status_reason,
                }
                self._server_tools[name] = []
        return self._server_status

    # ---------------------------------------------------------------- 查询
    @property
    def servers(self) -> dict[str, dict[str, Any]]:
        return self._server_status

    def available_servers(self) -> list[str]:
        return [
            n
            for n, s in self._server_status.items()
            if s.get("status") == "available"
        ]

    def local_manifest(self) -> list[dict[str, Any]]:
        """本地内置工具的 MCP manifest（复用 Phase 0 ``Tool.to_mcp_manifest``）。"""
        return self.registry.manifests()

    def remote_manifest(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for tools in self._server_tools.values():
            out.extend(t.to_manifest() for t in tools)
        return out

    def combined_manifest(self) -> list[dict[str, Any]]:
        """本地 + 远程统一工具清单（供 Agentic Loop 的 tool 列表）。"""
        return self.local_manifest() + self.remote_manifest()

    # ---------------------------------------------------------------- 调用
    def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """统一调用：远程名（``mcp__server__tool``）走 transport，本地名走 registry。"""
        if name.startswith(self.REMOTE_PREFIX):
            parts = name[len(self.REMOTE_PREFIX):].split("__", 1)
            if len(parts) != 2:
                return ToolResult(success=False, error=f"非法远程工具名：{name}")
            server, tool = parts
            transport = self._transports.get(server)
            status = self._server_status.get(server, {}).get("status")
            if transport is None or status != "available":
                reason = self._server_status.get(server, {}).get("reason", "未连接")
                return ToolResult(
                    success=False, error=f"MCP 服务器 {server} 不可用：{reason}"
                )
            try:
                data = transport.call_tool(tool, arguments)
                return ToolResult(success=True, data=data)
            except Exception as e:  # noqa: BLE001
                return ToolResult(success=False, error=f"{type(e).__name__}: {e}")
        # 本地工具
        return self.registry.call(name, **arguments)

    def is_healthy(self) -> bool:
        """是否至少有一个可用工具源（本地恒可用；远程可选）。"""
        return len(self.local_manifest()) > 0 or len(self.available_servers()) > 0
