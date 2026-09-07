"""提示词相关的纯文本格式化助手（从旧 ``agent.prompts`` 迁入）。

阶段 C 删除 ``agent.prompts`` 后，这些与提示词渲染强相关的助手函数统一收口到
``core/infra``（仅依赖基础类型，不反向依赖 workflow）。调用方改为：

    from agent.core.infra.prompt_helpers import format_rag_context, format_open_debts, format_learnings

注意：这些函数不在 ``scripts/lint_prompts.py`` 的 denylist 内（denylist 只针对已迁移到
md 的提示词常量），``format_*`` 前缀为白名单放行。
"""

from __future__ import annotations


def format_rag_context(chunks: list, max_chunks: int = 3, max_text_len: int = 120) -> str:
    """把召回的 Chunk 列表渲染为可读文本块（供 M5 生成提示 / 命令输出复用）

    优化（2026-09-07）：限制召回条数与每条文本长度，减少 prompt token 消耗。
    默认最多 3 条、每条文本截断到 120 字符，可在调用方按需放宽。

    Args:
        chunks: ``agent.core.rag.Chunk`` 列表（或具 source/chapter_num/kind/text 属性的对象）
        max_chunks: 最大返回条数（默认 3）
        max_text_len: 每条文本最大字符数（默认 120）

    Returns:
        多行文本；空列表返回提示语。
    """
    if not chunks:
        return "（无语义召回结果）"
    lines: list[str] = []
    for c in chunks[:max_chunks]:
        label = c.source
        if getattr(c, "chapter_num", 0):
            label = f"{c.source} · 第{c.chapter_num}章"
        kind = getattr(c, "kind", "") or "ref"
        text = str(getattr(c, "text", "") or "")
        if len(text) > max_text_len:
            text = text[:max_text_len] + "…"
        lines.append(f"- [{label}｜{kind}] {text}")
    return "\n".join(lines)


def format_open_debts(debts: list, max_debts: int = 5, max_desc_len: int = 60) -> str:
        """把「未收回的钩子债 / 伏笔债」渲染为可读文本块（供 M5 生成提示注入）

        优化（2026-09-07）：限制返回条数与描述长度，减少 prompt token 消耗。

        入参 ``debts`` 支持两种形态（与 ``PacingStore.get_open_debts`` 对齐，互不冲突）：
          - ``Debt`` 对象列表（含 id/desc/kind/planted_ch 属性）
          - ``dict`` 列表（含 id/desc/kind/planted_ch 键，由 M5 ``_load_context`` 转换）

        Args:
            debts: 开放债务列表（``Debt`` 或 dict）
            max_debts: 最大返回条数（默认 5）
            max_desc_len: 每条描述最大字符数（默认 60）

        Returns:
            多行文本；空列表返回提示语。
        """
        if not debts:
            return "（当前无未收回的钩子债 / 伏笔债）"
        lines: list[str] = []
        for d in debts[:max_debts]:
            if isinstance(d, dict):
                _id = d.get("id", "")
                desc = d.get("desc", "")
                kind = d.get("kind", "general")
                planted = d.get("planted_ch", 0)
            else:
                _id = getattr(d, "id", "")
                desc = getattr(d, "desc", "")
                kind = getattr(d, "kind", "general")
                planted = getattr(d, "planted_ch", 0)
            if len(desc) > max_desc_len:
                desc = desc[:max_desc_len] + "…"
            planted_str = f"（埋设于第 {planted} 章）" if planted else ""
            lines.append(f"- [{kind}] {_id}：{desc}{planted_str}")
        return "\n".join(lines)


def format_learnings(learnings: list) -> str:
    """把学习沉淀（``Learning`` 或 dict 列表）渲染为可读文本块，注入 M5 system prompt

    Args:
        learnings: ``Learning`` 对象或 dict（含 category/text）列表

    Returns:
        多行文本；空列表返回提示语。
    """
    if not learnings:
        return "（暂无已沉淀的写法记忆）"
    lines: list[str] = []
    for x in learnings:
        if isinstance(x, dict):
            cat = x.get("category", "general")
            text = x.get("text", "")
        else:
            cat = getattr(x, "category", "general")
            text = getattr(x, "text", "")
        lines.append(f"- [{cat}] {text}")
    return "\n".join(lines)
