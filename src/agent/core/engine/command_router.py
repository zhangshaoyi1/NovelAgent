"""命令路由器（M16）

职责：解析用户输入 → 校验阶段门禁 → 分发到工作流或对话处理器。

输入：用户原始消息
输出：路由结果（类型、目标处理函数、门禁拒绝原因）

F16.1 统一语法：斜杠命令 + 参数（/command --arg value）
F16.2 命令清单：按阶段过滤可用性
F16.3 命令路由：未到对应阶段时拒绝并提示当前阶段
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from agent.core.engine.state_machine import State, StateMachine


# ============================================================
# 命令元数据（F16.2 命令清单）
# ============================================================
@dataclass(frozen=True)
class CommandMeta:
    """命令元数据"""

    name: str  # 含前导斜杠，如 /write
    description: str
    usage: str = ""  # 用法示例
    # T-1 扩展：门禁派生字段（T-6 起为唯一门禁真相源）
    allowed_states: "tuple[State, ...] | None" = None  # 允许执行命令的状态；None 表示无状态约束
    is_global: bool = False  # True 表示任意状态可用（辅助/全局命令）


# 全量命令清单（与 PRD F16.2 表一致）
# 注：本列表为「基线元数据」，命令模块经 @command 装饰器注册时若命令名已存在则
# 以装饰器声明覆盖（allowed_states / is_global），不存在则追加。T-6 起门禁改由
# CommandMeta.allowed_states / is_global 派生（唯一真相源）。
COMMAND_REGISTRY: list[CommandMeta] = [
    CommandMeta("/start", "开新书", "/start [--dir <dir>]", allowed_states=(State.INIT,)),
    CommandMeta("/discuss", "进入脉络讨论", "/discuss [--dir <dir>] [--max-rounds <n>]", allowed_states=(State.CONFIGURING, State.DISCUSSING)),
    CommandMeta("/architecture", "生成/迭代故事架构", "/architecture [--dir <dir>] [--feedback <text>]", allowed_states=(State.ARCHITECTING,)),
    CommandMeta("/confirm-architecture", "确认故事架构（门禁）", "/confirm-architecture [--dir <dir>]", allowed_states=(State.ARCHITECTING, State.ARCH_REVISION)),
    CommandMeta("/revise-architecture", "修订已确认架构", "/revise-architecture [--dir <dir>]", allowed_states=(State.ARCH_CONFIRMED, State.WRITING)),
    CommandMeta("/outline", "生成大纲", "/outline [--dir <dir>]", allowed_states=(State.ARCH_CONFIRMED,)),
    CommandMeta("/design-characters", "角色设计", "/design-characters [--dir <dir>]", allowed_states=(State.OUTLINING,)),
    CommandMeta("/write", "写下一章", "/write [--dir <dir>]", allowed_states=(State.CHARACTER_DESIGN, State.WRITING)),
    CommandMeta("/adjust-route", "调整主角路线", "/adjust-route --dir <dir> --intent <text>", allowed_states=(State.WRITING,)),
    CommandMeta("/adjust-relation", "调整关系网", "/adjust-relation --dir <dir> --intent <text>", allowed_states=(State.WRITING,)),
    CommandMeta("/mode", "切换介入频率", "/mode [--dir <dir>] [--target heavy|light|auto]", is_global=True),
    CommandMeta("/load-skill", "加载 skill", "/load-skill <name>", is_global=True),
    CommandMeta("/bookworm-review", "书虫测评", "/bookworm-review --book <name> --title <t> --text <text>", allowed_states=(State.WRITING,)),
    CommandMeta("/rollback", "回滚到分叉点", "/rollback <chapter>", allowed_states=(State.WRITING,)),
    CommandMeta("/resume", "续作恢复", "/resume", allowed_states=(State.PAUSED,)),
    CommandMeta("/export", "导出", "/export <format>", allowed_states=(State.WRITING, State.COMPLETED)),
    CommandMeta("/audit", "一致性审计", "/audit", allowed_states=(State.WRITING,)),
    CommandMeta("/snapshot", "创建设定集快照", "/snapshot [--dir <dir>] [--label <text>]", is_global=True),
    CommandMeta("/list-snapshots", "列出快照", "/list-snapshots [--dir <dir>]", is_global=True),
    CommandMeta("/rollback-setting", "回滚设定集", "/rollback-setting --dir <dir> --snapshot <name>", is_global=True),
    CommandMeta("/frozen-fields", "查看冻结字段", "/frozen-fields [--dir <dir>]", is_global=True),
    CommandMeta("/unfreeze", "解冻字段", "/unfreeze --dir <dir> --field <name>", is_global=True),
    CommandMeta("/foreshadow-report", "伏笔回收报告", "/foreshadow-report [--dir <dir>]", is_global=True),
    CommandMeta("/foreshadow-check", "伏笔检查", "/foreshadow-check [--dir <dir>] [--subline <id>]", is_global=True),
    CommandMeta("/status", "查看项目状态", "/status [--dir <dir>]", is_global=True),
    CommandMeta("/help", "列出当前可用命令", "/help", is_global=True),
    CommandMeta("/reset-state", "重置到上一稳定点", "/reset-state", is_global=True),
]


def command_allowed_in_state(cmd: str, state: "State | str") -> bool:
    """基于命令元数据判断命令在某状态下是否可用（T-1 提供，T-6 正式启用）。

    Args:
        cmd: 命令名（含前导斜杠，如 /write）。
        state: State 枚举实例或状态字符串。

    Returns:
        该状态下是否允许执行此命令。
    """
    meta = get_command_meta(cmd)
    if meta is None:
        return False
    if meta.is_global:
        return True
    if meta.allowed_states is None:
        return False
    if isinstance(state, State):
        return state in meta.allowed_states
    try:
        return State(state) in meta.allowed_states
    except ValueError:
        return False


def get_command_meta(name: str) -> CommandMeta | None:
    """按名称查询命令元数据"""
    for cmd in COMMAND_REGISTRY:
        if cmd.name == name:
            return cmd
    return None


def commands_for_state(state_value: str) -> list[CommandMeta]:
    """返回指定状态下可用的命令元数据（按 COMMAND_REGISTRY 顺序，T-6 元数据驱动）

    门禁由 CommandMeta.allowed_states / is_global 派生：全局命令始终可用，
    状态命令在其 allowed_states 包含当前状态时可用。
    """
    try:
        state = State(state_value)
    except ValueError:
        return []
    return [
        cmd
        for cmd in COMMAND_REGISTRY
        if cmd.is_global
        or (cmd.allowed_states is not None and state in cmd.allowed_states)
    ]


# ============================================================
# 路由结果
# ============================================================
@dataclass
class RouteResult:
    """命令路由结果"""

    type: str  # 'command' | 'dialog' | 'rejected'
    command: str | None = None
    args: dict[str, Any] | None = None
    handler: Callable[..., Any] | None = None
    reason: str | None = None  # 门禁拒绝原因


# ============================================================
# 参数解析（F16.1）
# ============================================================
# 匹配 --key value 或 --key=value 或 -k value
_ARG_PATTERN = re.compile(
    r"(?:^|\s)(?P<key>--[\w-]+|-\w)(?:=(?P<val_eq>[^\s]+)|\s+(?P<val_space>[^\s-][^\s]*))?"
)


def parse_args(text: str) -> dict[str, Any]:
    """解析命令参数（F16.1 统一语法）

    支持：
        --key value      → {"key": "value"}
        --key=value      → {"key": "value"}
        --flag           → {"flag": True}
        -k value         → {"k": "value"}
        裸文本           → {"raw": "裸文本"}

    Args:
        text: 命令后的参数字符串（不含命令本身）

    Returns:
        参数字典
    """
    args: dict[str, Any] = {}
    text = text.strip()
    if not text:
        return args

    # 用正则迭代提取 --key value / --key=value / -k value
    matches = list(_ARG_PATTERN.finditer(text))
    consumed_spans: list[tuple[int, int]] = []
    for m in matches:
        key_raw = m.group("key")
        # 去掉前导 -- 或 -
        if key_raw.startswith("--"):
            key = key_raw[2:]
        else:
            key = key_raw[1:]
        val = m.group("val_eq") or m.group("val_space")
        if val is not None:
            args[key] = val
        else:
            args[key] = True
        consumed_spans.append((m.start(), m.end()))

    # 收集未被参数匹配消费的裸文本
    consumed_spans.sort()
    bare_parts: list[str] = []
    prev_end = 0
    for start, end in consumed_spans:
        if start > prev_end:
            segment = text[prev_end:start].strip()
            if segment:
                bare_parts.append(segment)
        prev_end = max(prev_end, end)
    if prev_end < len(text):
        tail = text[prev_end:].strip()
        if tail:
            bare_parts.append(tail)

    if bare_parts:
        args["raw"] = " ".join(bare_parts)

    return args


# ============================================================
# 命令路由器
# ============================================================
class CommandRouter:
    """命令路由器

    工作流：
        1. 解析用户输入（识别斜杠命令 vs 自由对话）
        2. 查询状态机门禁
        3. 路由到对应处理函数
    """

    def __init__(self, state_machine: StateMachine) -> None:
        self.sm = state_machine
        # 命令 → 处理函数 注册表
        self.handlers: dict[str, Callable[..., Any]] = {}

    def register(self, command: str, handler: Callable[..., Any]) -> None:
        """注册命令处理函数"""
        self.handlers[command] = handler

    def parse(self, user_input: str) -> tuple[str, dict[str, Any]] | None:
        """解析用户输入（F16.1）

        Returns:
            (command, args) 若是命令；None 若是自由对话
        """
        text = user_input.strip()
        if not text.startswith("/"):
            return None
        # 保留前导斜杠，与命令名（含前导 /）及 handlers 键一致
        parts = text.split(maxsplit=1)
        command = parts[0]
        if len(parts) > 1:
            args = parse_args(parts[1])
        else:
            args = {}
        return command, args

    def route(self, user_input: str) -> RouteResult:
        """路由用户输入（F16.3）

        Args:
            user_input: 用户原始消息

        Returns:
            RouteResult
        """
        parsed = self.parse(user_input)
        if parsed is None:
            return RouteResult(type="dialog")

        command, args = parsed

        # 门禁查询
        if not self.sm.is_command_allowed(command):
            allowed = self.sm.allowed_commands()
            return RouteResult(
                type="rejected",
                command=command,
                reason=(
                    f"命令 {command} 在当前状态 {self.sm.state.value} 下不可用。"
                    f"可用命令: {', '.join(allowed)}"
                ),
            )

        handler = self.handlers.get(command)
        if handler is None:
            return RouteResult(
                type="rejected",
                command=command,
                reason=f"命令 {command} 未注册处理函数",
            )

        return RouteResult(
            type="command",
            command=command,
            args=args,
            handler=handler,
        )

    def allowed_commands_meta(self) -> list[CommandMeta]:
        """当前状态下可用命令的元数据（F16.2）"""
        return commands_for_state(self.sm.state.value)
