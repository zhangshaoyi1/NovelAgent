# Agent Note: 统一命令接口方案：一个接口 · 多个消费者（CLI / Web / MCP）
Status: proposed

> 状态：**待审阅**（未开始开发）
> 基线标签：`pre-unified-command-20260829`（重构前恢复点）
> 作者：agent · 日期：2026-08-29

***

## 0. 一句话目标

把「命令」定义成一份**协议无关的契约**（输入 schema + 结构化结果 + 事件流 + 门禁），
CLI、Web、以及未来的 MCP 各自只实现一个**适配器（消费者）接入它。
业务逻辑、状态机推进、事件落盘全部收敛到契约实现里，三端共享**同一份数据和**同一条执行路径**，
彻底移除「Web 用子进程包一层 CLI」的旧架构。

***

## 1. 现状问题（为什么改）

1. **双入口逻辑漂移**：`web/state.py` 直接 import `state_machine / agent_service / m8_mode / genre_pack / merge_genres`，与 CLI 的 gate / 状态推导 / next\_steps 各写一份，必然漂移。
2. **子进程 + 文本/文件轮询**：`web/runner.py` spawn `python -m agent.cli ...`，`strip_rich` 剥文本 + 每 0.4s 轮询 `.state/progress.json`：

   * 双份易碎契约（rich 文本格式 + progress.json schema），CLI 一改输出 Web 就坏；

   * 每次操作冷启动整个 Python 进程 + 导入 67 个命令模块；

   * 事件被切成两半（stdout 日志 + progress 轮询），`events.jsonl` 的结构化事件（`llm.retry` / `failure`）根本没接进 Web，Web 上的"日志"是残缺的。
3. **加 MCP 成本高**：若继续子进程思路，每加一个消费者就要复制一遍命令调用 + 解析逻辑。

> 已核实的接缝：`ProgressEventBus.on_event` 回调已经是「消费者注入输出通道」的天然抽象点（CLI 注入 rich 渲染器），只要把它接到 Web 的 SSE 队列、MCP 的 progress 通知即可。

***

## 2. 目标架构总览

```
                ┌──────────────────────────────────────────────┐
                │  core/command/（契约层，零业务依赖）           │
                │  Command ABC · ParamSpec · CommandContext      │
                │  CommandResult · CommandError · Dispatcher     │
                └───────────────┬──────────────────────────────┘
                               │ dispatch(name, inputs, ctx)
             ┌─────────────────┼──────────────────┬──────────────┐
             ▼                 ▼                  ▼              ▼
      ┌─────────────┐   ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
      │ CLI 消费者   │   │ Web 消费者   │   │ MCP 消费者   │   │ (未来:其他)  │
      │ typer 解析+  │   │ 表单自动生成+ │   │ 工具定义自动  │   │  又一个适配器 │
      │ rich 渲染    │   │ SSE/JSON     │   │ 生成+调用     │   │             │
      └─────────────┘   └─────────────┘   └─────────────┘   └─────────────┘
             └───────────────┬──────────────────┘
                             ▼
              agent/commands/*（业务实现，编排 workflows）
                             ▼
           workflows → agents → core → client → base（现有分层不变）
```

* **契约（接口）**：`core/command/`。一个命令 = 一个 `Command` 类，声明输入参数、门禁、执行逻辑、结构化结果。

* **业务实现**：`agent/commands/`。具体命令的 `run()` 编排对应 workflow。

* **消费者（实现）**：CLI / Web / MCP 各自只做「协议适配」：把 argv / 表单 / JSON 参数转成 `inputs`，把结果和事件流渲染成 rich / SSE / 工具返回值。

* **数据共享**：三端读写**同一项目目录**下的同一批文件（world.md / outline.md / chapters / .state / .events），写操作一律走 Command，杜绝"CLI 写一套、Web 另写一套"。

***

## 3. 分层与目录（严守现有依赖方向）

现有依赖方向：`entry points → workflows → agents → core → client → base`。

| 层       | 目录                      | 内容                                           | 依赖                                                |
| ------- | ----------------------- | -------------------------------------------- | ------------------------------------------------- |
| 契约层     | `core/command/`（新增）     | `base.py` / `registry.py` / `dispatcher.py`  | 仅 `base/` + `core.engine.state_machine`（State 枚举） |
| 业务实现    | `agent/commands/`（新增）   | `StartCommand` `WriteCommand` …（8 → 25 → 67） | `workflows` `agents` `core` `client` `base`       |
| CLI 消费者 | `cli/commands/*.py`（改造） | typer 薄适配器：argv→inputs→dispatch→rich 渲染      | `agent/commands` + `core/command`                 |
| Web 消费者 | `web/`（改造）              | FastAPI：表单→inputs→进程内 dispatch→SSE           | `agent/commands` + `core/command`                 |
| MCP 消费者 | `agent/mcp/`（未来新增）      | 由 registry 自动生成工具定义                          | `core/command`                                    |

**为什么具体命令不放** **`core/`？**
`core/` 在依赖链里**低于** `workflows/`，而命令实现必须编排 workflow。放进 `core/` 会造成 `core → workflows` 反向依赖（历史教训：`base/retry.py` 依赖 `event_sourcing/` 被判违规）。因此：**契约在** **`core/command/`（纯净），实现在** **`agent/commands/`（编排）**。

**为什么要有** **`agent/commands/`** **这一层？**
CLI 与 Web 是兄弟节点，谁也不该依赖谁。共享的业务实现必须放在两者都能依赖的公共层。`agent/commands/` 就是这个公共层（等价于 Java 里的 service 实现，CLI/Web 是它的两个 controller）。

***

## 4. 接口定义（契约代码骨架）

### 4.1 输入契约 ParamSpec（一份声明，三种输入）

```python
# core/command/base.py
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ParamSpec:
    """命令参数声明：CLI 的 --chapters / Web 表单字段 / MCP 参数 都由此派生。"""
    name: str                       # "chapters"（CLI 为 --chapters，Web/MCP 为同名字段）
    type: str = "str"               # "str" | "int" | "float" | "bool" | "path" | "choice" | "list"
    default: Any = None
    help: str = ""
    required: bool = False
    choices: tuple[str, ...] | None = None   # type=="choice" 时给 Web 下拉 / CLI 校验
    positional: bool = False        # True 表示 CLI 位置参数（如 discuss 的 topic）
    flag: bool = False              # True 表示 CLI 开关（--xxx），对应 Web checkbox / MCP boolean
```

* **CLI**：`for p in cmd.params: typer.Option(p.default, f"--{p.name}", help=p.help, ...)` → 自动生成参数，不再手写 20 行 Option。

* **Web**：`for p in cmd.params: <input type=... name=p.name>` → 表单字段自动生成，字段类型/默认值/帮助文案与 CLI 完全一致。

* **MCP**：`param_spec_to_json_schema(p)` → 工具参数定义自动生成。

> 原则：**ParamSpec 是输入的唯一事实来源**，三端不得各自定义输入格式。

### 4.2 执行上下文 CommandContext（消费者注入的 I/O）

```python
@dataclass
class CommandContext:
    project_dir: Path
    on_event: Callable[[dict], None] | None = None   # 事件流输出通道（消费者注入）
    env: dict[str, str] | None = None                 # 透传 .env / 运行时环境
    progress_file: str | None = None                  # progress.json（默认保留，Web 不再依赖轮询）
    quiet: bool = False                               # CLI --quiet / Web 静默
    interactive: bool = False
```

`on_event` 是**唯一的事件流出口**：

* CLI 消费者注入 rich 渲染器（进度条 / 失败信封 / 摘要）；

* Web 消费者注入 asyncio 队列 → SSE 推送；

* MCP 消费者注入 progress 通知回调。

事件同时仍走 `ProgressEventBus → EventBus → .events/events.jsonl`（统一落盘不变）。

### 4.3 结果与失败契约

```python
@dataclass
class CommandResult:
    """结构化结果：CLI 渲染成 rich / Web 直接 JSON / MCP 工具返回值。"""
    data: dict[str, Any]           # 原 --json 信封字段（字段名逐字节不变，保住测试）
    events: list[dict] | None = None
    summary: dict | None = None


class CommandError(Exception):
    """业务失败信封（替代 typer.Exit(code)）：携带结构化 code/message/next_steps。"""
    def __init__(self, code: str, message: str, *, exit_code: int = 1,
                 next_steps: list[str] | None = None, details: dict | None = None):
        ...
```

### 4.4 Command 抽象基类

```python
class Command(ABC):
    name: str                                    # "write"
    description: str = ""
    params: list[ParamSpec] = []
    allowed_states: tuple[State, ...] | None = None   # 门禁（迁移自 CommandMeta）
    is_global: bool = False

    def check_gate(self, ctx: CommandContext) -> GateDecision | None:
        """门禁校验；返回 None 表示放行，返回拒绝给 Dispatcher 统一处理。
        特例（如 architecture/outline/design_characters 的 --feedback 绕过）在此覆写。"""
        return None

    @abstractmethod
    def run(self, ctx: CommandContext, **inputs: Any) -> CommandResult:
        """唯一核心：编排 workflow，返回结构化结果。抛 CommandError 表示业务失败。"""
        ...
```

***

## 5. 调度器 Dispatcher（三端共用同一执行入口）

```python
# core/command/dispatcher.py
class CommandDispatcher:
    def dispatch(self, name: str, ctx: CommandContext, **inputs: Any) -> CommandResult:
        cmd = registry.get(name)          # 不存在 → CommandError("unknown_command")
        self._validate(cmd, inputs)       # ParamSpec 类型/必填/choices 校验（三端统一）
        gate = cmd.check_gate(ctx)        # 门禁
        if gate and gate.blocked:
            raise CommandError(gate.code, gate.message, next_steps=gate.next_steps)
        return cmd.run(ctx, **inputs)     # 同步执行（CLI / MCP）

    async def dispatch_async(self, name, ctx, **inputs) -> CommandResult:
        return await asyncio.to_thread(self.dispatch, name, ctx, **inputs)  # Web 用
```

要点：

* **门禁**从 CLI 命令函数搬到 Dispatcher 统一执行，CLI 与 Web 的放行/拒绝行为逐字节一致（含 `--feedback` 等特例，通过 `check_gate` 钩子）。

* **参数校验**也统一：类型转换（"10" → 10）、必填、choices 越界报错，三端相同报错信息。

* `dispatch_async` 用 `asyncio.to_thread` 包同步 `run()`，Web 无需改 workflow 为 async；事件经 `ctx.on_event` 跨线程安全地转发（asyncio.run\_coroutine\_threadsafe → queue → SSE）。

***

## 6. 事件流（共享输出）

现状已具备的接缝直接复用：

```
workflow.emit() ──► ProgressEventBus
                      ├─ on_event（消费者注入）→ CLI rich / Web SSE / MCP progress
                      ├─ progress.json（落盘，向后兼容 Web ETA 视图）
                      └─ EventBus → .events/events.jsonl（统一落盘，不变）
```

* CLI 消费者：`on_event = rich 渲染器`（现状复用，行为不变）。

* Web 消费者：`on_event = asyncio.Queue.put`，SSE 端点从队列取事件推送；**不再解析 stdout、不再轮询 progress.json**（progress.json 仍写，作为 ETA 兜底）。

* MCP 消费者：`on_event = progress 通知`（MCP 规范 `notifications/progress`）。

***

## 7. 三个消费者的实现要点

### 7.1 CLI 消费者（改造 `cli/commands/*.py` 为薄适配器）

```python
# cli/commands/write.py（改造后：~30 行）
from agent.commands.write_command import WriteCommand
from agent.core.command.registry import get

cmd = get("write")

@command("write", ...)   # 保留 @command 注册与 CommandMeta（兼容现有 registry/门禁元数据）
def write(
    project_dir: Path = typer.Option(Path("."), "--dir", "-d", ...),
    chapters: int = typer.Option(1, "--chapters", ...),
    json_output: bool = typer.Option(False, "--json", ...),
    ...
):
    ctx = CommandContext(project_dir=project_dir, on_event=_rich_renderer)
    try:
        result = dispatcher.dispatch("write", ctx, chapters=chapters, ...)
    except CommandError as e:
        _render_error(e)          # rich 红字 + next_steps；json_output 时输出信封到 stdout
        raise typer.Exit(e.exit_code)
    _render_result(result)        # rich 摘要；json_output 时 emit_result(result.data, json_mode=True)
```

* **typer 函数签名保持原样**（测试直接 `write(project_dir=..., chapters=...)` 调用仍兼容）；

* `--json` 信封字段与现状逐字节一致（`test_cli_json.py` 不破）；

* rich 渲染逻辑从命令函数搬进 CLI 适配器（`_render_result`），命令核心不再依赖 rich。

### 7.2 Web 消费者（改造 `web/runner.py` 为进程内执行）

```python
# web/runner.py（改造后核心）
def _run_command_inline(run: RunRecord, project: str, command: str, params: dict):
    ctx = CommandContext(
        project_dir=Path(project),
        on_event=lambda e: asyncio.run_coroutine_threadsafe(q.put(e), loop),
    )
    try:
        result = asyncio.run_coroutine_threadsafe(
            dispatcher.dispatch_async(command, ctx, **params), loop).result()
        run.set_done(result.data, result.events, result.summary)
    except CommandError as e:
        run.set_failed(e.code, e.message, e.next_steps)
    except Exception as e:
        run.set_failed("internal_error", str(e))   # 其余命令仍走子进程回退
```

* 已迁移命令（8 个）：**进程内执行**，事件直推 SSE；

* 未迁移命令：**子进程回退**（现状路径，逐行保留），保证增量迁移不回归；

* 迁移完成后（Phase 3）删除子进程路径。

### 7.3 MCP 消费者（未来，展示"只需实现一个消费者"）

```python
# agent/mcp/tools.py（新增，~40 行核心）
def command_to_mcp_tool(cmd: Command) -> dict:
    return {
        "name": f"novel_{cmd.name}",
        "description": cmd.description,
        "inputSchema": {"type": "object",
                        "properties": {p.name: param_spec_to_json_schema(p) for p in cmd.params},
                        "required": [p.name for p in cmd.params if p.required]},
    }

async def call_command(name: str, arguments: dict, ctx: CommandContext) -> dict:
    return (await dispatcher.dispatch_async(name, ctx, **arguments)).data
```

**加 MCP = 写一个适配器文件 + 把命令注册进 MCP 服务，命令本身零改动。** 这就是"一个接口，多个消费者"的红利。

***

## 8. 数据共享设计

| 数据         | 位置                                  | 写入者                                | 读取者                    |
| ---------- | ----------------------------------- | ---------------------------------- | ---------------------- |
| 故事设定/大纲/章节 | 项目根 `world.md/outline.md/chapters/` | 命令核心（workflow）                     | CLI / Web / MCP 读同一批文件 |
| 状态机        | `.state/state.json`                 | `core.engine.state_machine`（不变）    | 三端                     |
| 事件日志       | `.events/events.jsonl`              | `core.event_sourcing.EventBus`（不变） | 三端                     |
| 进度         | `.state/progress.json`              | `ProgressEventBus`（不变）             | Web（ETA 兜底）            |
| 规划产物       | `.state/plan.json`                  | start 命令（新：强制写入 total\_chapters）   | `_book_total()` 权威源    |

**写操作一律走 Command**（CLI 与 Web 的写路径完全一致）；**读操作**走只读查询（数据源同一即可）。
Web 专属状态（`.state/stages.json`、`.state/qa/*.json`）在 Phase 2 收敛为命令（`review` / `confirm-stage` / `qa-save`），页面渲染保留在 Web，但读写都经过 Command，保证 CLI 也能用。

***

## 9. 迁移策略（分阶段，每阶段独立可交付）

| 阶段          | 内容                                                                                                              | 退出条件                                |
| ----------- | --------------------------------------------------------------------------------------------------------------- | ----------------------------------- |
| **Phase 0** | `core/command/` 契约层 + Dispatcher + `agent/commands/` 骨架 + 3 个简单命令试点（status / doctor / mode）                     | 试点命令 CLI 行为不变、Web 进程内可跑、测试全绿        |
| **Phase 1** | 8 个引导命令：start / discuss / architecture / confirm-architecture / outline / design-characters / write / autowrite | Web 向导全流程进程内执行，无子进程、无 rich 解析       |
| **Phase 2** | 全部 25 个 `--json` 命令 + Web 专属状态收敛为命令（stages/qa/review）                                                           | CLI/Web 全命令一致，`test_cli_json.py` 全绿 |
| **Phase 3** | 全量 67 命令 + 删除子进程回退 + Web 表单按 ParamSpec 全自动生成 + 更新 `项目文档/架构文档.md`                                                | 三端（含 MCP 演示）同接口同数据                  |

**回退保障**：每个阶段 Web runner 都保留「已迁移命令 → 进程内；未迁移 → 子进程」双路径，任何阶段可独立上线，不阻塞现有功能。

***

## 10. 兼容与回归保障

1. typer 命令函数签名**不变**（测试直接调用兼容）；
2. `--json` 信封字段**逐字节不变**（`test_cli_json.py` / `test_g4_cli.py` 等）；
3. 事件类型 / progress.json schema **不变**（`test_g9_progress.py` / `test_g9_stream.py`）；
4. 门禁行为不变（`--feedback` 特例经 `check_gate` 钩子保留）→ `test_enforce_gate.py` / `test_g2_stricter_gate.py`；
5. 全量 1112 测试分阶段回归。

***

## 11. 风险与权衡

| 风险                              | 缓解                                                     |
| ------------------------------- | ------------------------------------------------------ |
| 大范围回归                           | 分阶段 + 子进程回退 + 既有测试兜底                                   |
| ParamSpec 手动声明 vs 自动内省          | 手动声明（单源事实、Java 风格清晰）；CLI/Web/MCP 三端均从 ParamSpec 派生，不重复 |
| CLI help 文案微调                   | 低风险（无 help 文案测试依赖）                                     |
| 门禁特例分散                          | 统一收进 `Command.check_gate` 钩子，Dispatcher 执行             |
| 部分命令参数形态特殊（list/positional/密码类） | ParamSpec 支持 `list` / `positional` / `flag`；特殊命令逐一定制校验 |

***

## 12. 与"方案确认"相关的决策点（待你拍板）

1. **契约位置**：`core/command/`（契约）+ `agent/commands/`（实现）—— 是否认可？
2. **门禁收编**：允许 states/is\_global 从现有 `CommandMeta` 迁移到 `Command` 类（`check_gate` 钩子保留特例）—— 是否认可？
3. **迁移节奏**：Phase 0 → 1 → 2 → 3 分阶段 + 子进程回退 —— 是否认可？还是想一次到位？
4. **MCP 演示**：Phase 3 是否要顺带实现一个最小 MCP 消费者演示"一个接口多个消费者"？
