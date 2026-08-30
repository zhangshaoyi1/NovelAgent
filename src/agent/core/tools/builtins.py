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

下沉说明（2026-08-29）：``export_chapters`` 工具已迁至 ``workflows/m11_export.py``
（工作流层注册），消除 ``core→workflows`` 反向依赖；本模块仅保留纯 core 能力工具。
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
    from agent.core.quality.scoring.quality_checker import QualityChecker

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
    name="deslop_check",
    description=(
        "检测并去除章节文本中的AI味：返回 AI 味等级（light/medium/heavy）、"
        "6 项客观指标、禁用词命中清单，以及改写后的文本与修改记录。"
        "中度/重度走 LLM 改写（6 Gate + 三遍法），轻度走规则后处理。"
        "仅改表达不改剧情，保留原文的伏笔/人设/情节。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "chapter_text": {"type": "string", "description": "待去AI味的章节正文"},
            "level": {
                "type": "string",
                "enum": ["auto", "light", "medium", "heavy"],
                "description": "AI味等级（默认 auto 自动扫描判定）",
                "default": "auto",
            },
            "dry_run": {
                "type": "boolean",
                "description": "True 仅扫描报告，不改写文本",
                "default": False,
            },
        },
        "required": ["chapter_text"],
    },
)
def deslop_check(chapter_text: str, level: str = "auto", dry_run: bool = False) -> Any:
    from agent.core.anti_ai.rewriter import DeslopRewriter

    rewriter = DeslopRewriter(project_dir=get_project_dir())
    if dry_run:
        report = rewriter.classify(chapter_text)
        return {
            "level": report.level,
            "score": report.score,
            "metrics": report.metrics,
            "banned_hits": report.banned_hits,
            "flagged_items": report.flagged_items,
        }
    result = rewriter.rewrite(chapter_text, level=level)
    return {
        "level": result.level,
        "changed": result.changed,
        "via_llm": result.via_llm,
        "text": result.text,
        "changes": result.changes[:30],
        "metrics": result.metrics,
    }

