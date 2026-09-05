"""架构红线测试（agent 包，对应 llmagent/docs/m0-guardrails.md R1–R5）

把 AGENTS.md 声明的分层约束变成"提交即失败"的可检查约束：
- R1  仅 `agent/client/` 与 `llmagent/gateway/` 可 import provider SDK（openai/ollama）
- R2  `agent/base/` 是最底层，不得 import 任何上层包
- R3  业务层（workflows/core/agents）不得触碰 `llmagent.gateway.providers` / `llmagent.gateway.secrets`
- R4  业务层（workflows/core/agents）不得 import `agent.cli` / `agent.web`（依赖只能向下）
- R5  API key 直读仅允许白名单文件（gateway secrets / client 凭据装配 / base 配置 /
      doctor 诊断 / web 模型档案回显），白名单外出现即失败——收紧增量，存量不扩散
"""

from __future__ import annotations

import ast
from pathlib import Path

AGENT_SRC = Path(__file__).resolve().parents[2] / "src" / "agent"

PROVIDER_SDK_MODULES = {"openai", "ollama"}
KEY_ENV_NAMES = {"LLM_API_KEY", "EMBEDDING_API_KEY"}

# R5 白名单：允许直读 API key 的文件（相对 AGENT_SRC 的 posix 路径）
KEY_ACCESS_WHITELIST = {
    "llmagent/gateway/secrets.py",
    "client/provider.py",
    "client/embeddings.py",
    "base/config.py",
    "base/llm.py",
    "core/infra/doctor.py",
    "web/app.py",
}


def _iter_py_files(subdir: str | None = None):
    root = AGENT_SRC / subdir if subdir else AGENT_SRC
    for p in root.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        yield p


def _imports_of(path: Path) -> list[str]:
    """AST 解析一个文件的 import 顶层模块路径列表。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    mods: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.append(node.module)
    return mods


def _rel(path: Path) -> str:
    return path.relative_to(AGENT_SRC).as_posix()


class TestR1ProviderSdkIsolation:
    def test_provider_sdk_only_in_client_and_gateway(self):
        violations = []
        for p in _iter_py_files():
            rel = _rel(p)
            if rel.startswith(("client/", "llmagent/gateway/")):
                continue
            for mod in _imports_of(p):
                top = mod.split(".")[0]
                if top in PROVIDER_SDK_MODULES:
                    violations.append(f"{rel}: import {mod}")
        assert not violations, "provider SDK 只允许 client/ 与 llmagent/gateway/ 引用:\n" + "\n".join(
            violations
        )


class TestR2BaseIsBottomLayer:
    def test_base_imports_no_upper_layer(self):
        upper = ("agent.core", "agent.client", "agent.agents", "agent.workflows",
                 "agent.cli", "agent.web", "agent.service", "agent.tasks",
                 "agent.memory", "agent.session", "agent.skills")
        violations = []
        for p in _iter_py_files("base"):
            for mod in _imports_of(p):
                if any(mod == u or mod.startswith(u + ".") for u in upper):
                    violations.append(f"{_rel(p)}: import {mod}")
        assert not violations, "agent/base 不得 import 上层包:\n" + "\n".join(violations)


class TestR3GatewayInternalsAreSealed:
    def test_business_layer_cannot_touch_gateway_internals(self):
        banned = ("llmagent.gateway.providers", "llmagent.gateway.secrets")
        # gateway_adapter.py 是文档钦定的适配点（把 LLMProvider 适配为 Gateway 的
        # ModelProvider），是 client 层唯一允许触碰 gateway 内部的文件
        allowed = {"client/gateway_adapter.py"}
        violations = []
        for p in _iter_py_files():
            rel = _rel(p)
            if rel.startswith("llmagent/") or rel in allowed:
                continue
            for mod in _imports_of(p):
                if any(mod == b or mod.startswith(b + ".") for b in banned):
                    violations.append(f"{rel}: import {mod}")
        assert not violations, "业务层不得触碰 gateway 内部（providers/secrets）:\n" + "\n".join(
            violations
        )


class TestR4BusinessLayerDoesNotImportUi:
    def test_business_layer_imports_no_cli_or_web(self):
        banned = ("agent.cli", "agent.web")
        # __main__.py 是进程入口，组装 CLI 属于其职责
        allowed = {"__main__.py"}
        violations = []
        for p in _iter_py_files():
            rel = _rel(p)
            if rel.startswith(("cli/", "web/")) or rel in allowed:
                continue
            for mod in _imports_of(p):
                if any(mod == b or mod.startswith(b + ".") for b in banned):
                    violations.append(f"{rel}: import {mod}")
        assert not violations, "业务层不得 import cli/web（方向只能向下）:\n" + "\n".join(violations)


class TestR5KeyAccessWhitelist:
    def test_key_reads_confined_to_whitelist(self):
        violations = []
        for p in _iter_py_files():
            rel = _rel(p)
            if rel in KEY_ACCESS_WHITELIST:
                continue
            try:
                src = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for name in KEY_ENV_NAMES:
                if name in src:
                    violations.append(f"{rel}: 引用了 {name}")
        assert not violations, "API key 直读超出白名单（新文件请走 client/gateway_adapter）:\n" + "\n".join(
            violations
        )
