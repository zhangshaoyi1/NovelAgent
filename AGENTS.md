# NovelAgent AGENTS.md - Standing Orders

> 本文件遵循 DeepSeek Harness 设计：**Context Router**，而非百科全书。
> 每个新 Coding Agent Session 首先阅读本文件，然后按指引读取进一步信息。

***

## 导航规则

### 当前里程碑状态（2026-09-19）

> 规划层监理链已合龙到 **M6**。改这条链上任何一环前，先读 `../项目文档/优化/` 里对应的登记单。

| 里程碑 | 内容 | 落点 | 状态 |
|---|---|---|---|
| M1（含 Fix） | 章级**强度档位**供给 | `workflows/planning/`（档位独立小节 + 行首字段）、`plan_gate_pace_tier.jsonl` 采样闸 | ✅ `e02fbe0` / `f6d549a` |
| M3 | 7 条平权规则按档位分叉（**写手侧**） | `prompts/m5/pace_rules.md` + `writer_agent._writer_base(pace_relaxed)` | ✅ `2fe889c` |
| M4 | 质检档位参照系（**评委侧**） | `pace_tiers_of_window` 逐章供档，只进评委端 | ✅ `b78cb9f` / `80963a3` |
| M5 | 规划评委（采样模式，**恒不阻断**） | `workflows/planning/` 评委接线 | ✅ `85addcc` |
| M6 | 张力链路：孤儿 `tension_curve` → **已接线观测面** | `core/story/tension_curve.py` + `m5_persist._record_tension` | 🟡 B1–B3 已交付 `4f38276` / `d2e6a1a`；**B4 阈值重标定待拍板** |

⚠ **M6-B4 未拍板前，`no_climax` / `no_aftermath` / `flat` 三条判据都不许接线为闸门**（理由见下）。

### 文档体系（2026-09-12 复核）

`../项目文档/` **根目录只保留四份权威文档**，其余全部并入这四份后删除（历史版本见 git）：

| 文档 | 定位 | 什么时候读 |
|---|---|---|
| [产品需求文档.md](../项目文档/产品需求文档.md) | 需求（做什么、为什么） | 理解产品目标、功能范围、竞品对标 |
| [架构文档.md](../项目文档/架构文档.md) | 架构设计（怎么组织） | 改包结构、分层、依赖方向前**必读** |
| [详细设计文档.md](../项目文档/详细设计文档.md) | 详细设计（具体怎么实现） | 改具体模块/类/调用链时读 |
| [用户使用最佳实践.md](../项目文档/用户使用最佳实践.md) | 最佳实践（怎么用好） | 答疑用法、成本/质量调参、避坑 |

> `历史归档/`（历史基线快照）与 `设计图/`（UI 设计资产 HTML/PNG/CSS）**保留但非权威**，只在溯源/对图时查阅。
> `模块设计/`（**9 份**模块专题文档：RAG / llmagent编排内核 / 多Agent / 上下文管理 / 记忆 / 进程管理 / 打包 / 长线一致性与多团队自动写作设计）**保留但非权威**——它们是各模块落地前的设计稿，类名与数字可能已过期；**导航与时效状态见《详细设计文档.md》§12「模块设计专区导航」**，改模块前读详细设计对应小节即可。
> 已不存在的文档（文档更新日志 / 竞品分析 / 架构评审与待办 / 高可用评估架构重构方案 / writer-daemon设计与实施 / 进度状态一致性复盘 / 两天提交分析与框架根因反思 / 体检反馈闭环修复方案 / 竞品差距改进计划）——**不要去找**，内容已并入上述四份。

### 小说质量优化登记（2026-09-13 新增，强制）

**所有提升小说质量的优化（无论来自读者差评、体检反馈还是自查），必须先在 [../项目文档/优化/](../项目文档/优化/) 登记，再动代码。** 模板见 [../项目文档/优化/_模板.md](../项目文档/优化/_模板.md)，每项优化一个独立登记文件，命名 `YYYYMMDD_短标题.md`。登记必填：涉及小说、问题现象、根因（对应流水线环节）、提升点、优化原因、评审/差评来源信息、验收标准、关联代码位置与回滚方式。
> ⚠ **验收标准必须写成「某条因果链被切断」，不能写成「某个计数下降」**——复合指标会误导（计数降了但缺陷仍在，是常态）。反例清单见第 17 条纪律。
> 未登记就改代码 = 流程违规；登记后实施完成的，须回填"实施记录"并注明 commit sha。

> **2026-09-15 扩围（回溯 3 天 98 提交的缺口 G3）**：登记范围**不止"提升小说质量的优化"**。凡改动 `src/agent/` 的**结构性改动**（红线判据、门禁/处置动作、维度契约、派生状态底座、基础设施）**同样必须先登记**——09-12 那批（长线一致性一期 A–E、管理团队二期、实体名册/信息账本/战力标尺，25 提交/+9470 行）因制度尚未建立而全程无治理，是后续反复补调用点的直接原因（追认登记见 `../项目文档/优化/20260915_追认登记-0912结构性改动.md`）。
> 结构性改动的登记单**必填「同类点位全量清单（已覆盖 N / 共 M）」**：修一处缺陷时若同族存在多个入口，必须一次列全并标明覆盖情况，禁止"改一个调用点、等下次再冒出来"（G4）。

### 理解项目
读取 [../项目文档/产品需求文档.md](../项目文档/产品需求文档.md)

### 理解架构（修改前必读）

任何涉及 `src/agent/` 包结构的修改：
→ **先读** [../项目文档/架构文档.md](../项目文档/架构文档.md)
→ **再读** [../项目文档/详细设计文档.md](../项目文档/详细设计文档.md)
→ **重点关注** "架构不变性"章节，确认你不违反依赖方向

### 包结构总览

```
agent/
├── src/agent/                    # 主业务代码（小说创作系统）
│   ├── base/                     # 基础抽象层（不依赖任何上层）
│   ├── client/                   # 统一 LLM 客户端层（只依赖 base）
│   ├── core/                     # 核心引擎层（依赖 base + client）
│   │   ├── base/                 # 基础基础设施（异常/注册表/重试/结构化输出）
│   │   ├── engine/               # 核心引擎（状态机/Agent循环/命令路由/工作流编排）
│   │   ├── story/                # 故事领域模型（设定/伏笔/章节/高潮曲线）
│   │   ├── quality/              # 质量保障层（护栏/一致性/评分/改写）
│   │   ├── llm/                  # 预算计划（embedding 路由已在 client/，兼容层已拆除）
│   │   ├── llmops/               # LLMOps（追踪/成本/评测）
│   │   ├── registry/             # 扩展机制注册表（Skill/题材包）
│   │   ├── infra/                # 基础设施（上下文工程/仪表盘/诊断/Compose）
│   │   ├── event_sourcing/       # 事件溯源（事件总线/存储/恢复）
│   │   ├── rag/                  # 检索增强生成（索引/检索/向量存储）
│   │   ├── anti_ai/              # AI 味检测与压制
│   │   ├── continuity/           # 连续性账本（G15）
│   │   ├── supervisor/           # 长小说监督体系
│   │   ├── auto_orchestrator/    # 一键自动编排
│   │   ├── tools/                # Tool 实现层（内置工具/ MCP 桥接）
│   │   └── failure/              # 统一失败处理
│   ├── agents/                   # 四个核心智能体（Planner/Writer/Editor/Evaluator）
│   ├── workflows/                # 工作流编排（@workflow 装饰器动态注册）
│   │   ├── planning/             # M1-M4：写作规划阶段（世界观/讨论/架构/大纲/角色）
│   │   ├── writing/              # M5-M6：章节写作阶段（AgenticWrite/写章/调整）
│   │   │   └── m5_write_chapter.py 已按职责拆为 Mixin 模块（2026-09-05）：
│   │   │       m5_context.py（上下文装配）/ m5_quality_gate.py（质量闸）/
│   │   │       m5_persist.py（落盘归档）/ m5_text_hygiene.py（文本净化）；
│   │   │       主文件经多继承组合，类名与导入路径不变，勿再往主文件堆积新职责
│   │   ├── evaluation/           # M10-M21：评测审计阶段
│   │   ├── pipeline/             # 流水线编排（全流程自主/主线/预算）
│   │   ├── market/               # M22-M23：市场分析
│   │   ├── m8_mode.py            # 模式切换（单独保留在根目录）
│   │   └── __init__.py           # 保持向后兼容的导入
│   ├── tasks/                    # 新式 TaskSpec + Executor 模式
│   ├── cli/                      # CLI 命令（@command 自动发现）
│   ├── service/                  # Service 层（AgentService 进程内服务接口）
│   ├── session/                  # 会话管理（原生 llmagent SessionManager 再导出）
│   ├── memory/                   # 统一记忆层（MemoryLayer + 三层记忆）
│   ├── skills/                   # Skill 插件目录
│   ├── prompts/                  # Prompt 配置文件
│   ├── templates/                # Jinja2 模板目录
│   ├── methods/                  # 写作方法目录
│   ├── state_schema/             # 状态 Schema 定义
│   ├── daemon/                   # 后台写作守护进程（任务队列/进程管理/跟随）
│   └── web/                      # FastAPI Web UI（web→cli 仅命令注册副作用，禁止 cli import web）
├── src/llmagent/                 # 编排内核（Gateway/Task/Catalog/Session/EventBus）
│   ├── gateway/                  # 模型调用网关（唯一LLM出口）
│   ├── kernel/                   # 核心运行时（Task/Session/Agent/Planner/Memory）
│   └── tasks/                    # 业务 Task 定义
├── tests/                        # 业务测试（pytest）
├── llmagent_tests/               # 编排内核测试
├── scripts/                      # 辅助脚本
├── projects/                     # 项目数据目录
└── tools/                        # 工具目录
```

### 依赖方向（严格单向）

`base → client → core → agents → workflows → tasks`

- **`base/`** 不依赖任何上层（client/core/agents/workflows）

- **`client/`** 只依赖 `base/`，不依赖 `core/` 或任何上层

- **`core/`** 依赖 `base/` + `client/`

- **`agents/`** 依赖 `base/` + `client/` + `core/`

- **`workflows/`** 依赖所有下层

- **`tasks/`** 与 `workflows/` 并列，均为入口层，依赖所有下层

### 修改 `base/` 层：

→ `base/` **不依赖任何上层**（client/core/agents/workflows）
→ `base/` 只提供基础抽象（Agent 基类、配置、消息、类型、LLM 协议层）
→ 打破这条约束会导致循环导入，必须立即回滚

### 修改 `client/` 层：

→ `client/` **只依赖** **`base/`**，不依赖 `core/` 或任何上层
→ `client/` 提供统一 LLM 客户端入口（内部使用原生 llmagent Gateway）
→ `client/gateway_adapter.py` 是唯一 LLM 出口，提供 `create_gateway()` / `chat_creative()` / `chat_utility()` / `chat_structured()`
→ 所有 LLM 调用必须走 `gateway_adapter` 的辅助函数，**禁止直接调用 `LLMClient`（已废弃）**

### 修改 `core/` 层：

→ `core/` 依赖 `base/` + `client/`
→ `core/` 是领域核心引擎（状态机、护栏、一致性、结构化输出等）
→ `core/` 下子包职责单一，禁止循环依赖

### 修改 `agents/` 层：

→ `agents/` 依赖 `base/` + `client/` + `core/`
→ 四个核心智能体：`planner.py` / `writer_agent.py` / `editor.py` / `evaluator.py`
→ 旧 `*_agent.py` 已删除，请勿再引用

### 修改 `workflows/` 层：

→ `workflows/` 依赖所有下层
→ 编排主写作流程：`m1_config` → ... → `m23_short`
→ 通过 `@workflow` 装饰器自动注册到 `WorkflowRegistry`

### 修改 `tasks/` 层：

→ `tasks/` 与 `workflows/` 并列，均为入口层
→ 使用 `TaskSpec` + `Executor` 模式注册到 `TaskRegistry`
→ 每个任务文件定义一个 TaskSpec 和对应 Executor

### 新增命令：

→ 在 `src/agent/cli/commands/` 新建文件
→ 用 `@command(name=..., allowed_states=(...))` 装饰器自动注册
→ 无需手动修改 CLI 装配代码

### 非平凡修改（超过 3 个文件 / 影响架构）：

→ **必须** 创建或更新 **Agent Note**（决策记录，模板见 `.agents/notes/TEMPLATE.md`）
→ 路径 `.agents/notes/{lifecycle}/{class}/yyyy-mm-dd-topic-title.md`
→ `lifecycle`：`proposed/`（实施前评审）· `implemented/`（已交付）· `rejected/`（否决）
→ `class`：`architecture` / `feature` / `bug-fix` / `simplification` / `process` / `testing`
→ 头部两行严格为 `# Agent Note: <title>` + `Status: <status>`
→ 正文骨架：`## Problem` → `## Decision`（implemented）/ `## Proposal`（proposed）
→ → `## Alternatives considered` → `## Consequences`（implemented）
→ **架构变更（base/client/core 包结构、依赖方向、跨包契约）必须补充 Agent Note**

### 测试验证：

→ 修改后统一运行验证：`pytest`（全量口径 = 无参数，含 tests/ + llmagent_tests/；干净跑带 `CODEBUDDY_SAFE_DELETE_ENABLED=0`）
→ **基线数字一律以 `../.workbuddy/memory/MEMORY.md` 的基线行为准**（随每次提交刷新，本节不再硬编码计数）。最近一次：**2780 passed / 9 skipped / 0 failed @ `d2e6a1a`**，约 560–1190 秒（耗时随机器负载浮动 2 倍，**勿按耗时判健康**）
→ ⚠ **改判据强度 / 新增降级点 / 删旧路径 ⇒ 必须跑全量**；只改文案或新增独立测试文件可只跑受影响面 + 架构红线
→ 看 **FAILED 行**判定，不看退出码；`tests/architecture/` 红线全量 **191 项**（秒级）
→ ⚠ **全量若缺汇总行 = 取证不完整**：safe-delete shim 拦截 pytest 的 tmp 清理会致 `exit 1` 且不打印汇总 ⇒ 必须带 `CODEBUDDY_SAFE_DELETE_ENABLED=0` 重跑
→ ⚠ **后台起跑的 pytest，采集发生在「启动那一瞬间」**：启动后新增的测试不进本次统计（实测差 5 项，一度被当成回归）。判据：`collected == passed + skipped + failed`，不等就用 `pytest --collect-only -q` 复测
→ 提交关卡：`.git/hooks/pre-commit` 自动跑 architecture 红线（见第 36 条）；失败先归因再动手（`scripts/baseline_diff.py`，见第 39 条）

### 提交前检查：

→ 所有测试通过（pre-commit 关卡自动兜底 architecture 红线）
→ 不破坏架构不变性
→ 非平凡修改有决策记录
→ 结构性改动 / 质量优化**已先在 `../项目文档/优化/` 登记**（含同类点位全量清单）
→ 不提交 `.env.accel`、`outputs/`、`_debug_*.py`、根目录 `_probe_*.py` / `_tmp_*.py`
→ `.agents/notes/` **随代码一起提交**（决策记录纳入版本管理，2026-09-12 起）

#### ★ 自检三问（2026-09-19 新增，提交前逐条回答）

> 这三问对应 09-18/09-19 反复出现的返工类型。答不上来就别提交。

1. **改动了哪一层？** —— 是**供给面**（影响写作输入）还是**观测面**（只统计/对账）？
   若是观测面，确认它**不会**反向影响写作（判据见纪律 19）。
2. **同类点位列全了吗？** —— 写「已覆盖 N / 共 M」。**有哪个调用点没跟着改？**
   凡跨模块共享常量/词表，有没有红线做机器交叉核对（纪律 22）？
3. **判据强度与证据匹配吗？** —— 阈值的历史达成率是多少（纪律 17）？
   结论的强度有没有超过取证等级（纪律 24）？**真实项目验证过，还是只有构造单测？**

#### ★ agent 侧改动之后的「文档同步」动作（本回合新增，补的是流程缺环）

> 现状缺口：**规划产出的四份权威文档（PRD/架构/详细设计/最佳实践）没有「何时更新」的触发规则** ——
> 结果是代码改了、文档不动，`模块设计/` 里的类名与数字越来越旧。以下为动作清单。

→ **改了 `src/agent/` 的结构 / 契约 / 基线** ⇒ 同步 `../项目文档/详细设计文档.md` 对应小节（它自称"详细设计的唯一权威"，冲突时以它为准）
→ **改了包结构 / 依赖方向 / 分层** ⇒ 同步 `../项目文档/架构文档.md`（并补 Agent Note）
→ **改了用户可见的用法 / 成本与质量调参** ⇒ 同步 `../项目文档/用户使用最佳实践.md`
→ **改了产品范围 / 功能边界** ⇒ 同步 `../项目文档/产品需求文档.md`
→ 每次同步**都要刷新文末的「文档同步基线 = agent@<sha>」**，不加基线的同步视为未完成
→ ⚠ `../项目文档/模块设计/`（9 份）是**落地前设计稿，非权威**，**不要**当作需要同步的目标；它们的过期是预期内的

***

## 全局不变性（必须遵守）

1. **单向依赖原则**：依赖方向永远是 `base → client → core → agents → workflows`，绝不反向
2. **降级不阻断**：任何 LLM 不可用 / API 失败都应该优雅降级，不阻断写作流程
3. **删除即收口（2026-09-12 起）**：零调用方兼容层直接删除、不留废弃警告再导出（core/llm 兼容层与 core 悬空导出已拆除）；新增降级点必须接 `degrade()`（豁免棘轮只减不增），新增 state.json 直写会被状态所有权红线拦截
4. **Agentic 设计**：任何失败都返回错误信息给用户，而非静默崩溃
5. **LLM 统一出口**：所有 LLM 调用必须通过 `agent.client.gateway_adapter` 的辅助函数（`chat_creative()` / `chat_utility()` / `chat_structured()`），**禁止直接使用旧 `LLMClient`**
6. **软维度单次 FAIL 不触发回滚**（探针实证单次证据不可靠）——已翻转语义勿改回，旧测试报 `rolled_back` 属预期
7. **E3 `_pre_validation` 是有意取舍**，已登记能力对账豁免，勿擅自接线；勿凭旧清单删码

### 架构红线测试（tests/architecture/，改架构前必看）

| 红线 | 含义 |
|---|---|
| `test_state_ownership.py` | 状态所有权：**路径→业主**成员契约（`STATE_OWNERSHIP`）；旁路须在 `STATE_OWNERSHIP_TOLERATED` 具名登记（reason+target）；引用次数降为看板 |
| `test_degrade_visibility.py` | F-1 降级可见化：静默点清零；豁免须写 `reason=<枚举>[ ref=<登记单>]`，未引用 reason 者棘轮只减不增 |
| `test_degrade_contract.py` | 降级命名空间契约：`degrade(where,...)` 的 where 必须在 `core/infra/degrade_registry.py` 登记；双向差集（未登记/僵尸条目均 FAIL） |
| `test_single_source_of_truth.py` | 单一真源：根目录不得有权威副本（M1）；根目录脚本须在 `ROOT_SCRIPT_REGISTRY` 在册（M1b，当前 **7** 项，与根目录 7 个脚本一一对应）；兄弟 worktree 须在 `SIBLING_WORKTREES` 在册且**到期即 FAIL**（M3）；仓内独立嵌套仓须在 `NESTED_REPO_ALLOWLIST` 在册（M4）；仓内 `AGENTS/README/TEMPLATE` 必须被 git 跟踪 |
| `test_m6_tension.py` | **张力观测面红线（21 项）**：量纲与章长解耦｜`measure == TensionCurveManager._compute_tension`（同源）｜失败显性且不阻断｜`TestObservationNotSupply` 钉死观测面不得产 `pace_tier*`｜真实项目不被污染 |
| `test_capability_parity.py` | 能力对账：接口对账 + 副作用 hook 对账（差集真机制，勿改白名单） |
| `test_hostile_delete_env.py` | 宿主敌意：safe-delete 护栏免疫，删除路径不被 SystemExit 逃逸 |
| R4 方向矩阵 | cli↔web 循环依赖已拆除：web→cli 仅命令注册副作用，禁止反向 |

> **契约 vs 配额（2026-09-15，G2 收口）**：上述两条计数型闸门（豁免总数 / 每文件引用次数）
> 已**降为看板**（超限只告警不 FAIL）。闸门改为**成员资格**：降级点须有登记命名空间，
> 状态写入者须是业主或被具名容忍。新增监管能力时优先问"这是成员资格校验，还是配额计数"——
> 配额拦住的是数量，拦不住资格（把 N 处集中到一个无权模块，计数不变而所有权已崩塌）。

***

## 判据设计纪律（2026-09-19 新增，均源自真实事故）

> 这一节的每一条都对应一次**已发生**的返工。改判据 / 阈值 / 门禁前逐条过一遍。

17. **★ 阈值类判据上线前必须做「历史达成率」体检（阈值成了摧毁扳机）**：任何"超过 X 就阻断/回退"的判据，先拿**真实语料**统计它历史上达成过几次。
    - 实证（M6-B2）：`no_climax`（最高张力 < 7.0）与 `no_aftermath`（前章 ≥ 8.0）在 **1266 章真实语料上达成 0 次**（实测 max=4.40）。若直接接线，它永远触发不到 → 要么是死代码，要么一旦被调到就会**成片摧毁合法章节**。**达成率 < 50% 的阈值不许直接当闸门。**
    - 配套：`flat`（极差 < 1.0）真实 IQR≈0.70 ⇒ 极易误报，必须先重标定再用。
18. **★ 「改量纲」不等于「让阈值可达」——先分清根因是量纲还是度量本身**：M6-B3 把三分量统一按句归一（修掉了"冲突词按每百字、悬念词按绝对计数"的方向矛盾），**章长解耦已验证**（15/60/150/600 句恒为 7.0），但真实 `max` 仅从 4.40 → 5.00，**≥7.0 仍是 0/1266**。⇒ **根因是「度量与真实文体不匹配」，不是量纲。** 教训：修完一个"看起来是根因"的问题后，**必须重跑基线数字确认结论**，不可直接宣布可达。
19. **★ 观测面不得反向成为供给面**：新增"度量/统计/对账"类模块时，先定性它是**事实**还是**意图**。事实层的产物只能用于**对账 / 告警**，不得回灌为写作约束（否则两条独立线互相污染，再无法交叉验证）。M6 用红线 `TestObservationNotSupply` 结构性钉死（该模块不得产 `pace_tier*`、不得导出含 `tier` 的 API）。
20. **★ 给"章级"消费者喂"阶段级/整段"文本 = 没喂**：供给粒度 ≠ 消费粒度时，消费者只能**编造** ⇒ 章章同质 ⇒ 回退重写输入不变 ⇒ **死循环**。问三句：① 生产者被要求给的粒度是**章**还是**阶段/整本**？② 消费者按**本章**切分了吗？③ 截断方向与"本章"一致吗？
21. **★ 判据不能建立在「调用方猜对语义」之上**：凡是"回退 / 兜底"设计，先问**猜错是不是比不猜更贵**。若是，必须换成**结构判据**（数据自证），而不是"再补一个参数"。实证：三端只有一处传 `pressure_stage`，**猜错阶段词 ⇒ 命中 0 行 ⇒ 仍回退整段**，比不传更糟 —— "仅补传参数"是**危险修复**。
22. **★ 跨模块共享的常量/词表必须有一条红线做机器交叉核对**：两边各写一份、无人核对 ⇒ **一次改名即双向破裂**（09-18 一天内发生两次）。红线形态应是「成员/派生关系」或「闭环」，**不是「数值相等」**。
23. **★ 编排层谱面必须核对覆盖面与全书章数对齐**：缺口 ⇒ 消费者**沿用上值**（无报错、无日志）= **静默失真**。实证：谱面只覆盖 S01–S03 各 6 章 + S04 ⇒ **ch19–109 共 95 章无谱、同阶段**。⚠ **检测 ≠ 修复。**

### 取证纪律（同批，配合作业用）

24. **数字对 ≠ 来源对；构造单测通过 ≠ 真实项目验证**：
    - 「我记得的实测值」必须重新取证到 `路径 + 命令`（实证：证据按记忆记成 A 项目，真源实为 B 项目 —— 字数吻合但项目名全错）。
    - **结论强度必须与取证等级匹配**：真实数据不含某字段时，"X 成立"只有**构造样本**证据，**不得写成"真实项目已验证"**。
    - **基线数字必须绑定 commit**（否则无法回溯判定）。
25. **改提示词 / 规则文案前，先取证「这一段是否真被渲染」——改死文件 = 没改**。三问：① 该段是 `system` 还是 `user`？（`render_user` **只返回 user 段**）② 有几个渲染调用点？③ 谁真把它送进 `messages`？实证（M3）：写作要求 3-6 条有两份"真源"，而 `m5.generate` 全仓**只有 `render_user` 一个调用点** ⇒ `generate.md` 的 `# system` 段在 autowrite 路径**从未渲染**；渲染取证全绿却零效果。
26. **凡主链路自证的现象，都要找独立 side-channel 对账**（回退账本、快照序号）。
27. **「注入了」必须做行为级断言**（断言内容真出现）；只断言"函数被调用 / 字段存在"会漏掉**静默截断**。
28. **验收标准写成「某条因果链被切断」，不写「某个计数下降」**（复合指标会误导；计数降了但缺陷仍在是常态）。
29. **「连续/累计」型护栏必须证明状态比它守护的周期活得久，且必须有「断链归零」规则**（实证：`consecutive=9` 永不解锁 ⇒ 每轮被陈旧 `tripped()` 截断）。
30. **不可逆动作（回滚销毁内容）必须由显式规则授权，兜底只能上报人工**；**动作强度 ≤ 判据可达性**。凡"不可逆动作 + 阈值型判据"，先问：① 该判据历史达成率多少 ② 它的输出真改变过控制流吗 ③ "由 XX 兜底"被验证过吗。
31. **凡「为兼容历史开的口子」，必须证明它只挡历史、不影响当前**（否则口子 = 缺陷新入口）。
32. **凡写「供 XX 补检 / 供 XX 消费」的注释，必须同时证明该消费者存在**（否则注释是缺陷的伪装）。
33. **凡「设计产出」必须核对「写手 / 评委 / 落盘」三端是否可达** —— 做矩阵，空格即缺陷。（M6-A 就是这样发现 `tension_curve` 四端全不可达的。）
34. **凡「整方法/模块豁免」，必须逐项核对其内部子能力是否落地**；**删旧路径前，所有指向它的红线/断言必须先取证「能力的新家」** —— 取证不出 = 能力真丢了，**不许删**。
35. **「先副作用、后记账/判定」的护栏，记账与判定必须前移到副作用之前**（否则只能追认损失；**轮询粒度 = 失效窗口**）。

***

## 工作环境硬约束与流程约定（2026-09-12 沉淀，踩坑实证）

### 环境硬约束

1. **safe-delete 护栏**：宿主 shim 会 hook stdlib 删除，单 turn 累计 ≥50 次抛 `SystemExit(1)`（`except OSError/Exception` 接不住），写章必越线 → 长驻进程/长测一律带 `CODEBUDDY_SAFE_DELETE_ENABLED=0`；删文件用 `rm`，**禁用 `git rm`**。
2. **沙箱会回滚工作区**：已改文件可能被回滚到 HEAD（最阴险是部分回滚：调用进了提交、import 行被吞，测试全绿上线 NameError）→ **改完立刻 commit + `git show <sha> -- <file>` 核验，禁止攒批**；任何异常的测试/git 数字先重跑一次再采信。
3. **并行 Edit 禁令**：并行 Edit 同一文件会互相覆盖（已三次事故），必须串行；并行会话下 Edit 失败 = 被并发改动，轮询等待静默再动手。
4. **"改了不生效"系列**：改 `.py` 的生效范围见下面第 5 条；此外——既有 daemon 存活时"重启 Web ≠ 换代码"（任务由旧 daemon 的代码执行，回合主仓后下一个任务才生效）；daemon 解释器是 `D:\env\python\python.exe`（系统 python，非 venv）；`Web/daemon` spawn 的长驻进程在沙箱内必被回收，**daemon 只能由 Web 或用户终端拉起**。
5. **改 .py / 配置的生效范围（2026-09-19 复核）**：
   - **daemon：改 `src/agent/**/*.py` 不需要重启** —— 写章子进程每任务重新 import，下一个任务即生效。
   - **Web：改 .py 必须重启**（常驻进程，import 只发生一次）。
   - **`models.json`（档位/max_tokens/模型定义）与 `NOVEL_MODEL_PROFILE`：Web 与 daemon 都要重启** —— 它们是进程启动时读取的配置，运行中改文件无效。上游 `glm-5.2` p90≈92s 且关 thinking 被忽略 ⇒ **timeout < 180s 必然超时**。
   - 浏览器 Ctrl+F5 ≠ 服务端重载。
6. **pytest 口径**：全量 = `pytest` 无参数（`testpaths = tests + llmagent_tests`；架构红线 **191 项**）；判定看 FAILED 行不看退出码；干净跑须 `CODEBUDDY_SAFE_DELETE_ENABLED=0`；**基线数字以 `../.workbuddy/memory/MEMORY.md` 基线行为准**（随提交刷新，勿在本文件硬编码）。
   - ⚠ **后台起跑的 pytest，采集发生在「启动那一瞬间」**：启动后新增的测试不会进本次统计（实测差 5 项，一度被当成回归）。判断口径：`collected` 数与 `passed + skipped + failed` 必须相等；不等就先查是不是采集时间早于最近的测试改动（`pytest --collect-only -q` 复测）。
   - ⚠ **全量跑期间不要编辑 `src/`**：`inspect.getsource` 读取源码时若文件正好被写，会产出**假失败**。

### 流程约定

7. **agent-repair / docs-repair 都是 linked worktree，且都停在旧分支 `release/20260906`**（2026-09-15 普查：落后 master **163 提交**；agent-repair **791MB / 328 dirty**）：
   - **只可读用于对照，禁止 cp 回主仓** —— cp 会把 repair 侧旧版本带回主仓（曾退回已删代码 → 假绿，测试全绿上线 NameError）。
   - 若确需 cp 同步（历史流程）：只做文件编辑、**禁跑 git**；同步用 cp 四件套 + models.json，**cp 后必须 `git status` 逐条甄别**，非本次改动 `git checkout HEAD --`。
   - 两条都已在 `test_single_source_of_truth.py` 的 `SIBLING_WORKTREES` 登记为 `deprecated-stale` 并带 `remove_by`；**到期红线即 FAIL**。移除用 `git worktree remove <path>`（分支与提交仍在对象库，可随时重建）。
8. **停任务 SOP**：daemon 活着时首选 `tq.request_stop()`（约 8s 生效）；"终止一直终止中" = daemon 死了（Web 只写 stop_requested）；`exit_code=2` = escalated 质量熔断非崩溃；清锁一律 `agent unlock`，**勿手删 writer.lock**。
9. **git**：双仓提交只推 gitcode，**无 GitHub 远程勿再补**（已拍板删除）；提交一律加 `-c commit.gpgsign=false`。
   - ⚠ **多行提交信息一律用 `-F <文件>`，不要用 `-m "..."`**（2026-09-15 两次实证）：`-m` 里的反引号会被当命令替换、`$` 会被展开、`*` 会被 glob 展开，**信息被静默改写且不报错**。踩过的例子：`` `except (SyntaxError, ...)` `` 整句消失、`scripts/*` 被展开成 `scripts/AGENTS.md: line 5: ...`。
   - ⚠ **漏了 `-c commit.gpgsign=false` 会「卡住」而不是「报错」**（本机 `commit.gpgsign=true`，2026-09-15 两次实证）：GPG 私钥缺失时 git 停在签名等待，表现为命令长时间无输出、bash 侧收到 SIGTERM，**看起来像 pre-commit 钩子挂住了**。排查顺序：① 是否漏带 flag；② 再怀疑钩子。`git commit --amend` 同样需要。
10. **设计红线**：失败必须显性化，绝不能被解读为通过；软维度单次 FAIL 不触发回滚（已翻转语义勿改回，旧测试报 rolled_back 属预期）；E3 `_pre_validation` 是有意取舍已登记豁免，勿擅自接线；新增降级点必须接 `degrade()`、新增 state.json 直写会被红线拦（豁免棘轮只减不增）。

***

## 监管与验证纪律（2026-09-15 新增，回溯 3 天 98 提交的六个缺口）

> 来源报告：`../.workbuddy/reports/2026-09-15-监管体系回溯-为什么修一个冒一个.md`
> 一句话：**「修一个冒一个」不是能力问题，是流水线缺三个环节（关卡 / 判据契约 / 同类盘点）。**

36. **提交前必须过架构红线（缺口 G1）**：`.git/hooks/pre-commit` 跑 `tests/architecture`（秒级，16 文件），`.git/hooks/pre-push` 跑全量。装一次即可：`bash scripts/githooks/install.sh`。
    - ⚠ `scripts/` 默认被 `.gitignore` 忽略（一次性脚本不入库），**唯独 `scripts/githooks/` 用负向规则放行** —— 钩子必须受版本控制，否则换机器即失效。新增钩子请放该目录。
    - **为何强制**：此前 CI 只在 `tags: v*` / 手动触发（无 push/PR），远程又只有 gitcode → 09-12~09-15 共 **98 次提交 CI 执行 0 次**；人只跑「受影响面」340/2315 = **15%**，**85% 测试面是提交盲区**，跨模块连带破坏（改 A 撞 B）只能等实弹。91 个碰 `src` 的提交里 **38 个（41%）零测试**。
    - ⚠ **统计日提交数必须用 `--since="YYYY-MM-DD 00:00"`**：裸日期 `--since="YYYY-MM-DD"` 会**漏掉当天前段**（实测 09-12 只算到 22 个，真实 49 个）。
    - 紧急绕过用 `git commit --no-verify`，但**必须在当日日志里记明原因**（绕过是显性行为，不许静默）。
37. **⚠ 两条红线的判据是启发式，写文案会撞（缺口 G2）**：`test_state_ownership`、`test_degrade_visibility` 曾用纯字符串出现次数做判据 —— **注释/日志文案里出现 `state.json` / `SILENT_DEGRADE` 字面量也会计数**。已实证：`092e55f` 补的 `degrade()` 文案含 "state.json"，**22 分钟后** `4bd2ec7` 只能改文案（语义完全没变）。
    - 2026-09-15 已升级为**语义判据**（`state_ownership` 走 AST 只数路径字面量；`degrade_visibility` 走 `tokenize` 只数 COMMENT）→ 文案不再计分。
    - 纪律：改这两条红线或新增降级点时，**先确认判据是"路径/注释"而非"任意字符串"**，不要把"为了让扫描过关"当成修复（那是 churn 的主引擎）。
    - **进而「配额 → 契约」（同日收口）**：判据语义化只解决"数得准"，没解决"管什么"。两条红线的**计数闸门已降为看板**，闸门改为**成员资格**：
      · 降级点 → 必须在 `core/infra/degrade_registry.py` 登记（未登记 / 僵尸条目均 FAIL）；
      · 豁免 → 必须写 `reason=<枚举>`（模糊理由另需 `ref=<登记单>`；悬空 ref FAIL）；
      · 状态写入 → 必须是业主，或在 `STATE_OWNERSHIP_TOLERATED` **具名**登记（reason+target）。
      **纪律**：新增监管能力先问「这是成员资格校验，还是配额计数」——配额拦住的是数量，拦不住资格（把 N 处写入集中到一个无权模块，计数不变而所有权已崩塌）；能用成员资格表达的，不要退化成配额。
38. **同类缺陷族必须一次列全（缺口 G4）**：修一处缺陷前，先枚举**全部同类入口**并在登记单写明「已覆盖 N / 共 M」。
    - **反面教材**：金三门禁链因每次只补下一个调用点（写时→批末→`/appeal`→rewrite→框架化），被跟进 **7 次**；degrade 可见化链按 except 点逐个清，**6 次**。
    - **正面范式（应制度化）**：`25a3293` 修完动作强度 → `479fcfc` 立刻把口径写成 M1–M6 矩阵红线 → **16 分钟后** `ddb4f14` 就被新红线抓出 `padding_repetition_abnormal` 作用域错配。**「修完立刻把口径棘轮化」是有效动作，不是可选项。**
39. **失败必须先归因再动手（缺口 G5）**：看到任何测试/指标失败，**先判定归属**（是不是我引入的），再改代码。
    - 首选工具 `python scripts/baseline_diff.py --base <基线sha>`（P2，一条命令给失败差集）；退化方案 `git archive <父sha> | tar -x -C $(mktemp -d)` 在快照上跑对照。
    - **反例代价**：`4e3c8ce`~`41dde29` 期间的 2 条存量失败因基线未刷新，导致后续每次全量都需重新甄别；基线三天漂移 5 版。
    - **基线刷新是改动的一部分**：改了代码就顺手把 `../.workbuddy/memory/MEMORY.md` 的基线行改掉，**禁止攒批**（基线不清 → 下一次判定就得重来）。
40. **监管动作必须包含"否决型"（缺口 G6）**：新增监管能力时，问一句「这是**发现**（detect）还是**否决**（prevent）」。
    - 现状全是 detect（红线测试 / `doctor` / `runtime_selfcheck` / 巡检）—— 红线起的是"事后罚款"，不是"事前设计约束"。刺眼证据：`1d52f7f` 的提交标题自带「**+红线豁免修补**」，即特性上线同一提交就给自己开豁免。
    - 纪律：**特性上线前先自问"这会撞哪条红线"**，而不是撞了再补豁免；能用 schema/关卡表达的约束，不要退化成"人记得检查"。
41. **★ 知识 ≠ 机制**：判据避坑、基线纪律这类经验，**写进 `../.workbuddy/memory/MEMORY.md` 不等于拦住复发**（G2 那个坑 09-13 就写进记忆第 79 行，`092e55f` 照样踩了）。
    - 能落成**红线断言 / 关卡 / schema 校验**的，一律落成机制；记忆只用于"暂时无法机制化"的取舍。
42. **单一真源：权威资产只能有一份，且必须在受版本控制的仓内（G6 在「工作区布局」维度的复现）**
    - ⚠ **工作区根目录 `D:\project\NovelAgent\` 不是任何 git 仓** —— `agent/`（代码仓）与 `项目文档/`（文档仓）是它的两个子仓。所以根目录里任何"权威文件"都是**第二副本：没有同步机制，只会漂移后无声误导**。已实证：根目录曾有一份 `AGENTS.md` 与仓内版本分叉（标题 78 / 正文 98 自相矛盾，新 Session 读到的是过期规则）。
    - 红线 `tests/architecture/test_single_source_of_truth.py`，三类形态（判据都是**成员资格**，不是计数）：
      · **M1 仓外副本**：根目录不得出现 `AGENTS.md` / `CLAUDE.md` / `README.md` / `pyproject.toml` / `models.json` / `.agents` / `src` / `tests` 等同名条目。**真源**：规则→`agent/AGENTS.md`；决策记录→`agent/.agents/notes/`。
      · **M1b 根目录脚本**：根目录 `*.py/*.bat/*.cmd/*.ps1` 必须在 `ROOT_SCRIPT_REGISTRY` 在册（写明 `purpose` + `blocker`）。**不可随意迁移**：它们被 Windows 计划任务 `NovelAgent_HourlyMonitor` 以**绝对路径**引用，且本沙箱 `schtasks` 被程序黑名单拦截（改了任务指向无法回滚）。
      · **M3 兄弟旧树**：根目录旁 `.git` 为**文件**的目录 = linked worktree，必须在 `SIBLING_WORKTREES` 在册；`status=deprecated-stale` 的必须带 `remove_by`，**到期即 FAIL**（把"临时容忍"变成"到期硬约束"）。
      · **M4 嵌套仓库**：仓**内**出现独立 git 仓（子目录里有自己的 `.git`）→ 该子树对父仓**完全不可见**（`git status` 只报一个 `?? path/`）。必须在 `NESTED_REPO_ALLOWLIST` 在册（reason + `remove_by`，到期即 FAIL）。已实测：`项目文档/skill/skills` 是 `anthropics/skills` 的克隆，**449 个文件全对父仓不可见**。
    - ⚠ **`agent-repair/` 与 `docs-repair/` 是旧分支 `release/20260906` 的工作树**（普查时落后 master **163 个提交**；agent-repair **791 MB / 328 dirty**）。内含 4 份**严重过期**的同名权威文档与已被声明删除的旧文档。**只可读用于对照，禁止 cp 回主仓**（在册 3 次污染事故，代价是「测试全绿上线 NameError」）。
    - ⚠ **`scripts/*.md` 必须能被版本控制看见**（`.gitignore` 已加负向规则 `!scripts/*.md`）：`scripts/AGENTS.md` 曾被忽略规则**永久吞掉**，从未入库 = 改了没人知道。
    - 巡检入口：`python scripts/ssot_audit.py [--deep]`（登记表**从红线读取**，单一来源不复制）；退出码 1 = 存在无主资产 / 未登记项 / 失明文档。

***

## 快速索引

| 文件/目录                                     | 作用                                      |
| ----------------------------------------- | --------------------------------------- |
| `src/agent/base/`                         | 基础抽象层（Agent 基类/消息/类型/LLM协议）             |
| `src/agent/client/`                       | 统一 LLM 客户端（Gateway 原生，gateway_adapter 为唯一出口）                    |
| `src/agent/agents/`                       | Planner/Writer/Editor/Evaluator 四个核心智能体 |
| `src/agent/workflows/pipeline/agentic_pipeline.py` | 自主写作主编排（2026-09-05 起位于 pipeline/ 子包） |
| `src/agent/workflows/writing/agentic_write.py`    | 唯一写章入口（2026-09-05 起位于 writing/ 子包）        |
| `src/agent/workflows/pipeline/mainline.py`         | 主流程编排（2026-09-05 起位于 pipeline/ 子包）        |
| `src/agent/core/engine/`                  | 核心引擎（状态机/Agent循环/工作流编排）                 |
| `src/agent/core/story/`                   | 故事领域模型（设定/伏笔/章节/高潮曲线；`tension_curve.py` = **已接线观测面**，见纪律 19）                   |
| `src/agent/core/quality/`                 | 质量保障体系（护栏/一致性/评分/改写）                    |
| `src/agent/core/infra/degrade_registry.py` | 降级命名空间**契约表**（新增 / 改名降级点必须同步登记；`m5.record_tension` 已登记） |
| `src/agent/core/llmops/`                  | LLMOps（追踪/成本/评测）                        |
| `src/agent/core/continuity/`              | 连续性账本（G15）                              |
| `src/agent/core/anti_ai/`                 | AI 味检测与压制                               |
| `src/agent/core/event_sourcing/`          | 事件溯源（事件总线/存储/恢复）                        |
| `src/agent/core/supervisor/`              | 长小说监督体系                                 |
| `src/agent/core/auto_orchestrator/`       | 一键自动编排                                  |
| `src/agent/core/failure/`                 | 统一失败处理（原生 llmagent FailureHandler）      |
| `src/agent/core/tools/`                   | Tool 实现层（内置工具/ MCP 桥接）                  |
| `src/agent/tasks/`                        | 新式 TaskSpec + Executor 注册               |
| `src/agent/cli/`                          | CLI 命令系统（@command 自动发现）                 |
| `src/agent/service/`                      | Service 层（AgentService）                 |
| `src/agent/session/`                      | 会话管理（原生 llmagent SessionManager）        |
| `src/agent/memory/`                       | 统一记忆层（MemoryLayer）                      |
| `src/agent/web/`                          | FastAPI Web UI                          |
| `src/llmagent/`                           | 编排内核（Gateway/Task/Catalog/EventBus）     |
| `src/llmagent/gateway/`                   | 模型调用网关（唯一LLM出口）                         |
| `src/llmagent/kernel/`                    | 核心运行时（Task/Session/Agent/Planner）       |
| `tests/`                                  | 业务测试（1200+ 用例）                          |
| `llmagent_tests/`                         | 编排内核测试                                  |
| `../项目文档/架构文档.md`                         | 完整架构文档（含模块职责）                           |
| `../项目文档/详细设计文档.md`                       | 组件级详细设计（智能体/工作流/Skill/引擎核心类）            |
| `../项目文档/优化/`                              | 质量优化 + 结构性改动**登记区**（改前必登记，含同类点位清单）      |
| `.agents/notes/`                          | 决策记录（**权威路径在仓内**；工作区根目录曾另有一份 = 已清理）      |
| `scripts/githooks/`                       | 提交关卡钩子（pre-commit 跑红线 / pre-push 跑全量，`install.sh` 安装） |
| `scripts/baseline_diff.py`                | 失败归因工具：HEAD vs 基线失败差集（先归因再改码）          |
| `scripts/ssot_audit.py`                   | 单一真源巡检：根目录资产归属 / worktree 分歧度 / 失明文档（登记表从红线读取） |
| `../agent-repair/`、`../docs-repair/`       | ⚠ **旧分支工作树（只读对照，禁止 cp 回主仓）**，登记表在 `test_single_source_of_truth.py` |
| `src/agent/core/infra/degrade_registry.py` | 降级命名空间**契约表**（新增 / 改名降级点必须同步登记）        |
| `../.workbuddy/memory/MEMORY.md`          | 长期记忆（测试基线、已收口缺陷、通用约定）                  |
| `../.workbuddy/reports/`                  | 复盘/回溯报告（含 2026-09-15 监管体系回溯）               |
| `.agents/skills/debugging.md`             | 调试方法论                                   |
| `.agents/skills/refactoring.md`           | 重构检查清单                                  |
| `.agents/skills/code-review.md`           | 代码审查清单                                  |

<br />
