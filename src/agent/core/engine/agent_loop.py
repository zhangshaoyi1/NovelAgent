"""Agentic Loop 引擎（Phase 1）

通用 ReAct 式 Agent 循环：plan → act（工具调用 / 结束） → observe → reflect。

把 Phase 0 沉淀的「工具层（Tool / ToolRegistry）」与「结构化输出（chat_structured）」
组装为真正的自主决策循环：LLM 每轮返回一个结构化动作（调用某个工具 / 提交最终结果），
引擎执行工具、把结果回灌对话，直到 LLM 提交最终结果或达到最大迭代。

这是 NovelAgent 从「Pipeline + LLM」迈向「Agent」的核心：模型不再被硬编码的七步流程
驱动，而是**自主决定调用哪些工具、何时交付**。

设计要点
--------
- **依赖注入** ``decide(messages) -> AgentAction``：生产环境包 ``LLMClient.chat_structured``，
  测试环境可注入脚本化函数，实现完全离线测试。
- **同步 / 异步**：``run``（同步）+ ``run_async``（异步）。工具本身支持同步与异步
  （``Tool.is_async``），引擎自动调度。
- **流式回调**：``on_iteration`` / ``on_tool_call`` / ``on_observation`` / ``on_finish``，
  供 CLI 实时打印进度（满足「异步化 + 流式输出」交付项）。
- **降级友好**：``decide`` 返回非法动作 / 解析失败 / 工具未知时，都把**观察**回灌给模型
  让其自我纠错，而不是崩溃——与项目既有的「网络/工具降级不阻断」策略一致。
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from pydantic import BaseModel, Field

from agent.core.engine.tool_contracts import Tool, ToolRegistry, ToolResult


class AgentAction(BaseModel):
    """LLM 每轮返回的结构化决策（动作协议）。

    模型必须严格按此结构输出（由 ``chat_structured`` 的 JSON Schema 强制）：
    - ``action == "tool_call"``：调用 ``tool``（参数 ``args``）。
    - ``action == "finish"``：结束循环，``draft`` 为最终交付物。
    """

    think: str = Field(default="", description="简短思考，便于追踪决策链路")
    action: str = Field(..., description="'tool_call' 调用工具，或 'finish' 结束并交付")
    tool: Optional[str] = Field(default=None, description="要调用的工具名（action=tool_call 时必填）")
    args: dict[str, Any] = Field(default_factory=dict, description="工具参数（JSON 对象）")
    draft: Optional[str] = Field(default=None, description="最终交付物（action=finish 时必填）")


@dataclass
class LoopStep:
    """单轮循环记录（可序列化，便于审计 / 调试 / 测试断言）。"""

    iteration: int
    think: str
    action: str
    tool: Optional[str] = None
    args: dict[str, Any] = field(default_factory=dict)
    observation: Any = None
    draft: Optional[str] = None


@dataclass
class LoopResult:
    """循环最终结果。"""

    finished: bool
    draft: Optional[str] = None
    steps: list[LoopStep] = field(default_factory=list)
    iterations: int = 0
    last_error: Optional[str] = None  # 最后一次 decide 异常文本（诊断瞬时故障用）

    def to_dict(self) -> dict[str, Any]:
        return {
            "finished": self.finished,
            "draft": self.draft,
            "iterations": self.iterations,
            "steps": [
                {
                    "iteration": s.iteration,
                    "think": s.think,
                    "action": s.action,
                    "tool": s.tool,
                    "args": s.args,
                    "observation": s.observation,
                    "draft": s.draft,
                }
                for s in self.steps
            ],
        }


# decide 签名：接收完整 messages，返回 AgentAction。
DecideFn = Callable[[list[dict[str, str]]], AgentAction]
DecideAsyncFn = Callable[[list[dict[str, str]]], Awaitable[AgentAction]]


class AgentLoop:
    """通用自主决策循环引擎。

    Args:
        tools: 可用工具列表或 ToolRegistry。
        decide: 同步决策函数（注入 LLM 结构化调用）。离线测试时替换为脚本化函数。
        decide_async: 异步决策函数。提供后 ``run_async`` 可用。
        max_iterations: 最大轮次（超出则 finished=False 返回，避免死循环）。
        system_prompt: 系统提示（含 Agent 人设 + 可用工具说明 + 动作协议）。
        on_iteration / on_tool_call / on_observation / on_finish: 流式回调。
    """

    def __init__(
        self,
        tools: list[Tool] | ToolRegistry,
        decide: DecideFn | None = None,
        decide_async: DecideAsyncFn | None = None,
        max_iterations: int = 12,
        system_prompt: str = "",
        on_iteration: Callable[[LoopStep], None] | None = None,
        on_tool_call: Callable[[str, dict[str, Any]], None] | None = None,
        on_observation: Callable[[Any], None] | None = None,
        on_finish: Callable[[Optional[str]], None] | None = None,
        fail_backoff_s: float = 0.0,
    ) -> None:
        if isinstance(tools, ToolRegistry):
            self.tools = tools.list()
            self.registry = tools
        else:
            self.tools = list(tools)
            self.registry = ToolRegistry()
            for t in self.tools:
                self.registry.register(t)
        self.decide = decide
        self.decide_async = decide_async
        self.max_iterations = max_iterations
        self.system_prompt = system_prompt or self._default_system_prompt()
        # decide 连续失败时的指数退避基数（秒；0=关闭，保持旧行为）。
        # 用途：provider 间歇性 403/429 风暴（如免费池过载）会在数秒内耗尽全部
        # 迭代并静默失败；退避后可在迭代预算内「等过去」短时故障窗口。
        self.fail_backoff_s = max(0.0, float(fail_backoff_s))
        self.cb = {
            "iteration": on_iteration,
            "tool_call": on_tool_call,
            "observation": on_observation,
            "finish": on_finish,
        }

    # ------------------------------------------------------------------
    # 工具说明（注入 system prompt，让模型知道能调哪些工具）
    # ------------------------------------------------------------------
    def _tools_description(self) -> str:
        lines: list[str] = []
        for t in self.tools:
            lines.append(f"- {t.name}：{t.description}")
            lines.append(f"  参数(JSON Schema)：{json.dumps(t.parameters_schema, ensure_ascii=False)}")
        return "\n".join(lines)

    def _default_system_prompt(self) -> str:
        return (
            "你是一个能自主调用工具的 Agent。每轮你必须输出一个结构化动作 JSON：\n"
            "{\n"
            '  "think": "简短思考",\n'
            '  "action": "tool_call" 或 "finish",\n'
            '  "tool": "工具名（action=tool_call 时）",\n'
            '  "args": { ... 工具参数 ... },\n'
            '  "draft": "最终交付物（action=finish 时）"\n'
            "}\n\n"
            "可用工具：\n"
            f"{self._tools_description()}\n\n"
            "规则：先按需调用工具收集信息，准备好后把 action 设为 'finish' 并提交 draft。"
        )

    # ------------------------------------------------------------------
    # 消息组装（ReAct 历史：assistant 动作 ↔ user 观察）
    # ------------------------------------------------------------------
    def _build_messages(
        self, task: str, history: list[tuple[str, str]]
    ) -> list[dict[str, str]]:
        msgs: list[dict[str, str]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": task},
        ]
        for role, content in history:
            msgs.append({"role": role, "content": content})
        return msgs

    @staticmethod
    def _obs_text(obs: Any) -> str:
        if isinstance(obs, ToolResult):
            payload = obs.to_dict()
        elif isinstance(obs, (dict, list)):
            payload = obs
        else:
            payload = {"value": str(obs)}
        return json.dumps(payload, ensure_ascii=False)

    # ------------------------------------------------------------------
    # 工具执行（同步 / 异步）
    # ------------------------------------------------------------------
    def _call_tool(self, name: str, args: dict[str, Any]) -> ToolResult:
        if name not in self.registry.names():
            return ToolResult(success=False, error=f"未知工具：{name}")
        try:
            return self.registry.call(name, **args)
        except Exception as e:  # 工具异常也降级为观察，不阻断循环
            return ToolResult(success=False, error=f"工具执行异常：{e}")

    async def _call_tool_async(self, name: str, args: dict[str, Any]) -> ToolResult:
        if name not in self.registry.names():
            return ToolResult(success=False, error=f"未知工具：{name}")
        try:
            return await self.registry.call_async(name, **args)
        except Exception as e:
            return ToolResult(success=False, error=f"工具执行异常：{e}")

    # ------------------------------------------------------------------
    # 同步主循环
    # ------------------------------------------------------------------
    def run(self, task: str, initial_observation: str | None = None) -> LoopResult:
        if self.decide is None:
            raise RuntimeError("同步循环需要注入 decide（LLM 决策函数）")
        history: list[tuple[str, str]] = []
        if initial_observation:
            history.append(("user", f"初始上下文：\n{initial_observation}"))

        result = LoopResult(finished=False)
        for i in range(1, self.max_iterations + 1):
            messages = self._build_messages(task, history)
            try:
                action = self.decide(messages)
            except Exception as e:  # decide 失败也降级，避免循环直接崩
                result.last_error = str(e)[:300]
                history.append(("user", f"决策失败（{e}），请严格按动作协议重新输出 JSON。"))
                if self.fail_backoff_s > 0:
                    time.sleep(min(self.fail_backoff_s * (2 ** (i - 1)), 30.0))
                continue

            step = LoopStep(
                iteration=i,
                think=action.think,
                action=action.action,
                tool=action.tool,
                args=action.args,
            )

            if action.action == "finish":
                draft = action.draft
                if not draft:
                    step.observation = "finish 但未提供 draft，请补充后重新 finish。"
                    history.append(("assistant", action.model_dump_json()))
                    history.append(("user", step.observation))
                    if self.cb["iteration"]:
                        self.cb["iteration"](step)
                    continue
                step.draft = draft
                history.append(("assistant", action.model_dump_json()))
                result.steps.append(step)
                result.finished = True
                result.draft = draft
                result.iterations = i
                if self.cb["iteration"]:
                    self.cb["iteration"](step)
                if self.cb["finish"]:
                    self.cb["finish"](draft)
                return result

            # action == tool_call
            obs = self._call_tool(action.tool or "", action.args or {})
            step.observation = obs.to_dict()
            history.append(("assistant", action.model_dump_json()))
            history.append(("user", f"工具 {action.tool} 返回：\n{self._obs_text(obs)}"))
            result.steps.append(step)
            if self.cb["iteration"]:
                self.cb["iteration"](step)
            if self.cb["tool_call"]:
                self.cb["tool_call"](action.tool or "", action.args or {})
            if self.cb["observation"]:
                self.cb["observation"](obs.to_dict())

        result.iterations = self.max_iterations
        return result

    # ------------------------------------------------------------------
    # 异步主循环
    # ------------------------------------------------------------------
    async def run_async(
        self, task: str, initial_observation: str | None = None
    ) -> LoopResult:
        if self.decide_async is None:
            raise RuntimeError("异步循环需要注入 decide_async")
        history: list[tuple[str, str]] = []
        if initial_observation:
            history.append(("user", f"初始上下文：\n{initial_observation}"))

        result = LoopResult(finished=False)
        for i in range(1, self.max_iterations + 1):
            messages = self._build_messages(task, history)
            try:
                action = await self.decide_async(messages)
            except Exception as e:
                result.last_error = str(e)[:300]
                history.append(("user", f"决策失败（{e}），请严格按动作协议重新输出 JSON。"))
                if self.fail_backoff_s > 0:
                    await asyncio.sleep(min(self.fail_backoff_s * (2 ** (i - 1)), 30.0))
                continue

            step = LoopStep(
                iteration=i,
                think=action.think,
                action=action.action,
                tool=action.tool,
                args=action.args,
            )

            if action.action == "finish":
                draft = action.draft
                if not draft:
                    step.observation = "finish 但未提供 draft，请补充后重新 finish。"
                    history.append(("assistant", action.model_dump_json()))
                    history.append(("user", step.observation))
                    if self.cb["iteration"]:
                        self.cb["iteration"](step)
                    continue
                step.draft = draft
                history.append(("assistant", action.model_dump_json()))
                result.steps.append(step)
                result.finished = True
                result.draft = draft
                result.iterations = i
                if self.cb["iteration"]:
                    self.cb["iteration"](step)
                if self.cb["finish"]:
                    self.cb["finish"](draft)
                return result

            obs = await self._call_tool_async(action.tool or "", action.args or {})
            step.observation = obs.to_dict()
            history.append(("assistant", action.model_dump_json()))
            history.append(("user", f"工具 {action.tool} 返回：\n{self._obs_text(obs)}"))
            result.steps.append(step)
            if self.cb["iteration"]:
                self.cb["iteration"](step)
            if self.cb["tool_call"]:
                self.cb["tool_call"](action.tool or "", action.args or {})
            if self.cb["observation"]:
                self.cb["observation"](obs.to_dict())

        result.iterations = self.max_iterations
        return result
