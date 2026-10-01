"""WriterAgent —— 自主写章 Agent（Phase 1，Writer + Critic 内联）

把 Phase 0 工具层 + Phase 1 Agentic Loop 组装为一个**可自主写一章**的 Agent：

- Writer（创作模型）在 Agentic Loop 中**自主决定**调用哪些工具
  （``rag_retrieve`` 召回前文、``foreshadow_read`` 看伏笔、``count_words`` 自检字数、
  ``quality_check`` 自评），准备好后用 ``finish`` 提交章节正文。
- Critic（质检门禁）**内联**：外环按 tier（auto / heavy / light）对提交稿做质量门禁，
  不达标则把审稿意见回灌给 Writer 修订，循环直到通过或达到该 tier 的最大修订次数。
  这样既保留了 M5「生成→质检→修订」的质量保证，又把"何时调工具、是否自评"交还给模型。

与 M5 的关系（设计文档 §5 复用映射）：
- M5 的"硬编码七步生成 + 固定修订" → 被本 Agentic Loop 替代（**核心交付**）。
- M5 的上下文加载 / 证据链 / 落盘 / 进度 → 由 ``AgenticWriteWorkflow`` 复用，保证输出兼容。

质量基线：默认门禁为规则层 ``quality_check``（纯规则、零网络、必有）；生产环境由
``AgenticWriteWorkflow`` 注入 LLM 九项审稿门禁，使"质量不低于现 M5"。

离线友好：``decide``（决策函数）与 ``quality_gate``（门禁函数）均可注入，便于无 LLM 测试。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from pydantic import ValidationError
from rich.console import Console

from agent.core.engine.agent_loop import AgentAction, AgentLoop
from agent.core.engine.tool_contracts import (
    Tool,
    ToolRegistry,
    ToolResult,
    registry as default_registry,
)
from agent.client.gateway_adapter import create_gateway, chat_structured
from llmagent.gateway import Gateway
from agent.core.infra.prompt_manager import pm
from agent.core.base.structured_output import StructuredOutputError
from agent.core.base.exceptions import FatalProviderError
from agent.core.quality.scoring.quality_checker import (
    _count_cjk,
    _chapter_length_from_ctx,
    resolve_hard_cap_cjk_words,
    resolve_max_cjk_words,
    resolve_min_cjk_words,
)

# 写作人设（WriterAgent system 提示）
#
# ★ 2026-10-01 收口（批次收口二）：live 写手系统提示的唯一真源是
#   ``prompts/m5/generate.md`` 的 ``# system`` 段（输出协议 + 21 条写作要求）。
#   此前的 ``_WRITER_HEAD/_WRITER_TAIL`` 内联常量已迁入该模板——本文件只装配：
#   第 3-6 条仍按**本章强度档位**从 ``prompts/m5/pace_rules.md`` 渲染后注入。
#   绝不可把任何写作要求条目写回本文件——那就是两份真源。
def _writer_base(pace_relaxed: bool = False) -> str:
    """装配 Writer system 提示：第 3-6 条按**本章强度档位**分叉。

    - ``pace_relaxed=False``（未标档位 / 非放松档）⇒ 红线
      ``test_writer_base_unchanged_when_not_relaxed`` 用快照比对（2026-10-01
      随收口二重钉：generate.md 迁入 + 合并原死文件独有的元指令禁令 /
      Markdown 分段 / 最简记忆包 / 禁复述前情 / P-11 五条保护）。
    - ``pace_relaxed=True``（规划登记的放松档）⇒ 降低的是**强度**而非"有没有"：
      仍要求一个情绪落点与章末去向感，避免制造"判而不可修"的死结（纪律 #17）。

    真源缺失（提示词文件读不到）时**不静默放宽**：``pm.get`` 直接抛错由调用方
    显性失败——比悄悄少 4 条硬要求更安全（纪律 #1）。
    """
    from agent.core.infra.prompt_manager import pm

    rules = pm.get("m5.pace_rules").render_system(pace_relaxed=bool(pace_relaxed))
    return pm.get("m5.generate").render_system(
        pace_rules=rules.strip(), pace_relaxed=bool(pace_relaxed)
    )


# 各 tier 的最大起草次数（含首稿；修订次数 = 起草次数 - 1）
TIER_MAX_DRAFTS: dict[str, int] = {
    "light": 1,  # 仅首稿 + 单次自检，不修订
    "auto": 4,   # 首稿 + 最多 3 次修订（P0：model 偶发 stub 短稿，曾打满 3 次仍未达字数）
    "heavy": 4,  # 首稿 + 最多 3 次修订（更严）
}


class WriterAgent:
    """自主写章 Agent。

    Args:
        project_dir: 小说项目目录（注入工具上下文）。
        llm_client: LLM 客户端；不传则惰性创建（仅在有真实 LLM 时可用）。
        tools: 可用工具；默认用全局 registry 中的内置工具。
        tier: ``auto``（默认）/ ``heavy`` / ``light``。
        console: rich 控制台（CLI 进度输出；``--json`` 时传静默控制台）。
        decide / decide_async: 注入决策函数（离线测试用）；不传则包 ``llm_client.chat_structured``。
        quality_gate: 注入门禁函数 ``(text, ctx) -> (passed: bool, report: dict)``；
            不传则使用规则层 ``quality_check`` 工具（零网络）。
    """

    def __init__(
        self,
        project_dir: str | Path,
        llm_client: Gateway | None = None,
        tools: list[Tool] | ToolRegistry | None = None,
        tier: str = "auto",
        console: Console | None = None,
        decide: Callable[[list[dict[str, str]]], AgentAction] | None = None,
        decide_async: Callable[[list[dict[str, str]]], Awaitable[AgentAction]] | None = None,
        quality_gate: Callable[[str, Any], tuple[bool, dict[str, Any]]] | None = None,
        # 提速·定向修订：修订轮用「原稿+审稿意见」单次改写，替代整章 Agentic 重写；
        # 置 False 恢复旧的整章重写行为
        targeted_revise: bool = True,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.llm = llm_client
        self.tier = tier if tier in TIER_MAX_DRAFTS else "auto"
        self.console = console or Console()
        self.quality_gate = quality_gate
        self.targeted_revise = targeted_revise

        if isinstance(tools, ToolRegistry):
            self.tools: list[Tool] = tools.list()
            self.registry = tools
        elif isinstance(tools, list):
            self.tools = tools
            self.registry = ToolRegistry()
            for t in tools:
                self.registry.register(t)
        else:
            # 默认使用全局 registry 中的内置工具（导入 builtins 触发注册）
            import agent.core.tools.builtins  # noqa: F401

            self.registry = default_registry
            self.tools = default_registry.list()

        # 注入项目上下文，使工具能读取本项目文件
        from agent.core.tools.builtins import set_project_context

        set_project_context(self.project_dir)

        self._decide = decide
        self._decide_async = decide_async

    # ------------------------------------------------------------------
    # 决策函数（生产环境包 LLM 结构化输出；测试可注入）
    # ------------------------------------------------------------------
    def _make_decide(self) -> Callable[[list[dict[str, str]]], AgentAction]:
        if self._decide is not None:
            return self._decide
        if self.llm is None:
            self.llm = create_gateway()
        llm = self.llm

        def decide(messages: list[dict[str, str]]) -> AgentAction:
            retry_messages = messages
            for attempt in (0, 1):
                try:
                    data = chat_structured(
                        llm,
                        retry_messages,
                        AgentAction,
                        use="creative",
                        temperature=0.6,
                        max_tokens=8192,
                        enable_thinking=False,
                    )
                    return data
                except (ValidationError, StructuredOutputError) as ve:  # noqa: BLE001 - G4 精确捕获
                    if attempt == 1:
                        # 两次均失败 → 明确报错，让外环重试（T3 验收：不破坏 G3 降级不阻断）
                        self.console.print(
                            f"[yellow]Writer 结构化输出校验失败（重试一次后仍失败：{ve}）[/yellow]"
                        )
                        raise
                    # 首次失败 → 把真实校验错误附进重试指令（错误归因准确，
                    # 避免「JSON 其实合法只是缺字段」时模型被误导继续翻车）
                    self.console.print(
                        "[yellow]Writer 结构化输出解析失败，附错误详情重试…[/yellow]"
                    )
                    retry_messages = list(messages) + [
                        {
                            "role": "user",
                            "content": (
                                pm.get("agents.writer_retry").system
                                + f"\n\n【上一次的具体错误】{ve}"
                            ),
                        }
                    ]  # noqa: SILENT_DEGRADE

        return decide

    def _make_decide_async(self) -> Callable[[list[dict[str, str]]], Awaitable[AgentAction]]:
        if self._decide_async is not None:
            return self._decide_async
        if self.llm is None:
            self.llm = create_gateway()
        llm = self.llm

        async def decide_async(messages: list[dict[str, str]]) -> AgentAction:
            from asyncio import to_thread
            retry_messages = messages
            for attempt in (0, 1):
                try:
                    data = await to_thread(
                        chat_structured,
                        llm,
                        retry_messages,
                        AgentAction,
                        use="creative",
                        temperature=0.6,
                        max_tokens=8192,
                        enable_thinking=False,
                    )
                    return data
                except (ValidationError, StructuredOutputError) as ve:  # noqa: BLE001 - G4 精确捕获
                    if attempt == 1:
                        # 两次均失败 → 明确报错，让外环重试（T3 验收：不破坏 G3 降级不阻断）
                        self.console.print(
                            f"[yellow]Writer 结构化输出校验失败（重试一次后仍失败：{ve}）[/yellow]"
                        )
                        raise
                    # 首次失败 → 把真实校验错误附进重试指令（错误归因准确）
                    self.console.print(
                        "[yellow]Writer 结构化输出解析失败，附错误详情重试…[/yellow]"
                    )
                    retry_messages = list(messages) + [
                        {
                            "role": "user",
                            "content": (
                                pm.get("agents.writer_retry").system
                                + f"\n\n【上一次的具体错误】{ve}"
                            ),
                        }
                    ]  # noqa: SILENT_DEGRADE

        return decide_async

    # ------------------------------------------------------------------
    # 质量门禁（Critic 内联）
    # ------------------------------------------------------------------
    def _gate(self, text: str, ctx: Any) -> tuple[bool, dict[str, Any]]:
        if self.quality_gate is not None:
            return self.quality_gate(text, ctx)
        # 默认：规则层 quality_check 工具（零网络，必然可用）
        res: ToolResult = self.registry.call("quality_check", chapter_text=text)
        data = res.data if isinstance(res.data, dict) else {}
        return bool(data.get("passed", False)), data

    @staticmethod
    def _format_critique(report: dict[str, Any]) -> str:
        issues = report.get("issues", []) if isinstance(report, dict) else []
        if issues:
            lines = [f"- [{i.get('rule_id', '?')}] {i.get('severity', '')}：{i.get('description', '')}"
                     for i in issues]
            return "上一版未通过质量门禁，请逐项修订后重新提交：\n" + "\n".join(lines)
        return "上一版质量门禁未通过，请整体提升开篇钩子、情绪锚点、章末悬念与场景占比后重新提交。"

    # ------------------------------------------------------------------
    # 提速·定向修订：原稿+审稿意见 → 单次改写（不整章重写）
    # ------------------------------------------------------------------
    def _revise(self, text: str, critique: str) -> str:
        """按审稿意见定向修订原稿（复用 m5.revise 提示词，单次创作调用）。

        与整章 Agentic 重写相比省去工具循环与全量上下文重建，修订轮耗时
        通常降 30-50%；输出异常截短时抛错，由调用方回退整章重写。
        """
        from agent.client.gateway_adapter import chat_creative

        prompt = pm.get("m5.revise")
        resp = chat_creative(
            self.llm,
            messages=[
                {"role": "system", "content": prompt.system},
                {
                    "role": "user",
                    "content": prompt.render_user(
                        quality_report=critique, chapter_text=text
                    ),
                },
            ],
            temperature=0.6,
            max_tokens=4096,
            enable_thinking=False,
        )
        revised = (resp or "").strip()
        # 输出异常截短（不足原稿一半）→ 视为失败，回退整章重写
        if not revised or len(revised) < max(200, len(text) // 2):
            raise RuntimeError("定向修订输出异常截短")
        return revised

    def _top_up_length(self, text: str, target_words: int, min_words: int,
                       max_rounds: int = 2) -> str:
        """续写补字（2026-09-10）：输出过短时从正文结尾续写拼接，而非整章重写。

        背景：思考型模型（dots3-note-prev 等）思考 token 吃掉输出预算，
        间歇性产出低于字数下限的章节，此前只能反复重写并在仍不达标时
        整章放弃（五灵破 ch25/ch155 三次实证）。续写补字把「重写」降级为
        「追加」：保留已达标的部分，只补足差额，单轮成本远低于整章重写。

        Args:
            text: 当前最佳草稿（未达标）
            target_words: 目标字数
            min_words: 字数下限
            max_rounds: 最多续写轮数（每轮追加后重计字数）

        Returns:
            补足后的全文；仍不达标则原样返回（由调用方决定放弃）。
        """
        from agent.client.gateway_adapter import chat_creative

        merged = text
        for i in range(max_rounds):
            cur = _count_cjk(merged)
            if cur >= min_words:
                break
            need = max(300, min(target_words - cur, target_words))
            try:
                resp = chat_creative(
                    self.llm,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "你是网文续写器。只输出正文的自然延续，禁止任何解释、"
                                "标题、批注或对已有内容的复述；保持既有文风、人称与"
                                "情节走向，推进新的子事件（动作/对话/情绪反应），"
                                "禁止用重复描写、空泛抒情或大段心理独白注水。"
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                f"以下是一章的已写正文（尚未写完，禁止提前收尾）：\n\n"
                                f"{merged}\n\n"
                                f"请从结尾处无缝继续写约 {need} 字（当前全文 {cur} 字，"
                                f"目标约 {target_words} 字），直接输出续写内容。"
                            ),
                        },
                    ],
                    temperature=0.7,
                    max_tokens=8192,
                    enable_thinking=False,
                )
            except Exception as e:  # noqa: BLE001
                self.console.print(
                    f"[yellow]      …续写补字第 {i + 1} 轮调用失败（{e}），跳过[/yellow]"
                )
                continue  # noqa: SILENT_DEGRADE - 补字失败降级，由调用方走放弃/熔断链路
            piece = (resp or "").strip()
            # 续写异常截短（不足 200 字）视为本轮无效，避免拼入残句
            if len(piece) < 200:
                self.console.print(
                    f"[yellow]      …续写补字第 {i + 1} 轮输出过短（{len(piece)} 字），跳过[/yellow]"
                )
                continue
            merged = merged.rstrip() + "\n\n" + piece
            self.console.print(
                f"[dim]      · 续写补字第 {i + 1} 轮：+{_count_cjk(piece)} 字 → "
                f"全文 {_count_cjk(merged)} 字[/dim]"
            )
        return merged

    def _trim_length(self, text: str, target_words: int, min_words: int,
                     max_words: int | None = None, max_rounds: int = 1) -> str:
        """定向压缩（2026-09-15）：超硬上限时做一次压缩，而非原样落盘。

        对称于 :meth:`_top_up_length`（下限补字）。背景：写章侧此前**只有下限硬闸、
        上限全程无处置** —— 提示词第 17 条只约束「不足」，落盘前只判 ``< min_len``，
        超长稿一路直落（灵荒薪传 ch012 单章 32520 字、越权写到全书大结局，实证）。

        压缩只删冗余（重复描写／空泛抒情／大段心理独白／无信息量对白），**禁止新增
        情节、禁止提前收尾**；输出必须「短于原稿且不低于下限」才被采纳，否则原样返回
        （由调用方告警落盘，**绝不因压缩失败而拒绝落盘**）。

        Args:
            text: 当前超长草稿
            target_words: 目标字数
            min_words: 字数下限
            max_words: 字数合理上限（压缩目标，缺省用目标字数）
            max_rounds: 最多压缩轮数

        Returns:
            压缩后的全文；调用失败或输出无效则原样返回（由调用方决定告警）。
        """
        from agent.client.gateway_adapter import chat_creative

        cur = _count_cjk(text)
        upper = max_words or target_words
        for i in range(max_rounds):
            try:
                resp = chat_creative(
                    self.llm,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "你是网文精简器。只输出压缩后的完整正文，禁止任何解释、"
                                "标题、批注或对压缩过程的说明；严格保留既有情节、子事件、"
                                "人物、对话要点与章节结尾钩子，删除重复描写、空泛抒情、"
                                "大段心理独白与无信息量对白；禁止新增情节，禁止提前收尾。"
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                f"以下一章篇幅过长（当前 {cur} 字），请压缩到约 {upper} 字"
                                f"以内（且不低于 {min_words} 字），输出完整的压缩后正文：\n\n"
                                f"{text}"
                            ),
                        },
                    ],
                    temperature=0.5,
                    max_tokens=8192,
                    enable_thinking=False,
                )
            except Exception as e:  # noqa: BLE001
                self.console.print(
                    f"[yellow]      …篇幅压缩第 {i + 1} 轮调用失败（{e}），保留原稿[/yellow]"
                )
                continue  # noqa: SILENT_DEGRADE - 压缩失败降级，由调用方告警落盘
            trimmed = (resp or "").strip()
            new_len = _count_cjk(trimmed)
            # 采纳判据：确实变短（防模型原样回吐）且不低于下限（防压成残章）
            if new_len < cur and new_len >= min_words:
                self.console.print(
                    f"[dim]      · 篇幅压缩第 {i + 1} 轮：{cur} → {new_len} 字[/dim]"
                )
                return trimmed
            self.console.print(
                f"[yellow]      …篇幅压缩第 {i + 1} 轮输出无效（{new_len} 字，"
                f"未变短或低于下限），保留原稿[/yellow]"
            )
        return text

    @staticmethod
    def _target_from_ctx(ctx: Any) -> int | None:
        """解析本章目标字数（与 :meth:`_word_budget` 同口径；未知返回 None）。"""
        if isinstance(ctx, dict):
            return _chapter_length_from_ctx(ctx)
        if ctx is not None:
            v = getattr(ctx, "chapter_length", None)
            try:
                iv = int(v)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return None
            return iv if iv > 0 else None
        return None

    def _normalize_overlong(
        self, draft: str, ctx: Any, min_len: int,
        passed: bool, report: dict[str, Any],
    ) -> tuple[str, bool, dict[str, Any]]:
        """超硬上限 → 一次定向压缩 → **对压缩稿重判门禁**；无效则保留原稿。

        对称于过短分支的续写补字。压缩稿必须重新过闸（避免「未验证状态被当结论」）；
        压缩失败 / 输出无效 ⇒ 原样返回原稿（告警落盘，不 raise、不放弃）——
        与「宁断批不带病存档」不冲突：此处是篇幅问题而非确定性 blocking 缺陷。

        Returns:
            ``(draft, passed, report)``
        """
        target = self._target_from_ctx(ctx)
        hard_cap = resolve_hard_cap_cjk_words(target)
        if not hard_cap:
            return draft, passed, report
        cur = _count_cjk(draft)
        if cur <= hard_cap:
            return draft, passed, report
        self.console.print(
            f"[yellow]      …本章 {cur} 字，超硬上限 {hard_cap} 字（目标×2），"
            f"触发定向压缩[/yellow]"
        )
        trimmed = self._trim_length(
            draft,
            target_words=target or hard_cap,
            min_words=min_len,
            # 压缩目标对齐**目标中值**（而非合理上限×1.2）：此前用 ×1.2 会把压缩
            # 打向"贴着上限"，实测一次 18145→2297（target 2500）压成贴着下限的残章。
            # 以中值为锚，压缩稿落到目标区间中心附近，避免"白烧一个巨型 draft、
            # 又产出贴底章节"的双重浪费（2026-09-21）。
            max_words=target or hard_cap,
        )
        if trimmed == draft:
            self.console.print(
                "[yellow]      …压缩无有效产出，保留原稿并告警落盘[/yellow]"
            )
            return draft, passed, report
        new_passed, new_report = self._gate(trimmed, ctx)
        return trimmed, new_passed, new_report

    # ------------------------------------------------------------------
    # 单轮起草（携带或不携带审稿意见）
    # ------------------------------------------------------------------
    def _draft(self, task: str, critique: str | None, min_words: int | None = None,
               max_words: int | None = None, pace_relaxed: bool = False) -> str:
        system_prompt = self._system_prompt(critique, min_words, max_words, pace_relaxed=pace_relaxed)

        loop = AgentLoop(
            tools=self.tools,
            decide=self._make_decide(),
            max_iterations=10,
            fail_backoff_s=3.0,
            system_prompt=system_prompt,
            on_tool_call=self._on_tool_call,
            on_finish=self._on_finish,
        )
        # 弹性重试：瞬时 LLM 故障会导致循环 10 轮不收敛并直接中止整个写章批次；
        # 立即重开一轮（最多 1 次）把瞬时故障降级为延迟，而非批次失败
        result = loop.run(task)
        if (not result.finished or not result.draft) and not result.fatal_error:
            self.console.print(
                "[yellow]      …Agentic Loop 未收敛，重试一轮（瞬时故障保护）[/yellow]"
            )
            loop = AgentLoop(
                tools=self.tools,
                decide=self._make_decide(),
                max_iterations=10,
                fail_backoff_s=3.0,
                system_prompt=system_prompt,
                on_tool_call=self._on_tool_call,
                on_finish=self._on_finish,
            )
            result = loop.run(task)
        if not result.finished or not result.draft:
            detail = f"；最后一次决策失败：{result.last_error}" if result.last_error else ""
            if result.fatal_error:
                raise FatalProviderError(
                    f"Writer 因 Provider 致命错误中止（Agentic Loop 提前结束）{detail}"
                )
            raise RuntimeError(
                f"Writer 未在迭代上限内提交章节（Agentic Loop 未正常结束）{detail}"
            )
        return result.draft

    async def _draft_async(self, task: str, critique: str | None, min_words: int | None = None,
                           max_words: int | None = None, pace_relaxed: bool = False) -> str:
        system_prompt = self._system_prompt(critique, min_words, max_words, pace_relaxed=pace_relaxed)

        loop = AgentLoop(
            tools=self.tools,
            decide_async=self._make_decide_async(),
            max_iterations=10,
            fail_backoff_s=3.0,
            system_prompt=system_prompt,
        )
        result = await loop.run_async(task)
        if (not result.finished or not result.draft) and not result.fatal_error:
            self.console.print(
                "[yellow]      …Agentic Loop 未收敛，重试一轮（瞬时故障保护）[/yellow]"
            )
            loop = AgentLoop(
                tools=self.tools,
                decide_async=self._make_decide_async(),
                max_iterations=10,
                fail_backoff_s=3.0,
                system_prompt=system_prompt,
            )
            result = await loop.run_async(task)
        if not result.finished or not result.draft:
            detail = f"；最后一次决策失败：{result.last_error}" if result.last_error else ""
            if result.fatal_error:
                raise FatalProviderError(
                    f"Writer 因 Provider 致命错误中止（Agentic Loop 提前结束）{detail}"
                )
            raise RuntimeError(
                f"Writer 未在迭代上限内提交章节（Agentic Loop 未正常结束）{detail}"
            )
        return result.draft

    def _system_prompt(self, critique: str | None = None, min_words: int | None = None,
                       max_words: int | None = None, pace_relaxed: bool = False) -> str:
        """组装 Writer 系统提示。在写手人设基础上，把**具体字数数字**作为强约束
        注入（否则模型只看到相对描述『目标字数×0.8~1.2』，不知具体下限而产出偏短）。

        ``pace_relaxed``：本章是否为规划登记的放松档——决定第 3-6 条平权规则
        的分叉（真源 ``prompts/m5/pace_rules.md``）。
        """
        system_prompt = _writer_base(pace_relaxed=pace_relaxed)
        if min_words is not None and max_words is not None and max_words >= min_words:
            system_prompt += (
                f"\n17. 【字数硬性约束·上下限公共】本章正文中文字数**必须**落在 "
                f"{min_words}-{max_words} 字（中值约 {(min_words + max_words) // 2} 字）。"
                f"**提交前必须调用 count_words 工具自检实际字数**，分两种情形处理：\n"
                f"   · 若**不足下限** {min_words}：禁止用重复描写/空泛抒情/大段心理独白注水凑字；"
                f"从本章可用情节点素材（细纲情节点、钩子设计、爽点剧本、伏笔任务、"
                f"未回收钩子债/伏笔债、角色冲突）补充 3-6 个可推进剧情或情绪的子事件"
                f"（谁做了什么，一句话一个），织入正文扩写至达标再 commit。\n"
                f"     ⚠ 扩写素材**只能**来自细纲情节点与本章设计供给；"
                f"【前情提要】里的上一章原文是**事实参考，不是素材库**——"
                f"逐字/近逐字复用其中任何段落会被跨章重复检测（相似度≥0.85）"
                f"以 blocking 硬拒绝，整章作废（实证：第70章曾因此连续 4 轮被拒）。\n"
                f"   · 若**超出上限** {max_words}：同样禁止直接提交——超上限会被压缩，"
                f"既浪费算力又可能删掉情节。应**就地删减**冗余描写/注水/重复渲染，"
                f"保留全部情节、子事件、线索与章尾钩子，压缩到 {min_words}-{max_words} 字区间"
                f"再 commit；不得用删情节、砍章尾钩子或提前收尾来凑字数，"
                f"也不得挪用后续章节本应出现的内容。"
                f"若一次压缩仍未达标，重复修剪至区间内。\n"
            )
        if critique:
            system_prompt += "\n\n【审稿意见 · 请据此修订】\n" + critique
        return system_prompt

    # ------------------------------------------------------------------
    # 流式回调（CLI 进度）
    # ------------------------------------------------------------------
    def _on_tool_call(self, name: str, args: dict[str, Any]) -> None:
        self.console.print(f"[dim]  · Writer 调用工具 {name}[/dim]")

    def _on_finish(self, draft: Optional[str]) -> None:
        wc = len(draft) if draft else 0
        self.console.print(f"[dim]  · Writer 提交章节（{wc} 字）[/dim]")

    # ------------------------------------------------------------------
    # 公开入口：按 tier 撰写并返回（正文, 修订次数, 是否通过门禁）
    # ------------------------------------------------------------------
    @staticmethod
    def _min_len_from_ctx(ctx: Any) -> int:
        """从校验上下文中解析本章字数门禁下限（未知目标时用绝对下限兜底）。"""
        if isinstance(ctx, dict):
            target = _chapter_length_from_ctx(ctx)
        elif ctx is not None:
            target = getattr(ctx, "chapter_length", None)
        else:
            target = None
        return resolve_min_cjk_words(target)

    @staticmethod
    def _word_budget(ctx: Any) -> tuple[int | None, int | None]:
        """从上下文解析本章字数的【下限/上限】具体数字，供注入强约束提示词。

        与 _min_len_from_ctx 同口径；未知目标时返回 (None, None)，此时提示词不追加
        硬性字数数字（保持原有相对描述，门禁兜底仍在）。
        """
        if isinstance(ctx, dict):
            target = _chapter_length_from_ctx(ctx)
        elif ctx is not None:
            target = getattr(ctx, "chapter_length", None)
        else:
            target = None
        if not target:
            return None, None
        return resolve_min_cjk_words(target), resolve_max_cjk_words(target)

    @staticmethod
    def _keep_best(
        best_draft: str,
        best_report: dict[str, Any],
        cand_draft: str,
        cand_report: dict[str, Any],
        min_len: int,
    ) -> tuple[str, dict[str, Any]]:
        """好稿兜底：在候选与最佳间择优，优先保留「篇幅达标」且「质量更优」的草稿。

        规则（优先级从高到低）：
        1. 篇幅达标者优先于不达标者（stub 绝不让位）；
        2. 同达标时字数更多者优先（更接近目标篇幅）；
        3. 字数相同时问题数更少者优先。
        """
        cand_cjk = _count_cjk(cand_draft)
        best_cjk = _count_cjk(best_draft)
        cand_ok = cand_cjk >= min_len
        best_ok = best_cjk >= min_len
        if cand_ok and not best_ok:
            return cand_draft, cand_report
        if best_ok and not cand_ok:
            return best_draft, best_report
        if cand_cjk > best_cjk:
            return cand_draft, cand_report
        if cand_cjk == best_cjk:
            cand_issues = len(cand_report.get("issues", [])) if isinstance(cand_report, dict) else 0
            best_issues = len(best_report.get("issues", [])) if isinstance(best_report, dict) else 0
            if cand_issues < best_issues:
                return cand_draft, cand_report
        return best_draft, best_report

    #: 确定性 blocking 规则集：证据是确定性计算（非 LLM 主观分），
    #: "兜底落盘"等于把已证实的缺陷固化入库 → 一律拒绝兜底（2026-09-12）。
    _HARD_REFUSE_RULE_IDS = frozenset(
        {
            "cross_chapter_dup",        # 跨章段落重复（G14 前置）
            "repetition_abnormal",      # 章内重复句/注水（padding 前置）
            "consistency_timeline_conflict",   # 生死/时间线矛盾
            "consistency_field_conflict",      # 设定字段冲突
            "consistency_realm_span",          # 境界跨度越级（无契机的越级跳变）
        }
    )

    @staticmethod
    def _golden_refuse_save(report: dict[str, Any], ctx: Any) -> bool:
        """硬拒绝兜底落盘判定（金三 + 确定性 blocking 规则）。

        金三：前三章吸引力评分不达标（低质量开局固化 → 批末必然熔断修不到）。
        确定性规则：任意章节，cross_chapter_dup / repetition_abnormal /
        consistency_* blocking 命中即拒绝——这些不是主观分，是确定性证据。
        非 dict ctx / 章号未知时金三不拦（保守）；确定性规则与章号无关。"""
        if not isinstance(report, dict):
            return False
        issues = report.get("issues") or []
        for i in issues:
            if (
                isinstance(i, dict)
                and i.get("severity") == "blocking"
                and str(i.get("rule_id")) in WriterAgent._HARD_REFUSE_RULE_IDS
            ):
                return True
        if report.get("golden_gate_failed"):
            ch = (
                ctx.get("chapter_num")
                if isinstance(ctx, dict)
                else getattr(ctx, "chapter_num", None)
            )
            try:
                return bool(ch is not None and int(ch) <= 3)
            except (TypeError, ValueError):
                return False
        return False

    def run(self, task: str, ctx: Any = None) -> tuple[str, int, bool]:
        """自主撰写一章。

        Returns:
            (final_text, revision_attempts, quality_passed)
        """
        min_words, max_words = self._word_budget(ctx)
        # M3：本章强度档位（写手侧平权规则分叉信号）；未标档位 ⇒ False ⇒ 走原规则
        _relaxed = bool(isinstance(ctx, dict) and ctx.get("pace_relaxed"))
        draft = self._draft(task, critique=None, min_words=min_words, max_words=max_words,
                    pace_relaxed=_relaxed)
        revision_attempts = 0
        passed, report = self._gate(draft, ctx)

        if self.tier == "light":
            draft, passed, report = self._normalize_overlong(
                draft, ctx, self._min_len_from_ctx(ctx), passed, report
            )
            return draft, 0, passed

        # 好稿兜底：追踪篇幅达标的最佳草稿，避免主观审稿误杀好稿导致整章作废
        min_len = self._min_len_from_ctx(ctx)
        best_draft, best_report = draft, report

        max_drafts = TIER_MAX_DRAFTS.get(self.tier, 3)
        for r in range(1, max_drafts):
            if passed:
                break
            critique = self._format_critique(report)
            # 提速·定向修订：优先「原稿+意见」单次改写；失败回退整章重写
            if self.targeted_revise:
                try:
                    draft = self._revise(draft, critique)
                except Exception as e:  # noqa: BLE001 - 修订失败回退整章重写
                    self.console.print(
                        f"[dim]  · 定向修订失败（{e}），回退整章重写[/dim]"
                    )
                    draft = self._draft(
                        task, critique=critique, min_words=min_words, max_words=max_words, pace_relaxed=_relaxed
                    )  # noqa: SILENT_DEGRADE
            else:
                draft = self._draft(
                    task, critique=critique, min_words=min_words, max_words=max_words, pace_relaxed=_relaxed
                )
            revision_attempts = r
            passed, report = self._gate(draft, ctx)
            best_draft, best_report = self._keep_best(
                best_draft, best_report, draft, report, min_len
            )

        # 兜底落盘：全轮未通过时，用篇幅达标的最佳稿兜底（标记未通过）；全是 stub 才放弃
        if not passed:
            # 硬判定：金三/确定性 blocking 规则不达标 ⇒ 拒绝兜底落盘（宁断批不带病存档）
            if self._golden_refuse_save(report, ctx):
                raise RuntimeError(
                    "质量门禁硬拒绝：存在确定性 blocking 缺陷"
                    "（金三吸引力/跨章重复/重复句注水/一致性矛盾），"
                    f"已修订 {revision_attempts} 轮仍未达标，放弃落盘以避免缺陷固化。"
                )
            best_draft, best_report = self._keep_best(
                best_draft, best_report, draft, report, min_len
            )
            if _count_cjk(best_draft) < min_len:
                # 续写补字（2026-09-10）：先追加续写补足差额，仍不达标才放弃落盘。
                # 根因：思考型模型思考 token 吃输出预算，间歇性产出过短章节，
                # 整章重写同样过短只能放弃（五灵破 ch25/ch155 三次实证）。
                _target = _chapter_length_from_ctx(ctx) or (min_len * 5 // 4)
                best_draft = self._top_up_length(
                    best_draft, target_words=_target, min_words=min_len
                )
            if _count_cjk(best_draft) < min_len:
                raise RuntimeError(
                    "Writer 反复产出过短章节（未达字数下限），放弃落盘以避免写出残缺章节。"
                )
            self.console.print(
                "[yellow]      …质量门禁未通过，回退到本轮篇幅达标的最佳稿兜底落盘（标记未通过）。[/yellow]"
            )
            draft, report = best_draft, best_report
        # 篇幅规范化（2026-09-15）：超硬上限 → 一次定向压缩 → 压缩稿重判门禁；
        # 压缩无效则保留原稿告警落盘（不阻断）。对称于过短分支的续写补字。
        draft, passed, report = self._normalize_overlong(
            draft, ctx, min_len, passed, report
        )
        return draft, revision_attempts, passed

    async def run_async(self, task: str, ctx: Any = None) -> tuple[str, int, bool]:
        min_words, max_words = self._word_budget(ctx)
        # M3：本章强度档位（写手侧平权规则分叉信号）；未标档位 ⇒ False ⇒ 走原规则
        _relaxed = bool(isinstance(ctx, dict) and ctx.get("pace_relaxed"))
        draft = await self._draft_async(task, critique=None, min_words=min_words, max_words=max_words,
                               pace_relaxed=_relaxed)
        revision_attempts = 0
        passed, report = self._gate(draft, ctx)

        if self.tier == "light":
            draft, passed, report = self._normalize_overlong(
                draft, ctx, self._min_len_from_ctx(ctx), passed, report
            )
            return draft, 0, passed

        # 好稿兜底：追踪篇幅达标的最佳草稿，避免主观审稿误杀好稿导致整章作废
        min_len = self._min_len_from_ctx(ctx)
        best_draft, best_report = draft, report

        max_drafts = TIER_MAX_DRAFTS.get(self.tier, 3)
        for r in range(1, max_drafts):
            if passed:
                break
            critique = self._format_critique(report)
            # 提速·定向修订：优先「原稿+意见」单次改写；失败回退整章重写
            if self.targeted_revise:
                try:
                    draft = self._revise(draft, critique)
                except Exception as e:  # noqa: BLE001 - 修订失败回退整章重写
                    self.console.print(
                        f"[dim]  · 定向修订失败（{e}），回退整章重写[/dim]"
                    )
                    draft = await self._draft_async(
                        task, critique=critique, min_words=min_words, max_words=max_words, pace_relaxed=_relaxed
                    )  # noqa: SILENT_DEGRADE
            else:
                draft = await self._draft_async(
                    task, critique=critique, min_words=min_words, max_words=max_words, pace_relaxed=_relaxed
                )
            revision_attempts = r
            passed, report = self._gate(draft, ctx)
            best_draft, best_report = self._keep_best(
                best_draft, best_report, draft, report, min_len
            )

        # 兜底落盘：全轮未通过时，用篇幅达标的最佳稿兜底（标记未通过）；全是 stub 才放弃
        if not passed:
            # 硬判定：金三/确定性 blocking 规则不达标 ⇒ 拒绝兜底落盘（宁断批不带病存档）
            if self._golden_refuse_save(report, ctx):
                raise RuntimeError(
                    "质量门禁硬拒绝：存在确定性 blocking 缺陷"
                    "（金三吸引力/跨章重复/重复句注水/一致性矛盾），"
                    f"已修订 {revision_attempts} 轮仍未达标，放弃落盘以避免缺陷固化。"
                )
            best_draft, best_report = self._keep_best(
                best_draft, best_report, draft, report, min_len
            )
            if _count_cjk(best_draft) < min_len:
                # 续写补字（2026-09-10）：先追加续写补足差额，仍不达标才放弃落盘。
                # 根因：思考型模型思考 token 吃输出预算，间歇性产出过短章节，
                # 整章重写同样过短只能放弃（五灵破 ch25/ch155 三次实证）。
                _target = _chapter_length_from_ctx(ctx) or (min_len * 5 // 4)
                best_draft = self._top_up_length(
                    best_draft, target_words=_target, min_words=min_len
                )
            if _count_cjk(best_draft) < min_len:
                raise RuntimeError(
                    "Writer 反复产出过短章节（未达字数下限），放弃落盘以避免写出残缺章节。"
                )
            self.console.print(
                "[yellow]      …质量门禁未通过，回退到本轮篇幅达标的最佳稿兜底落盘（标记未通过）。[/yellow]"
            )
            draft, report = best_draft, best_report
        # 篇幅规范化：超硬上限 → 一次定向压缩 → 压缩稿重判门禁；无效则保留原稿告警落盘。
        draft, passed, report = self._normalize_overlong(
            draft, ctx, min_len, passed, report
        )
        return draft, revision_attempts, passed
