"""批前逐章契约补齐（供给端修复，2026-09-24）。

背景（《灵荒工坊》自动写作失败实证，日志
``novels/灵荒工坊/.state/tasks/logs/20260924171142-54b1b4.log``）：
``workflows/planning/m3_outline.py::_render_and_save_sublines`` 是 subline.md
逐章行（``第N章：…``）的**唯一写入方**，而它只在 M3 首轮大纲生成时跑一次；
``agents/planner.py::PlannerAgent.replan_batch`` 只产 **arc 级**规划，
**从不写逐章行**。⇒ 首轮开篇窗口（20 章）之后，逐章契约**永久缺位**
⇒ 写手只能按阶段模板自编本章内容 ⇒ 同质/注水 ⇒ 评委判不合格 ⇒
回退重写时输入一字不变 ⇒ 整窗销毁-重写死循环（与 ``chapter_contract``
模块头记录的 09-18 P0 同源，只是触发区间从"开篇之后"提前到了"21 章之后"）。

本模块补上「批间滚动供给」这一环：每次批前，为**即将写的窗口**
（``cur+1 .. cur+20``，与门禁 ``plan_consistency._write_window`` 同口径）内
缺逐章行的章生成逐章行，并**合并**回原 subline.md —— 维持 subline.md 为
唯一真源，幂等可重跑（同章号旧行被替换，其余内容原样保留）。

契约对齐（纪律 #3「语言锚与解析式是同一件事的两半」）
----------------------------------------------
· 小节名（``章节钩子设计`` / ``章节强度档位`` / ``情节点序列``）、逐章行匹配式、
  档位四档语义一律取自 ``core.story.chapter_contract``（渲染端与消费端的唯一真源）；
· 「窗口内有没有逐章行」的判定复用门禁 ``plan_consistency`` 的解析函数——
  若各写一份，就会出现"补了行但门禁看不见"的静默失真（补了等于没补）。

失败语义（与批间复规划一致）
---------------------------
逐章契约补齐是**增强不是门槛**：失败 ``degrade()`` 显性留痕后继续写
（写手仍有 ``chapter_contract`` 的阶段级回退路径），绝不静默吞掉；
但也不允许它把写作拖停。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from rich.console import Console

from agent.core.infra.degrade import degrade
from agent.core.story.chapter_contract import (
    HOOKS_SECTION,
    PACE_TIERS,
    POINTS_SECTION,
    TIERS_SECTION,
    chapter_line_pattern,
    extract_section,
)
from agent.workflows.pipeline.plan_consistency import (
    chapter_numbers,
    subline_range,
    write_window,
)

#: 每个 subline.md 的逐章小节 → LLM 草稿字段名（三小节与 M3 的 subline 字段同构）
_SECTIONS: tuple[tuple[str, str], ...] = (
    (HOOKS_SECTION, "chapter_hooks"),
    (TIERS_SECTION, "chapter_tiers"),
    (POINTS_SECTION, "plot_points"),
)


class ChapterContractDraft(BaseModel):
    """LLM 产出的逐章契约草稿（字段名与 M3 ``sublines[*]`` 同构）。"""

    chapter_hooks: str = Field(
        default="",
        description=(
            "逐章钩子：每章一行，格式 "
            "`第N章：档位=<高潮|推进|垫片|日常>｜章首钩子=…｜章尾钩子=…｜爽点=…"
            "｜目标情绪=…｜在场=…｜禁=…｜验收=…`（档位紧跟章号写在行首）"
        ),
    )
    chapter_tiers: str = Field(
        default="",
        description="每章强度档位表：每章一行，格式 `第N章：<高潮|推进|垫片|日常>`",
    )
    plot_points: str = Field(
        default="",
        description="情节点序列：每章一行，格式 `第N章：动作化子事件1；动作化子事件2`",
    )


def _normalize_lines(text: Any, *, lo: int, hi: int) -> list[str]:
    """规范化 LLM 产出的逐章行：只留区间内、行首带 ``第N章：`` 的行（去重保序）。

    宽容处理常见的形态噪声（markdown 列表前缀 ``- `` / 包围空白），
    但**不猜测**：行首没有章标记的行一律丢弃——写手只认 ``第N章：…``，
    放进别的内容只会污染小节（纪律 #3）。
    """
    out: list[str] = []
    seen: set[int] = set()
    for raw in str(text or "").splitlines():
        ln = raw.strip().lstrip("-*·").strip()
        m = re.match(r"^第\s*(\d+)\s*章\s*[：:]", ln)
        if not m:
            continue
        n = int(m.group(1))
        if n < lo or n > hi or n in seen:
            continue
        seen.add(n)
        out.append(ln)
    return out


def _section_span(content: str, title: str) -> tuple[int, int] | None:
    """``## <title>`` 小节正文的 ``[start, end)`` 偏移（正文不含标题行）。"""
    m = re.search(rf"^##\s*{re.escape(title)}\s*\n", content, re.M)
    if not m:
        return None
    start = m.end()
    nxt = re.search(r"^##\s", content[start:], re.M)
    return (start, start + nxt.start()) if nxt else (start, len(content))


def _segments(line: str) -> list[str]:
    """按 ``；`` 切分一行（LLM 有时用分号而非换行分隔多章契约段）。

    ⚠ 与 ``chapter_contract._chapter_segments`` 的分法（按章标记切）不同但等价
    覆盖同一现实输入：本模块只用它做**行归属判定**，不承担切分产出。
    """
    return [s.strip() for s in re.split(r"[；;]", line) if s.strip()]


def _belongs_to(line: str, targets: set[int]) -> bool:
    """该行是否是 ``targets`` 中某一章的逐章契约行（行首章标记判据，真源同源）。"""
    segs = _segments(line)
    return any(chapter_line_pattern(n).match(s) for n in targets for s in segs)


def _merge_section(content: str, title: str, new_lines: list[str]) -> str:
    """把 ``new_lines`` 幂等合并进 ``## title`` 小节（同章号旧行删除后追加）。

    为什么是"删同章号 + 追加"而不是"整节重写"：小节里还有 M3 写的批次标题与
    历史逐章行（本窗口之外的章），整节重写会**销毁历史供给**（不可逆）。
    """
    if not new_lines:
        return content
    targets = {n for ln in new_lines for n in _line_numbers(ln)}
    span = _section_span(content, title)
    if span is None:
        return content.rstrip("\n") + f"\n\n## {title}\n\n" + "\n".join(new_lines) + "\n"
    start, end = span
    kept = [ln for ln in content[start:end].splitlines() if not _belongs_to(ln, targets)]
    head = "\n".join(kept).strip("\n")
    new_body = (head + "\n\n" if head else "") + "\n".join(new_lines) + "\n\n"
    return content[:start] + new_body + content[end:]


def _line_numbers(line: str) -> set[int]:
    """行内出现的章号（仅用于定位旧行，不做契约判定）。"""
    return {int(m.group(1)) for m in re.finditer(r"第\s*(\d+)\s*章\s*[：:]", line)}


def _prior_reference(content: str, lo: int, *, keep: int = 3) -> str:
    """取本窗口之前**最近**的逐章钩子行（承接锚点，最多 ``keep`` 条）。

    为什么必须给：新生成的逐章行若不知道前文已定的事件与境界/能力进度，
    会写出一份与已写章节冲突的细纲 —— 那等于把"供给缺位"换成"供给冲突"。
    """
    section = extract_section(content, HOOKS_SECTION)
    if not section:
        return ""
    prior: list[tuple[int, str]] = []
    for ln in section.splitlines():
        ln = ln.strip()
        for n in _line_numbers(ln):
            if n < lo:
                prior.append((n, ln))
                break
    prior.sort(key=lambda t: t[0])
    return "\n".join(ln for _, ln in prior[-keep:])


def _build_messages(
    *,
    subline_id: str,
    content: str,
    lo: int,
    hi: int,
    current: int,
    summary: str,
) -> list[dict[str, str]]:
    """装配补齐提示词（语言锚＝ ``chapter_contract`` 的解析式，格式与 M3 一致）。"""
    tiers = "；".join(f"{t.name}={t.label}" for t in PACE_TIERS)
    goal = extract_section(content, "支线目标")
    chars = extract_section(content, "出场角色")
    conflicts = extract_section(content, "关键冲突")
    constraints = extract_section(content, "约束")
    curve = extract_section(content, "剧集压力曲线")
    prior = _prior_reference(content, lo)

    system = (
        "你是网文细纲规划师，为一条**已在推进的支线**补齐指定章节区间的逐章细纲。\n"
        "严格只输出 JSON，字段：chapter_hooks / chapter_tiers / plot_points。\n\n"
        "格式（必须能被下游逐行解析，冒号用中文全角）：\n"
        "· chapter_hooks 每章一行：\n"
        "  `第N章：档位=<四档之一>｜章首钩子=…｜章尾钩子=…｜爽点=…｜目标情绪=…"
        "｜在场=…｜禁=…｜验收=…`\n"
        "  （`档位` 必须紧跟 `第N章：` 写在**行首**——写在行尾实测会被整段丢掉）\n"
        "· chapter_tiers 每章一行：`第N章：<四档之一>`（与上一字段行内档位一致）\n"
        "· plot_points 每章一行：`第N章：动作化子事件1；动作化子事件2`\n\n"
        f"档位四档语义：{tiers}。"
        "高潮须有明确爆点；推进须有实质进展或代价；垫片/日常为**规划上就该放松**的章。"
        "整段要有落差，不要每章都满格。\n\n"
        "硬约束：\n"
        "1. 必须为【需补齐的章节】区间内**每一章**各写一行，章号连续、不跳号、不越界；\n"
        "2. 每章的钩子与情节点必须是**该章独有**的具体事件（谁做了什么、指向什么物件/"
        "关系/后果），严禁「日常铺垫」「危机触发」「继续修炼」这类可套用到任意一章的"
        "模板词；任意两章不得雷同——写手只会拿到本章那一行，雷同等于没给；\n"
        "3. 必须承接【前文已定逐章行】与【实际写作进展】：境界、能力解锁进度、"
        "人物登场顺序不得与之冲突，已发生的剧情不得推翻重演；\n"
        "4. `在场` 只允许已登记角色 + 本章首次登场的具名人物，不得照抄整条支线的角色表；\n"
        "5. `禁` 写本章禁止出现的内容（未到登场点的角色、未解锁的能力、不可逆进展）；\n"
        "6. `验收` 必须是外部可判定标准（读者读完能说出什么），禁用「氛围到位」"
        "「情绪饱满」这类不可判定的词；\n"
        "7. 不要输出 ```json 标记，不要任何解释文字。"
    )
    user = (
        f"【支线】{subline_id}\n"
        f"【支线目标】{goal or '（未标注）'}\n"
        f"【出场角色】{chars or '（未标注）'}\n"
        f"【关键冲突】{conflicts or '（未标注）'}\n"
        f"【约束】{constraints or '（未标注）'}\n"
        f"【剧集压力曲线】\n{curve or '（未标注）'}\n\n"
        f"【前文已定逐章行（第{lo - 1}章及以前，必须承接）】\n"
        f"{prior or '（本支线此前无逐章行，请依据支线目标与约束自洽展开）'}\n\n"
        f"【实际写作进展摘要】\n{summary or '（无摘要）'}\n\n"
        f"【需补齐的章节】第{lo}-{hi}章（当前已写到第 {current} 章）\n"
        "请补齐这些章节的逐章细纲。"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _draft_for_range(
    llm: Any,
    messages: list[dict[str, str]],
    *,
    lo: int,
    hi: int,
) -> ChapterContractDraft:
    """调 LLM 产出草稿；失败追加「强制纯 JSON」重试一次（与写手同策略）。"""
    from agent.client.gateway_adapter import chat_structured

    try:
        out = chat_structured(
            llm,
            messages,
            ChapterContractDraft,
            use="creative",
            temperature=0.7,
            max_tokens=12288,
            enable_thinking=False,
        )
        return out if isinstance(out, ChapterContractDraft) else ChapterContractDraft(**out)
    except Exception as first:  # noqa: BLE001, SILENT_DEGRADE reason=retry-loop - 下面追加约束后重试一次
        retry_msgs = [
            *messages,
            {
                "role": "user",
                "content": (
                    "上一次输出无法解析为 JSON。请**只输出一个 JSON 对象**，"
                    "不要任何前后缀、解释或 ```json 标记；所有字段值都是字符串"
                    "（多行用 \\n 连接）。"
                ),
            },
        ]
        try:
            out = chat_structured(
                llm,
                retry_msgs,
                ChapterContractDraft,
                use="creative",
                temperature=0.4,
                max_tokens=12288,
                enable_thinking=False,
            )
            return out if isinstance(out, ChapterContractDraft) else ChapterContractDraft(**out)
        except Exception as second:  # noqa: BLE001 - 两败才上抛，由调用方显性降级
            raise RuntimeError(f"逐章契约生成失败（已重试一次）：{second}") from first


def ensure_window_contracts(
    project_dir: str | Path,
    *,
    summary: str = "",
    llm: Any = None,
    console: Console | None = None,
) -> list[str]:
    """为即将写的窗口补齐 subline.md 的逐章契约行（幂等，失败降级不阻断）。

    流程：算写作窗口 → 找覆盖窗口的 subline → 统计窗口内缺口章 → 仅对缺口调
    LLM（无缺口时**零 LLM 调用**）→ 按章号合并回同一 subline.md。

    Args:
        project_dir: 项目根目录。
        summary: 批级进展摘要（``batch_replan.build_batch_summary``），
            作为"实际写作进展"注入提示词；为空则退化为仅按支线自身设定生成。
        llm: 统一 Gateway（``client/LLMClient``）；空则按需 ``create_gateway()``。
        console: 输出通道。

    Returns:
        实际补齐的支线说明列表（空 = 无缺口或全部降级）。
    """
    console = console or Console()
    project_dir = Path(project_dir)
    sublines_dir = project_dir / "sublines"
    if not sublines_dir.exists():
        return []
    win = write_window(project_dir)
    if win is None:
        return []  # 无进度文件：判不出窗口，不猜

    notes: list[str] = []
    for sub_dir in sorted(sublines_dir.iterdir()):
        f = sub_dir / "subline.md"
        if not (sub_dir.is_dir() and f.exists()):
            continue
        try:
            content = f.read_text(encoding="utf-8")
        except Exception as e:  # noqa: BLE001 - 读不到则该支线本次不补
            degrade("subline_contract.read", f"sublines/{sub_dir.name}/subline.md 读取失败，本次不补", e)
            continue

        scope_lo, scope_hi = _scope_of(content, win)
        if scope_hi < scope_lo:
            continue  # 支线区间与写作窗口不相交
        covered = chapter_numbers(content)
        gaps = [n for n in range(scope_lo, scope_hi + 1) if n not in covered]
        if not gaps:
            continue  # 窗口内已全覆盖：零 LLM 调用（幂等重跑的关键）
        lo, hi = gaps[0], gaps[-1]

        try:
            if llm is None:
                from agent.client.gateway_adapter import create_gateway

                llm = create_gateway()
            msgs = _build_messages(
                subline_id=sub_dir.name,
                content=content,
                lo=lo,
                hi=hi,
                current=win[0] - 1,
                summary=summary,
            )
            draft = _draft_for_range(llm, msgs, lo=lo, hi=hi)
            merged, per_section = _merge_draft(content, draft, lo=lo, hi=hi)
            filled = set().union(*per_section.values()) if per_section else set()
            if not filled:
                degrade(
                    "subline_contract.empty",
                    f"sublines/{sub_dir.name}/subline.md 补齐返回空（第{lo}-{hi}章），沿用既有细纲",
                )
                continue
            tmp = f.parent / (f.name + ".tmp")
            tmp.write_text(merged, encoding="utf-8")
            tmp.replace(f)
        except Exception as e:  # noqa: BLE001 - 增强项：失败显性留痕后继续写
            degrade(
                "subline_contract.generate",
                f"sublines/{sub_dir.name}/subline.md 逐章契约补齐失败（第{lo}-{hi}章），"
                "本批写手回退阶段级供给",
                e,
            )
            console.print(
                f"[yellow]⚠ 逐章契约补齐失败（{sub_dir.name} 第{lo}-{hi}章）：{e}；"
                "本批沿用既有细纲[/yellow]"
            )
            continue

        want = set(range(lo, hi + 1))
        thin = {
            title: sorted(want - got)
            for title, got in per_section.items()
            if got and want - got
        }
        line = f"{sub_dir.name} 第{lo}-{hi}章补齐 {len(filled)}/{hi - lo + 1} 章逐章契约"
        notes.append(line)
        if want - filled:
            degrade(
                "subline_contract.partial",
                f"sublines/{sub_dir.name}/subline.md 补齐不完整：{line}"
                f"（缺 {len(want - filled)} 章）",
            )
        if thin:
            degrade(
                "subline_contract.thin_section",
                f"sublines/{sub_dir.name}/subline.md 分小节供给不全："
                + "；".join(f"{t} 缺 {len(ns)} 章" for t, ns in thin.items()),
            )
        console.print(
            f"[cyan]✓ 逐章细纲补齐：{line}（写入 sublines/{sub_dir.name}/subline.md）[/cyan]"
        )
    return notes


def _scope_of(content: str, win: tuple[int, int]) -> tuple[int, int]:
    """本支线在写作窗口内的应写章区间（与门禁 ``check_subline_plot_source`` 同口径）。"""
    lo, hi = subline_range(content)
    scope_lo, scope_hi = win
    if lo:
        scope_lo = max(scope_lo, lo)
        scope_hi = min(scope_hi, hi)
    return scope_lo, scope_hi


def _merge_draft(
    content: str, draft: ChapterContractDraft, *, lo: int, hi: int
) -> tuple[str, dict[str, set[int]]]:
    """把草稿三个字段合并进 subline.md；返回 (新内容, 各小节覆盖到的章号集合)。

    ⚠ 覆盖度**按小节分别统计**：门禁只看三小节的**并集**（``chapter_numbers``），
    故"钩子全覆盖、情节点只到一半"在门禁眼里是通过的——那种半供给必须在这里
    单独看得见（否则又是一次"只判有没有、不判够不够"）。
    """
    merged = content
    per_section: dict[str, set[int]] = {}
    for title, field in _SECTIONS:
        lines = _normalize_lines(getattr(draft, field, ""), lo=lo, hi=hi)
        per_section[title] = {n for ln in lines for n in _line_numbers(ln)}
        if lines:
            merged = _merge_section(merged, title, lines)
    return merged, per_section