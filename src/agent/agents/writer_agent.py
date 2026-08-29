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
from agent.client import LLMClient
from agent.core.base.structured_output import StructuredOutputError

# 写作人设（与 M5 创作系统提示同源，保证风格一致）
_WRITER_BASE = (
    "你是顶级修仙小说写手，擅长用精炼的场景描写、个性台词和节奏控制写出生动的章节。\n\n"
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
    "12. 直接输出正文，不要标题、不要前言、不要解释\n"
    "13. 【纯中文约束】仅输出简体中文小说正文，禁止输出任何英文字母串、系统提示片段、"
    "错误/日志信息（如 \"Visualization failed\"、\"Cost\"、\"undefined\"、\"[system]\" 等）、"
    "markdown 代码围栏以外的符号或占位标记；若误生成请立即删除重写。\n"
    "14. 【标题约束】每章正文首行必须是「# 第N章 · <有信息量、非模板化的场景化标题>」；"
    "禁止「第N章·第N章」这类占位标题，禁止留空，禁止与已发布章节标题重复。\n"
    "15. 【避免雷同】避免与前述章节使用完全相同的开场白/场景描写（如都从同一句环境白描起笔）；"
    "若需描写相似场景，请换视角、换措辞或换切入点，确保本章开头具有独立性。\n\n"
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
    "auto": 3,   # 首稿 + 最多 2 次修订
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
        llm_client: LLMClient | None = None,
        tools: list[Tool] | ToolRegistry | None = None,
        tier: str = "auto",
        console: Console | None = None,
        decide: Callable[[list[dict[str, str]]], AgentAction] | None = None,
        decide_async: Callable[[list[dict[str, str]]], Awaitable[AgentAction]] | None = None,
        quality_gate: Callable[[str, Any], tuple[bool, dict[str, Any]]] | None = None,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.llm = llm_client
        self.tier = tier if tier in TIER_MAX_DRAFTS else "auto"
        self.console = console or Console()
        self.quality_gate = quality_gate

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
            self.llm = LLMClient()
        llm = self.llm

        def decide(messages: list[dict[str, str]]) -> AgentAction:
            retry_messages = messages
            for attempt in (0, 1):
                try:
                    data = llm.chat_structured(
                        retry_messages,
                        AgentAction,
                        use="creative",
                        temperature=0.82,
                        max_tokens=6000,
                        enable_thinking=False,
                        strict=True,  # G4 开启 strict=True 强校验
                    )
                    return AgentAction(**data)
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
                        {"role": "user", "content": _RETRY_JSON_PROMPT}
                    ]

        return decide

    def _make_decide_async(self) -> Callable[[list[dict[str, str]]], Awaitable[AgentAction]]:
        if self._decide_async is not None:
            return self._decide_async
        if self.llm is None:
            self.llm = LLMClient()
        llm = self.llm

        async def decide_async(messages: list[dict[str, str]]) -> AgentAction:
            retry_messages = messages
            for attempt in (0, 1):
                try:
                    data = await llm.chat_structured_async(
                        retry_messages,
                        AgentAction,
                        use="creative",
                        temperature=0.82,
                        max_tokens=6000,
                        enable_thinking=False,
                        strict=True,
                    )
                    return AgentAction(**data)
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
                        {"role": "user", "content": _RETRY_JSON_PROMPT}
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
    # 单轮起草（携带或不携带审稿意见）
    # ------------------------------------------------------------------
    def _draft(self, task: str, critique: str | None) -> str:
        system_prompt = _WRITER_BASE
        if critique:
            system_prompt += "\n\n【审稿意见 · 请据此修订】\n" + critique

        loop = AgentLoop(
            tools=self.tools,
            decide=self._make_decide(),
            max_iterations=10,
            system_prompt=system_prompt,
            on_tool_call=self._on_tool_call,
            on_finish=self._on_finish,
        )
        result = loop.run(task)
        if not result.finished or not result.draft:
            raise RuntimeError("Writer 未在迭代上限内提交章节（Agentic Loop 未正常结束）")
        return result.draft

    async def _draft_async(self, task: str, critique: str | None) -> str:
        system_prompt = _WRITER_BASE
        if critique:
            system_prompt += "\n\n【审稿意见 · 请据此修订】\n" + critique
        loop = AgentLoop(
            tools=self.tools,
            decide_async=self._make_decide_async(),
            max_iterations=10,
            system_prompt=system_prompt,
        )
        result = await loop.run_async(task)
        if not result.finished or not result.draft:
            raise RuntimeError("Writer 未在迭代上限内提交章节（Agentic Loop 未正常结束）")
        return result.draft

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
    def run(self, task: str, ctx: Any = None) -> tuple[str, int, bool]:
        """自主撰写一章。

        Returns:
            (final_text, revision_attempts, quality_passed)
        """
        draft = self._draft(task, critique=None)
        revision_attempts = 0
        passed, report = self._gate(draft, ctx)

        if self.tier == "light":
            return draft, 0, passed

        max_drafts = TIER_MAX_DRAFTS.get(self.tier, 3)
        for r in range(1, max_drafts):
            if passed:
                break
            critique = self._format_critique(report)
            draft = self._draft(task, critique=critique)
            revision_attempts = r
            passed, report = self._gate(draft, ctx)

        return draft, revision_attempts, passed

    async def run_async(self, task: str, ctx: Any = None) -> tuple[str, int, bool]:
        draft = await self._draft_async(task, critique=None)
        revision_attempts = 0
        passed, report = self._gate(draft, ctx)

        if self.tier == "light":
            return draft, 0, passed

        max_drafts = TIER_MAX_DRAFTS.get(self.tier, 3)
        for r in range(1, max_drafts):
            if passed:
                break
            critique = self._format_critique(report)
            draft = await self._draft_async(task, critique=critique)
            revision_attempts = r
            passed, report = self._gate(draft, ctx)

        return draft, revision_attempts, passed
