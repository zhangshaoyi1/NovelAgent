"""parse_llm_json 旧通道冻结红线（2026-09-29）

背景：LLM 输出即事实 → 底层启发式抢救解析 → 各层门禁误报 → 逐条打补丁/登记豁免，
这条流水线的补丁增速由 LLM 输出长尾方差决定，无法收敛。治理方案：

1. 新调用点一律走 ``gateway_adapter.chat_utility_structured``（response_format
   强约束 + pydantic 校验 + 失败遥测）；
2. 存量 ``parse_llm_json`` 调用点以本测试白名单冻结——**只许删不许加**。

规则：
- 任何白名单之外的文件出现 ``parse_llm_json`` 调用 → fail（提示改用结构化通道）；
- 白名单文件全部迁走后，应把对应条目从 ``_ALLOWED_FILES`` 删除（清单缩短是进展，
  本测试会在文件已无调用时提示收缩清单）。
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "agent"

# 冻结基线（2026-09-29）：允许调用 parse_llm_json 的文件（相对 src/agent 路径，posix）。
# 基础设施豁免：
#   base/utils.py        — 定义本身
#   base/structured_output.py — 结构化输出的最后一级文本回退（提取器复用）
#   agent/utils.py、agent/base/__init__.py — 向后兼容再导出（非真实调用）
_ALLOWED_FILES: set[str] = {
    # 第一批（门禁/评分链 7 个文件）已于 2026-09-29 全部迁出，条目已删除
    # ── 第二批（非门禁路径，迁移可放缓）──
    "workflows/evaluation/m11_export.py",
    "workflows/evaluation/m14_architecture.py",
    "workflows/evaluation/m15_bookworm.py",
    "workflows/evaluation/m16_pacing.py",
    "workflows/evaluation/m17_learn.py",
    "workflows/evaluation/m19_review_sync.py",
    "workflows/evaluation/m20_analyze.py",
    "workflows/evaluation/m21_review.py",
    "workflows/market/m23_short.py",
    "workflows/planning/m1_config.py",
    "workflows/planning/m3_outline.py",
    "workflows/planning/m4_character.py",
    "workflows/writing/agentic_write.py",
    "workflows/writing/ledger_delta_producer.py",
    "workflows/writing/m6_adjust.py",
}

_INFRA_EXEMPT = {
    "base/utils.py",
    "base/structured_output.py",
    "utils.py",
    "base/__init__.py",
}


def _called_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                names.add(f.id)
            elif isinstance(f, ast.Attribute):
                names.add(f.attr)
    return names


def _scan() -> dict[str, int]:
    """返回 {相对路径: parse_llm_json 调用次数}（含 import 即视为调用方文件，计 0 次）"""
    result: dict[str, int] = {}
    for py in SRC.rglob("*.py"):
        rel = py.relative_to(SRC).as_posix()
        try:
            # utf-8-sig：部分存量文件带 BOM，utf-8 会 SyntaxError 导致漏检
            tree = ast.parse(py.read_text(encoding="utf-8-sig"))
        except SyntaxError:
            raise AssertionError(f"红线扫描无法解析（不允许跳过）: {py}")
        calls = 0
        imports = False
        for n in ast.walk(tree):
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                src = ast.unparse(n)
                if "parse_llm_json" in src:
                    imports = True
            if isinstance(n, ast.Call):
                f = n.func
                name = f.id if isinstance(f, ast.Name) else (
                    f.attr if isinstance(f, ast.Attribute) else ""
                )
                if name == "parse_llm_json":
                    calls += 1
        if imports or calls:
            result[rel] = calls
    return result


def test_no_new_parse_llm_json_call_sites() -> None:
    usage = _scan()
    offenders = {
        rel: n
        for rel, n in usage.items()
        if rel not in _INFRA_EXEMPT and rel not in _ALLOWED_FILES
    }
    assert not offenders, (
        "发现 parse_llm_json 新调用点（旧通道已冻结）。判定/评分类调用请改用 "
        f"gateway_adapter.chat_utility_structured；确属基础设施豁免请加入 "
        f"_INFRA_EXEMPT 并说明理由。新增文件：{offenders}"
    )


def test_allowed_list_should_shrink_when_files_migrated() -> None:
    usage = _scan()
    stale = sorted(rel for rel in _ALLOWED_FILES if rel not in usage)
    assert not stale, (
        "以下白名单文件已不再引用 parse_llm_json，请从 _ALLOWED_FILES 删除条目"
        f"（清单只许缩短）：{stale}"
    )
