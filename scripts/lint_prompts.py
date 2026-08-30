#!/usr/bin/env python3
"""Prompt-migration lint gate (Phase B, 见 项目文档/提示词管理方案设计.md §9).

目的
----
阶段 B 把 `agent/prompts.py` 中全部提示词常量迁到 `agent/prompts/*.md`，
由 `PromptManager`（`agent/core/infra/prompt_manager.py`，单例 `pm`）统一加载。
调用方必须改用 `pm.get(key).render_system/render_user(...)`，禁止再直接
`from agent.prompts import <常量>` 或裸引用已迁移常量名。

本脚本就是 CI 卡口：扫描仓库内全部 `*.py`（豁免 `prompts.py` 与
`prompt_manager.py` 自身），若存在对已迁移常量的裸引用则失败退出（exit 1）。

denylist 来源
------------
直接解析 `prompt_manager.py` 里所有 `_register(name, module, sys_attr, usr_attr)`
调用中的大写蛇形属性名——即"已被迁移"的常量集合。这样卡口与迁移清单自动同步：
新增 `_register` 自动纳入管控；从 `_register` 移除则自动放行。

白名单
------
`prompts.py` 中以 `def format_*(...)` 形式存在的助手函数（format_learnings /
format_open_debts / format_rag_context 等）不是提示词常量，仍允许 import。
它们不会出现在 `_register` 中，因此天然不在 denylist 内。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent          # agent/scripts
AGENT_ROOT = SCRIPT_DIR.parent                         # agent/
PROMPT_MANAGER_PY = AGENT_ROOT / "src" / "agent" / "core" / "infra" / "prompt_manager.py"

# 豁免文件：常量定义处 + 迁移加载器本身允许引用常量名
EXEMPT = {
    AGENT_ROOT / "src" / "agent" / "prompts.py",
    AGENT_ROOT / "src" / "agent" / "core" / "infra" / "prompt_manager.py",
}

# 显式白名单（防御性，正常情况 denylist 不会包含这些）
ALLOW = {"format_learnings", "format_open_debts", "format_rag_context"}


def collect_denylist() -> set[str]:
    """从 prompt_manager.py 的 _register(...) 调用提取已迁移常量名。"""
    text = PROMPT_MANAGER_PY.read_text(encoding="utf-8")
    names: set[str] = set()
    for call in re.finditer(r"_register\((.*?)\)", text, re.DOTALL):
        body = call.group(1)
        for sm in re.finditer(r"['\"]([A-Z][A-Z0-9_]*)['\"]", body):
            names.add(sm.group(1))
    return names - ALLOW


def scan_file(path: Path, denylist: set[str]) -> list[tuple[int, str, str]]:
    """返回该文件中的违规：(行号, 常量名, 该行内容)。"""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    lines = text.splitlines()
    hits: list[tuple[int, str, str]] = []
    for name in denylist:
        pat = re.compile(r"\b" + re.escape(name) + r"\b")
        for i, line in enumerate(lines, start=1):
            if pat.search(line):
                hits.append((i, name, line.strip()))
                break  # 每个常量每文件只报一次
    return hits


def main() -> int:
    denylist = collect_denylist()
    if not denylist:
        print("WARN: denylist 为空，请检查 prompt_manager.py 的 _register 解析。")
        return 2

    violations: list[tuple[Path, int, str, str]] = []
    scanned = 0
    for py in sorted(AGENT_ROOT.rglob("*.py")):
        if py in EXEMPT:
            continue
        # 跳过虚拟环境 / 缓存
        parts = py.parts
        if any(p in ("__pycache__", ".venv", "venv", "node_modules", ".git") for p in parts):
            continue
        scanned += 1
        for ln, name, snippet in scan_file(py, denylist):
            violations.append((py, ln, name, snippet))

    if violations:
        print(
            f"FAIL: 在 {len(violations)} 处发现对已迁移提示词常量的裸引用"
            f"（denylist 共 {len(denylist)} 个常量，扫描 {scanned} 个文件）。"
        )
        for py, ln, name, snippet in violations:
            rel = py.relative_to(AGENT_ROOT)
            print(f"  {rel}:{ln}  {name}  ->  {snippet}")
        print(
            "\n修复方式：把对应调用改成 "
            "pm.get(<key>).render_system/render_user(...)（见 提示词管理方案设计.md §9）。"
        )
        return 1

    print(f"PASS: 扫描 {scanned} 个文件，未发现对 {len(denylist)} 个已迁移常量的裸引用。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
