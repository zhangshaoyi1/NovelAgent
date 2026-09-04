"""AGENT Task：ReAct 主循环 + Toolset + TurnValidator + Scratchpad + StopPolicy

M3.2 新增模块。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from .redlines import MAX_AGENT_TURNS
from .task import Executor, FailurePolicy, TaskKind, TaskRun, TaskSpec, TaskStatus


# ===== Tool =====


@dataclass
class ToolSpec:
    """工具规格"""
    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)


class Tool(Protocol):
    """工具协议"""
    name: str
    spec: ToolSpec

    async def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        ...


# ===== ToolsetPolicy =====


class ToolsetPolicy:
    """工具集策略：静态清单 + 动态发现"""

    def __init__(self) -> None:
        self._static_tools: dict[str, Tool] = {}
        self._mcp_discovery: Any = None

    def register(self, tool: Tool) -> None:
        self._static_tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._static_tools.get(name)

    def list_available(self) -> list[str]:
        return list(self._static_tools.keys())

    def set_mcp_discovery(self, discovery: Any) -> None:
        self._mcp_discovery = discovery


# ===== ToolCall =====


@dataclass
class ToolCall:
    """工具调用记录"""
    tool_name: str
    args: dict[str, Any]
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    elapsed_ms: float = 0.0


# ===== Scratchpad =====


class Scratchpad:
    """暂存区：消息历史 + 轮粒度 Checkpoint

    结构：
    - turns: 每轮的完整记录 (input, thought, tools, output)
    - checkpoints: 每轮粒度 Checkpoint
    """

    def __init__(self) -> None:
        self._turns: list[dict[str, Any]] = []
        self._checkpoints: list[dict[str, Any]] = []

    def add_turn(self, entry: dict[str, Any]) -> None:
        self._turns.append(entry)
        self._checkpoints.append({
            "turn": len(self._turns),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "summary": entry.get("output", "")[:100],
        })

    def get_turns(self) -> list[dict[str, Any]]:
        return list(self._turns)

    def get_checkpoints(self) -> list[dict[str, Any]]:
        return list(self._checkpoints)

    def get_last_turn(self) -> dict[str, Any] | None:
        return self._turns[-1] if self._turns else None

    def get_history(self, max_turns: int = 10) -> list[dict[str, Any]]:
        return self._turns[-max_turns:]

    def clear(self) -> None:
        self._turns.clear()
        self._checkpoints.clear()


# ===== StopPolicy =====


@dataclass
class StopDecision:
    """停止判定"""
    should_stop: bool = False
    reason: str = ""


class StopPolicy:
    """停止判定策略"""

    def __init__(self, max_turns: int = MAX_AGENT_TURNS) -> None:
        self._max_turns = max_turns

    def should_stop(self, turn_index: int, last_output: dict[str, Any] | None) -> StopDecision:
        """判断是否应停止"""
        if turn_index >= self._max_turns:
            return StopDecision(should_stop=True, reason=f"达到最大轮次 ({self._max_turns})")

        if last_output is None:
            return StopDecision(should_stop=True, reason="无输出")

        if last_output.get("status") == "completed":
            return StopDecision(should_stop=True, reason="任务完成")

        if last_output.get("status") == "failed":
            return StopDecision(should_stop=True, reason="任务失败")

        return StopDecision(should_stop=False)


# ===== TurnValidator =====


class TurnValidator:
    """轮级校验器"""

    @staticmethod
    def validate_turn(turn: dict[str, Any]) -> list[str]:
        """校验单轮有效性"""
        issues: list[str] = []
        if not turn.get("tool_name") and not turn.get("output"):
            issues.append("轮次无工具调用也无输出")
        if turn.get("tool_name") and "error" in turn and turn["error"]:
            issues.append(f"工具调用失败: {turn['tool_name']}")
        return issues

    @staticmethod
    def validate_scratchpad(scratchpad: Scratchpad) -> list[str]:
        """校验整个暂存区"""
        all_issues: list[str] = []
        for turn in scratchpad.get_turns():
            issues = TurnValidator.validate_turn(turn)
            all_issues.extend(issues)
        return all_issues


# ===== AgentLoopExecutor =====


class AgentLoopExecutor(Executor):
    """AGENT Task 主循环（ReAct 循环）

    流程：
    1. 接收输入
    2. 循环：思考 → 调用工具/生成回复 → 收集结果 → 检查停止
    3. 返回最终输出
    """

    kind = TaskKind.AGENT

    def __init__(
        self,
        toolset_policy: ToolsetPolicy | None = None,
        stop_policy: StopPolicy | None = None,
        turn_validator: TurnValidator | None = None,
        scratchpad: Scratchpad | None = None,
        llm_think: Any = None,  # 思考用的 LLM 调用
    ) -> None:
        self._toolset = toolset_policy or ToolsetPolicy()
        self._stop_policy = stop_policy or StopPolicy()
        self._turn_validator = turn_validator or TurnValidator()
        self._scratchpad = scratchpad or Scratchpad()
        self._llm_think = llm_think

    @property
    def scratchpad(self) -> Scratchpad:
        return self._scratchpad

    @property
    def toolset(self) -> ToolsetPolicy:
        return self._toolset

    async def execute(self, run: TaskRun) -> TaskRun:
        """执行 ReAct 循环"""
        input_data = run.output
        user_input = input_data.get("input", "")

        turn_index = 0
        last_output: dict[str, Any] | None = {"status": "running"}

        while True:
            # 检查停止
            decision = self._stop_policy.should_stop(turn_index, last_output)
            if decision.should_stop:
                run.output = {
                    "success": True,
                    "output": last_output.get("output", ""),
                    "turns": turn_index,
                    "stop_reason": decision.reason,
                }
                run.status = TaskStatus.SUCCEEDED
                return run

            # 构建当前轮次
            turn_entry: dict[str, Any] = {
                "turn": turn_index,
                "input": user_input if turn_index == 0 else last_output,
                "thought": "",
                "tool_name": "",
                "tool_args": {},
                "output": "",
                "error": "",
            }

            # 思考（LLM 调用）
            if self._llm_think:
                thought = await self._llm_think({"input": turn_entry["input"], "tools": self._toolset.list_available()})
                turn_entry["thought"] = thought.get("thought", "")
                action = thought.get("action", "")
                if action and action in self._toolset.list_available():
                    tool = self._toolset.get(action)
                    if tool:
                        try:
                            result = await tool.execute(thought.get("args", {}))
                            turn_entry["tool_name"] = action
                            turn_entry["tool_args"] = thought.get("args", {})
                            turn_entry["output"] = str(result)
                        except Exception as e:
                            turn_entry["error"] = str(e)
                            turn_entry["output"] = f"工具调用失败: {e}"

            # 记录轮次
            self._scratchpad.add_turn(turn_entry)
            last_output = turn_entry
            turn_index += 1

    def set_llm_think(self, llm_think: Any) -> None:
        """设置思考用的 LLM 调用"""
        self._llm_think = llm_think


# ===== 内置工具 =====


class EchoTool:
    """Echo 工具（测试用）"""
    name = "echo"
    spec = ToolSpec(name="echo", description="回声工具", input_schema={"type": "object", "required": ["message"]})

    async def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"echo": args.get("message", "")}


class WriteTool:
    """写工具（占位，实际需 Gateway）"""
    name = "write"
    spec = ToolSpec(name="write", description="生成内容", input_schema={"type": "object", "required": ["prompt"]})

    async def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"content": f"[写工具占位: {args.get('prompt', '')[:50]}]"}