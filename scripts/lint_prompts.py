#!/usr/bin/env python3
"""Prompt-migration lint gate (阶段 B/C, 见 项目文档/提示词管理方案设计.md §9).

目的
----
阶段 B 把 ``agent/prompts.py`` 中全部提示词常量迁到 ``agent/prompts/*.md``，
由 ``PromptManager``（``agent/core/infra/prompt_manager.py``，单例 ``pm``）统一加载。
调用方必须改用 ``pm.get(key).render_system/render_user(...)``，禁止再直接
``from agent.prompts import <常量>``、``import agent.prompts``，或裸引用已迁移常量名。

阶段 C 已删除 ``agent/prompts.py``，因此本卡口改用**显式 canonical denylist**（即当初
从 ``prompts.py`` 迁出的 50 个常量名），不再依赖 ``_register`` 动态解析——
保证即便常量源头已删除，禁令依然有效，杜绝任何人把提示词"写回代码"。

本脚本即 CI 卡口：扫描仓库内全部 ``*.py``（豁免 ``prompt_manager.py`` 与
``prompt_helpers.py`` 自身），若存在对已迁移常量的裸引用、或重新 ``import agent.prompts``
则失败退出（exit 1）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent          # agent/scripts
AGENT_ROOT = SCRIPT_DIR.parent                         # agent/
PROMPT_MANAGER_PY = AGENT_ROOT / "src" / "agent" / "core" / "infra" / "prompt_manager.py"
PROMPT_HELPERS_PY = AGENT_ROOT / "src" / "agent" / "core" / "infra" / "prompt_helpers.py"

# 豁免文件：迁移加载器本身 + 格式化助手允许提及常量名；本卡口脚本内含 denylist 清单；
# 阶段 C 单测（tests/test_prompt_manager_phase_c.py）故意以字符串字面量引用旧常量名 +
# import agent.prompts，用于断言"已删除、不得回流"，属测试意图而非违规。
EXEMPT = {
    AGENT_ROOT / "src" / "agent" / "core" / "infra" / "prompt_manager.py",
    PROMPT_HELPERS_PY,
    SCRIPT_DIR / "lint_prompts.py",
    AGENT_ROOT / "tests" / "test_prompt_manager_phase_c.py",
}

# 显式白名单（防御性，正常情况 denylist 不会包含这些）
ALLOW = {"format_learnings", "format_open_debts", "format_rag_context"}

# canonical denylist：阶段 B 从 agent/prompts.py 迁出的全部 50 个常量名
# （21 个 system + 29 个 user）。阶段 C 删除 prompts.py 后此处为唯一真相源。
MIGRATED = {
    # ---- system ----
    "M1_SYSTEM_PROMPT",
    "M2_SYSTEM_PROMPT",
    "M3_SYSTEM_PROMPT",
    "M4_SYSTEM_PROMPT",
    "M14_SYSTEM_PROMPT",
    "M14_ITERATE_SYSTEM_PROMPT",
    "M14_GAP_CHECK_SYSTEM_PROMPT",
    "M5_GENERATE_SYSTEM_PROMPT",
    "M5_QUALITY_CHECK_SYSTEM_PROMPT",
    "M5_REVISE_SYSTEM_PROMPT",
    "M6_ADJUST_ROUTE_SYSTEM_PROMPT",
    "M6_ADJUST_RELATION_SYSTEM_PROMPT",
    "M6_IMPACT_REPORT_SYSTEM_PROMPT",
    "M12_CONFLICT_SYSTEM_PROMPT",
    "M12_CONTENT_AUDIT_SYSTEM_PROMPT",
    "M12_SUMMARY_SYSTEM_PROMPT",
    "M15_BOOKWORM_SYSTEM_PROMPT",
    "M16_PACING_SYSTEM_PROMPT",
    "M19_REVIEW_SYSTEM_PROMPT",
    "M_D_REVIEW_SYSTEM_PROMPT",
    "E_LEARN_EXTRACT_SYSTEM_PROMPT",
    # ---- user ----
    "M1_USER_PROMPT_TEMPLATE",
    "M2_USER_PROMPT_TEMPLATE",
    "M3_USER_PROMPT_TEMPLATE",
    "M4_USER_PROMPT_TEMPLATE",
    "M14_USER_PROMPT_TEMPLATE",
    "M14_ITERATE_USER_PROMPT_TEMPLATE",
    "M14_GAP_CHECK_USER_PROMPT_TEMPLATE",
    "M5_GENERATE_USER_TEMPLATE",
    "M5_QUALITY_CHECK_USER_TEMPLATE",
    "M5_REVISE_USER_TEMPLATE",
    "M6_ADJUST_ROUTE_USER_TEMPLATE",
    "M6_ADJUST_RELATION_USER_TEMPLATE",
    "M6_IMPACT_REPORT_USER_TEMPLATE",
    "M12_CONFLICT_USER_TEMPLATE",
    "M12_CONTENT_AUDIT_USER_TEMPLATE",
    "M12_SUMMARY_USER_TEMPLATE",
    "M15_BOOKWORM_USER_TEMPLATE",
    "M16_PACING_USER_TEMPLATE",
    "M19_REVIEW_USER_TEMPLATE",
    "M_D_REVIEW_USER_TEMPLATE",
    "E_LEARN_EXTRACT_USER_TEMPLATE",
    "G11_METHOD_INSTRUCTION_TEMPLATE",
    "G11_STYLE_INSTRUCTION_TEMPLATE",
    "G12_EMOTION_INSTRUCTION_TEMPLATE",
    "G12_PAYOFF_INSTRUCTION_TEMPLATE",
    "G12_READER_FEEDBACK_TEMPLATE",
    "G8_ENDING_FALLBACK_INSTRUCTION",
    "G8_ENDING_INSTRUCTION_TEMPLATE",
    "G_CHARACTER_STATE_CONSTRAINT_TEMPLATE",
}
DENYLIST = MIGRATED - ALLOW

# 重新引入 agent.prompts 模块（阶段 C 已删除）一律禁止
IMPORT_RE = re.compile(r"^\s*(from\s+agent\.prompts\s+import|import\s+agent\.prompts)\b")


def scan_file(path: Path) -> list[tuple[int, str, str]]:
    """返回该文件中的违规：(行号, 规则, 该行内容)。"""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    lines = text.splitlines()
    hits: list[tuple[int, str, str]] = []
    for i, line in enumerate(lines, start=1):
        if IMPORT_RE.search(line):
            hits.append((i, "import-agent.prompts", line.strip()))
            continue
        for name in DENYLIST:
            if re.search(r"\b" + re.escape(name) + r"\b", line):
                hits.append((i, name, line.strip()))
                break  # 每个文件只报首个命中，避免噪音
    return hits


def main() -> int:
    violations: list[tuple[Path, int, str, str]] = []
    scanned = 0
    for py in sorted(AGENT_ROOT.rglob("*.py")):
        if py in EXEMPT:
            continue
        parts = py.parts
        if any(p in ("__pycache__", ".venv", "venv", "node_modules", ".git") for p in parts):
            continue
        scanned += 1
        for ln, rule, snippet in scan_file(py):
            violations.append((py, ln, rule, snippet))

    if violations:
        print(
            f"FAIL: 在 {len(violations)} 处发现违规"
            f"（denylist 共 {len(DENYLIST)} 个已迁移常量，扫描 {scanned} 个文件）。"
        )
        for py, ln, rule, snippet in violations:
            rel = py.relative_to(AGENT_ROOT)
            print(f"  {rel}:{ln}  [{rule}]  ->  {snippet}")
        print(
            "\n修复方式：把对应调用改成 "
            "pm.get(<key>).render_system/render_user(...)（见 提示词管理方案设计.md §9）；"
            "格式化助手请改 from agent.core.infra.prompt_helpers import。"
        )
        return 1

    print(f"PASS: 扫描 {scanned} 个文件，未发现对 {len(DENYLIST)} 个已迁移常量的裸引用 / 重新 import agent.prompts。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
