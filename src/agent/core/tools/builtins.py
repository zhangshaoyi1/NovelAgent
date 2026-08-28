"""内置工具（Phase 0）

把 NovelAgent 现有能力封装为可被 Agent 调用的工具。所有需要"项目上下文"的工具
（RAG / 设定 / 质检 / 导出 / 伏笔）通过 ``set_project_context`` 注入 project_dir，
LLM 只需提供业务参数，无需关心项目路径。

工具清单（与设计文档 §2.6 / 缺口1 对应）：
- rag_retrieve      语义召回相关片段（RAG，无向量索引时 BM25 兜底）
- get_setting       读取设定集（world / character / subline）
- count_words       字数统计（总字符 + 中文字数）
- quality_check     章节规则层质量校验（无需网络）
- foreshadow_read   读取伏笔登记表原文（ForeshadowManager 仍为桩，先读原文）
- export_chapters   导出全书为 txt / markdown / epub
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.core.engine.tool_contracts import ToolResult, tool

# 项目上下文：由 Agent 循环在执行工具前注入
_CTX: dict[str, str] = {}


def set_project_context(project_dir: str | Path) -> None:
    """注入当前小说项目目录（工具执行时读取）"""
    _CTX["project_dir"] = str(project_dir)


def get_project_dir() -> Path:
    return Path(_CTX.get("project_dir", "."))


@tool(
    name="rag_retrieve",
    description=(
        "语义召回与当前写作相关的已写章节/设定片段，用于保持长篇一致性。"
        "query 通常为『第N章 + 支线 + 目标 + 阶段』。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "检索词"},
            "top_k": {"type": "integer", "description": "返回条数，默认 5", "default": 5},
        },
        "required": ["query"],
    },
)
def rag_retrieve(query: str, top_k: int = 5) -> Any:
    from agent.core.rag.retriever import Retriever

    try:
        chunks = Retriever(get_project_dir()).retrieve(query, top_k=top_k)
    except Exception:
        # 索引缺失或 embedding 不可达：返回空，不阻断（与 RAG 降级策略一致）
        chunks = []
    return [
        {
            "source": c.source,
            "chapter_num": c.chapter_num,
            "text": c.text[:500],
        }
        for c in chunks
    ]


@tool(
    name="get_setting",
    description=(
        "读取设定集：world（总设定集）、character（角色档案）、subline（支线设定）。"
        "不传 key 时列出该类的全部条目名。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": ["world", "character", "subline"],
                "description": "设定类型",
            },
            "key": {
                "type": "string",
                "description": "角色名（character）或支线 ID（subline）；world 可省略",
            },
        },
        "required": ["kind"],
    },
)
def get_setting(kind: str, key: str = "") -> Any:
    from agent.core.story.setting_manager import SettingManager

    sm = SettingManager(get_project_dir())
    if kind == "world":
        return sm.load_world()
    if kind == "character":
        if not key:
            return {"characters": sm.list_characters()}
        return sm.load_character(key)
    if kind == "subline":
        if not key:
            return {"sublines": sm.list_sublines()}
        return sm.load_subline(key)
    return ToolResult(success=False, error=f"未知 kind: {kind}")


@tool(
    name="count_words",
    description="统计文本字数（总字符数与中文字数），用于体量控制与限流。",
    parameters_schema={
        "type": "object",
        "properties": {"text": {"type": "string", "description": "待统计文本"}},
        "required": ["text"],
    },
)
def count_words(text: str) -> Any:
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    return {"total_chars": len(text), "cjk_chars": cjk}


@tool(
    name="quality_check",
    description=(
        "对章节正文做规则层质量校验（开篇钩子/章末悬念/禁用词/连贯等），"
        "返回是否通过（passed）及问题列表（issues）。纯规则，无需网络。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "chapter_text": {"type": "string", "description": "章节正文"}
        },
        "required": ["chapter_text"],
    },
)
def quality_check(chapter_text: str) -> Any:
    from agent.core.quality.quality_checker import QualityChecker

    report = QualityChecker(get_project_dir(), None).check(chapter_text)
    return {
        "passed": report.passed,
        "issues": [
            {
                "rule_id": i.rule_id,
                "severity": i.severity.value,
                "description": i.description,
            }
            for i in report.issues
        ],
    }


@tool(
    name="foreshadow_read",
    description=(
        "读取伏笔登记表（foreshadows.md）原始内容，用于追踪伏笔的埋设/回收状态。"
        "注：结构化伏笔管理（ForeshadowManager）尚未实现，此处返回原文。"
    ),
    parameters_schema={"type": "object", "properties": {}},
)
def foreshadow_read() -> Any:
    f = get_project_dir() / "foreshadows.md"
    if not f.exists():
        return {"exists": False, "content": ""}
    return {"exists": True, "content": f.read_text(encoding="utf-8")}


@tool(
    name="export_chapters",
    description="把全部章节导出为 txt / markdown / epub，返回导出结果（路径/章数/字数）。",
    parameters_schema={
        "type": "object",
        "properties": {
            "fmt": {
                "type": "string",
                "enum": ["txt", "markdown", "epub"],
                "description": "导出格式",
            },
            "title": {"type": "string", "description": "书名，可省略（从 world.md 读取）"},
        },
        "required": ["fmt"],
    },
)
def export_chapters(fmt: str, title: str = "") -> Any:
    from agent.workflows.m11_export import ExportWorkflow

    res = ExportWorkflow(get_project_dir()).export(fmt, title=title or None)
    return {
        "success": res.success,
        "format": res.format,
        "output_file": str(res.output_file),
        "chapter_count": res.chapter_count,
        "total_words": res.total_words,
        "message": res.message,
    }
