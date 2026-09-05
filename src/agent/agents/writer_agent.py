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
    resolve_max_cjk_words,
    resolve_min_cjk_words,
)

# 写作人设（与 M5 创作系统提示同源，保证风格一致）
_WRITER_BASE = (
    "【输出协议 · 最高优先级，任何违反都会导致本章作废】\n"
    "你的每一次输出都必须且只能是**单个裸 JSON 信封对象**，字段名逐字一致：\n"
    '{"think": "简短思考", "action": "finish 或 tool_call", "tool": null, '
    '"args": {}, "draft": "完整章节正文"}。\n'
    "除裸 JSON 外，禁止输出任何散文、规划思路、思考过程、```json 代码围栏、"
    "工具调用日志或解释文字。\n"
    "若 action 为 tool_call，则只填 tool 与 args，draft 置 null；"
    "若 action 为 finish，则 tool 置 null、args 置 {}，draft 填**完整章节正文**。\n\n"
    "你是一名顶级修仙小说写手，专职产出上述 JSON 信封 ``draft`` 字段里的章节正文。"
    "以下创作要求均作用于 draft 中的正文。\n\n"
    "写作要求：\n"
    "1. 严格遵守设定集（文风/视角/节奏/字数/禁用词/禁用元素）\n"
    "2. 本章必须属于当前压力曲线阶段，按阶段控制张力\n"
    "3. 前 500 字内出现冲突/悬念/反差之一\n"
    "4. 本章至少含一个爽/虐/燃/甜/惊锚点\n"
    "5. 章末必须有悬念/反转/期待之一\n"
    "6. 场景+动作+环境描写合计 ≥ 30%\n"
    "7. 禁用词\"突然/忽然/就在这时/微微一笑\"全章 ≤ 2 次\n"
    "8. 角色台词必须符合其语言指纹\n"
    "9. 不与世界观 / 支线 / 角色档案冲突\n"
    "10. 如需埋/回收伏笔，自然融入剧情\n"
    "11. 高潮章节自动扩篇幅 + 多视角 + 慢镜头\n"
    "12. 【格式提醒】再次强调：每次输出都必须是顶部「输出协议」规定的单个裸 JSON 信封，"
    "严禁把正文或规划写成纯文本。\n"
    "13. 【纯中文约束】正文部分仅输出简体中文小说正文，禁止输出任何英文字母串、系统提示片段、"
    "错误/日志信息（如 \"Visualization failed\"、\"Cost\"、\"undefined\"、\"[system]\" 等）、"
    "符号或占位标记；若误生成请立即删除重写。\n"
    "14. 【标题约束】draft 中章节正文首行必须是「# 第N章 · <有信息量、非模板化的场景化标题>」；"
    "禁止「第N章·第N章」这类占位标题，禁止留空，禁止与已发布章节标题重复。\n"
    "15. 【避免雷同】避免与前述章节使用完全相同的开场白/场景描写（如都从同一句环境白描起笔）；"
    "若需描写相似场景，请换视角、换措辞或换切入点，确保本章开头具有独立性。\n"
    "16. 【字数区间要求】draft 中本章正文的中文字数应在「目标字数×0.8 到 目标字数×1.2」之间"
    "（以目标字数为中值的合理区间）；写不足下限就会被打回扩写。请围绕目标字数铺足"
    "场景/动作/对白/情节，写够再收尾，禁止用「伏笔或悬念一句带过」来压缩篇幅；也不宜过度注水超过上限。\n\n"
    "你拥有若干工具（见下方动作协议中的可用工具）。写之前可调用工具核对设定 / 召回前文 / "
    "自检字数 / 自评质量；准备好后，把 action 设为 'finish' 并在 draft 中提交**完整章节正文**。"
)

# 结构化解析失败后的强制 JSON 回退指令：模型常把「正文当纯文本」而非 JSON 信封输出
# （尤其 creative + 高 temperature 时）。首次失败后追加该指令重试一次，强制其只吐 JSON。
# 符合 G4 / M14 既定约定「解析失败重试一次（要求纯 JSON），两次均失败则明确报错」。
_RETRY_JSON_PROMPT = (
    "\n\n【输出格式硬约束】上一条输出没能被解析为 JSON，此条必须只输出一个合法 JSON 对象。"
    "禁止任何解释文字、禁止 ```json 代码围栏、禁止把章节正文直接作为纯文本输出。"
    "JSON 结构如下（字段名必须逐字一致）：\n"
    '{"think": "简短思考", "action": "finish 或 tool_call", '
    '"tool": null, "args": {}, "draft": "完整章节正文或工具参数"}'
    "\n若 action 为 tool_call，则填 tool/args 并留空 draft；若为 finish，则 draft 填完整章节正文。"
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
                    # 首次失败 → 追加「强制纯 JSON」指令重试一次，避免同参数重跑再翻车
                    self.console.print(
                        "[yellow]Writer 结构化输出解析失败，追加纯 JSON 指令重试…[/yellow]"
                    )
                    retry_messages = list(messages) + [
                        {"role": "user", "content": pm.get("agents.writer_retry").system}
                    ]

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
                    # 首次失败 → 追加「强制纯 JSON」指令重试一次
                    self.console.print(
                        "[yellow]Writer 结构化输出解析失败，追加纯 JSON 指令重试…[/yellow]"
                    )
                    retry_messages = list(messages) + [
                        {"role": "user", "content": pm.get("agents.writer_retry").system}
                    ]

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

    # ------------------------------------------------------------------
    # 单轮起草（携带或不携带审稿意见）
    # ------------------------------------------------------------------
    def _draft(self, task: str, critique: str | None, min_words: int | None = None,
               max_words: int | None = None) -> str:
        system_prompt = self._system_prompt(critique, min_words, max_words)

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
                           max_words: int | None = None) -> str:
        system_prompt = self._system_prompt(critique, min_words, max_words)

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
                       max_words: int | None = None) -> str:
        """组装 Writer 系统提示。在 _WRITER_BASE 基础上，把**具体字数数字**作为强约束
        注入（否则模型只看到相对描述『目标字数×0.8~1.2』，不知具体下限而产出偏短）。
        """
        system_prompt = _WRITER_BASE
        if min_words is not None and max_words is not None and max_words >= min_words:
            system_prompt += (
                f"\n17. 【字数硬性约束】本章正文的中文字数**必须**在 {min_words}-{max_words} 字"
                f"（中值约 {(min_words + max_words) // 2} 字）。若自检发现字数不足，"
                f"**禁止用重复描写、空泛抒情或大段心理独白注水凑字**；应先从本章可用的"
                f"情节点素材（细纲情节点序列、细纲钩子设计、爽点剧本、伏笔任务、"
                f"未回收钩子债/伏笔债、角色冲突）中补充 3-6 个可推进剧情或情绪的子事件"
                f"（谁做了什么，一句话一个），再把这些情节点织入正文扩写，务必写足下限"
                f"再 commit，禁止用『伏笔/悬念一句带过』压缩篇幅。\n"
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

    def run(self, task: str, ctx: Any = None) -> tuple[str, int, bool]:
        """自主撰写一章。

        Returns:
            (final_text, revision_attempts, quality_passed)
        """
        min_words, max_words = self._word_budget(ctx)
        draft = self._draft(task, critique=None, min_words=min_words, max_words=max_words)
        revision_attempts = 0
        passed, report = self._gate(draft, ctx)

        if self.tier == "light":
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
                        task, critique=critique, min_words=min_words, max_words=max_words
                    )
            else:
                draft = self._draft(
                    task, critique=critique, min_words=min_words, max_words=max_words
                )
            revision_attempts = r
            passed, report = self._gate(draft, ctx)
            best_draft, best_report = self._keep_best(
                best_draft, best_report, draft, report, min_len
            )

        # 兜底落盘：全轮未通过时，用篇幅达标的最佳稿兜底（标记未通过）；全是 stub 才放弃
        if not passed:
            best_draft, best_report = self._keep_best(
                best_draft, best_report, draft, report, min_len
            )
            if _count_cjk(best_draft) < min_len:
                raise RuntimeError(
                    "Writer 反复产出过短章节（未达字数下限），放弃落盘以避免写出残缺章节。"
                )
            self.console.print(
                "[yellow]      …质量门禁未通过，回退到本轮篇幅达标的最佳稿兜底落盘（标记未通过）。[/yellow]"
            )
            draft, report = best_draft, best_report
        return draft, revision_attempts, passed

    async def run_async(self, task: str, ctx: Any = None) -> tuple[str, int, bool]:
        min_words, max_words = self._word_budget(ctx)
        draft = await self._draft_async(task, critique=None, min_words=min_words, max_words=max_words)
        revision_attempts = 0
        passed, report = self._gate(draft, ctx)

        if self.tier == "light":
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
                        task, critique=critique, min_words=min_words, max_words=max_words
                    )
            else:
                draft = await self._draft_async(
                    task, critique=critique, min_words=min_words, max_words=max_words
                )
            revision_attempts = r
            passed, report = self._gate(draft, ctx)
            best_draft, best_report = self._keep_best(
                best_draft, best_report, draft, report, min_len
            )

        # 兜底落盘：全轮未通过时，用篇幅达标的最佳稿兜底（标记未通过）；全是 stub 才放弃
        if not passed:
            best_draft, best_report = self._keep_best(
                best_draft, best_report, draft, report, min_len
            )
            if _count_cjk(best_draft) < min_len:
                raise RuntimeError(
                    "Writer 反复产出过短章节（未达字数下限），放弃落盘以避免写出残缺章节。"
                )
            self.console.print(
                "[yellow]      …质量门禁未通过，回退到本轮篇幅达标的最佳稿兜底落盘（标记未通过）。[/yellow]"
            )
            draft, report = best_draft, best_report
        return draft, revision_attempts, passed
