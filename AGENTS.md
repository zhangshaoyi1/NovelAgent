# NovelAgent AGENTS.md - Standing Orders

> 本文件遵循 DeepSeek Harness 设计：**Context Router**，而非百科全书。
> 每个新 Coding Agent Session 首先阅读本文件，然后按指引读取进一步信息。

***

## 导航规则

### 文档体系（2026-09-12 复核）

`../项目文档/` **根目录只保留四份权威文档**，其余全部并入这四份后删除（历史版本见 git）：

| 文档 | 定位 | 什么时候读 |
|---|---|---|
| [产品需求文档.md](../项目文档/产品需求文档.md) | 需求（做什么、为什么） | 理解产品目标、功能范围、竞品对标 |
| [架构文档.md](../项目文档/架构文档.md) | 架构设计（怎么组织） | 改包结构、分层、依赖方向前**必读** |
| [详细设计文档.md](../项目文档/详细设计文档.md) | 详细设计（具体怎么实现） | 改具体模块/类/调用链时读 |
| [用户使用最佳实践.md](../项目文档/用户使用最佳实践.md) | 最佳实践（怎么用好） | 答疑用法、成本/质量调参、避坑 |

> `历史归档/`（历史基线快照）与 `设计图/`（UI 设计资产 HTML/PNG/CSS）**保留但非权威**，只在溯源/对图时查阅。
> `模块设计/`（8 份模块专题文档：RAG / llmagent编排内核 / 多Agent / 上下文管理 / 记忆 / 进程管理 / 打包 / 长线一致性与多团队自动写作设计）**保留但非权威**——它们是各模块落地前的设计稿，类名与数字可能已过期；**导航与时效状态见《详细设计文档.md》§12「模块设计专区导航」**，改模块前读详细设计对应小节即可。
> 已不存在的文档（文档更新日志 / 竞品分析 / 架构评审与待办 / 高可用评估架构重构方案 / writer-daemon设计与实施 / 进度状态一致性复盘 / 两天提交分析与框架根因反思 / 体检反馈闭环修复方案 / 竞品差距改进计划）——**不要去找**，内容已并入上述四份。

### 小说质量优化登记（2026-09-13 新增，强制）

**所有提升小说质量的优化（无论来自读者差评、体检反馈还是自查），必须先在 [../项目文档/优化/](../项目文档/优化/) 登记，再动代码。** 模板见 [../项目文档/优化/_模板.md](../项目文档/优化/_模板.md)，每项优化一个独立登记文件，命名 `YYYYMMDD_短标题.md`。登记必填：涉及小说、问题现象、根因（对应流水线环节）、提升点、优化原因、评审/差评来源信息、验收标准、关联代码位置与回滚方式。未登记就改代码 = 流程违规；登记后实施完成的，须回填"实施记录"并注明 commit sha。

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

→ 修改后统一运行验证：`pytest`（全量口径 = 无参数，含 tests/ + llmagent_tests/，**2354 passed / 9 skipped / 0 failed**，约 870 秒；干净跑带 `CODEBUDDY_SAFE_DELETE_ENABLED=0`）
→ 看 FAILED 行判定，不看退出码；`tests/architecture/` 红线测试零失败（128 项，秒级）
→ 提交关卡：`.git/hooks/pre-commit` 自动跑 architecture 红线（见第 10 条）；失败先归因再动手（`scripts/baseline_diff.py`，见第 13 条）

### 提交前检查：

→ 所有测试通过（pre-commit 关卡自动兜底 architecture 红线）
→ 不破坏架构不变性
→ 非平凡修改有决策记录
→ 结构性改动 / 质量优化**已先在 `../项目文档/优化/` 登记**（含同类点位全量清单）
→ 不提交 `.env.accel`、`outputs/`、`_debug_*.py`
→ `.agents/notes/` **随代码一起提交**（决策记录纳入版本管理，2026-09-12 起）

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
| `test_capability_parity.py` | 能力对账：接口对账 + 副作用 hook 对账（差集真机制，勿改白名单） |
| `test_hostile_delete_env.py` | 宿主敌意：safe-delete 护栏免疫，删除路径不被 SystemExit 逃逸 |
| R4 方向矩阵 | cli↔web 循环依赖已拆除：web→cli 仅命令注册副作用，禁止反向 |

> **契约 vs 配额（2026-09-15，G2 收口）**：上述两条计数型闸门（豁免总数 / 每文件引用次数）
> 已**降为看板**（超限只告警不 FAIL）。闸门改为**成员资格**：降级点须有登记命名空间，
> 状态写入者须是业主或被具名容忍。新增监管能力时优先问"这是成员资格校验，还是配额计数"——
> 配额拦住的是数量，拦不住资格（把 N 处集中到一个无权模块，计数不变而所有权已崩塌）。

***

## 工作环境硬约束与流程约定（2026-09-12 沉淀，踩坑实证）

### 环境硬约束

1. **safe-delete 护栏**：宿主 shim 会 hook stdlib 删除，单 turn 累计 ≥50 次抛 `SystemExit(1)`（`except OSError/Exception` 接不住），写章必越线 → 长驻进程/长测一律带 `CODEBUDDY_SAFE_DELETE_ENABLED=0`；删文件用 `rm`，**禁用 `git rm`**。
2. **沙箱会回滚工作区**：已改文件可能被回滚到 HEAD（最阴险是部分回滚：调用进了提交、import 行被吞，测试全绿上线 NameError）→ **改完立刻 commit + `git show <sha> -- <file>` 核验，禁止攒批**；任何异常的测试/git 数字先重跑一次再采信。
3. **并行 Edit 禁令**：并行 Edit 同一文件会互相覆盖（已三次事故），必须串行；并行会话下 Edit 失败 = 被并发改动，轮询等待静默再动手。
4. **"改了不生效"系列**：改 .py / `models.json` 不影响已运行进程，必须重启 Web/daemon；既有 daemon 存活时"重启 Web ≠ 换代码"（任务由旧 daemon 的代码执行，回合主仓后下一个任务才生效）；浏览器 Ctrl+F5 ≠ 服务端重载；daemon 解释器是系统 python 非 venv；沙箱 spawn 的长驻进程必被回收，daemon 只能由 Web/用户终端拉起。
5. **pytest 口径**：全量 = `pytest` 无参数（`testpaths = tests + llmagent_tests`，当前 **2354 passed / 9 skipped / 0 failed**，约 870 秒）；判定看 FAILED 行不看退出码；干净跑须 `CODEBUDDY_SAFE_DELETE_ENABLED=0`；基线见 `../.workbuddy/memory/MEMORY.md`（随提交刷新）。**提交关卡见下文第 10 条。**

### 流程约定

6. **agent-repair 是 linked worktree**：里面只做文件编辑、**禁跑 git**；与主仓同步用 cp 四件套 + models.json，**cp 会把 repair 侧旧版本带回主仓**（曾退回已删代码 → 假绿）→ cp 后必须 `git status` 逐条甄别，非本次改动 `git checkout HEAD --`。
7. **停任务 SOP**：daemon 活着时首选 `tq.request_stop()`（约 8s 生效）；"终止一直终止中" = daemon 死了（Web 只写 stop_requested）；`exit_code=2` = escalated 质量熔断非崩溃；清锁一律 `agent unlock`，**勿手删 writer.lock**。
8. **git**：双仓提交只推 gitcode，**无 GitHub 远程勿再补**（已拍板删除）；提交一律加 `-c commit.gpgsign=false`。
   - ⚠ **多行提交信息一律用 `-F <文件>`，不要用 `-m "..."`**（2026-09-15 两次实证）：`-m` 里的反引号会被当命令替换、`$` 会被展开、`*` 会被 glob 展开，**信息被静默改写且不报错**。踩过的例子：`` `except (SyntaxError, ...)` `` 整句消失、`scripts/*` 被展开成 `scripts/AGENTS.md: line 5: ...`。
   - ⚠ **漏了 `-c commit.gpgsign=false` 会「卡住」而不是「报错」**（本机 `commit.gpgsign=true`，2026-09-15 两次实证）：GPG 私钥缺失时 git 停在签名等待，表现为命令长时间无输出、bash 侧收到 SIGTERM，**看起来像 pre-commit 钩子挂住了**。排查顺序：① 是否漏带 flag；② 再怀疑钩子。`git commit --amend` 同样需要。
9. **设计红线**：失败必须显性化，绝不能被解读为通过；软维度单次 FAIL 不触发回滚（已翻转语义勿改回，旧测试报 rolled_back 属预期）；E3 `_pre_validation` 是有意取舍已登记豁免，勿擅自接线；新增降级点必须接 `degrade()`、新增 state.json 直写会被红线拦（豁免棘轮只减不增）。

***

## 监管与验证纪律（2026-09-15 新增，回溯 3 天 98 提交的六个缺口）

> 来源报告：`../.workbuddy/reports/2026-09-15-监管体系回溯-为什么修一个冒一个.md`
> 一句话：**「修一个冒一个」不是能力问题，是流水线缺三个环节（关卡 / 判据契约 / 同类盘点）。**

10. **提交前必须过架构红线（缺口 G1）**：`.git/hooks/pre-commit` 跑 `tests/architecture`（秒级，16 文件），`.git/hooks/pre-push` 跑全量。装一次即可：`bash scripts/githooks/install.sh`。
    - ⚠ `scripts/` 默认被 `.gitignore` 忽略（一次性脚本不入库），**唯独 `scripts/githooks/` 用负向规则放行** —— 钩子必须受版本控制，否则换机器即失效。新增钩子请放该目录。
    - **为何强制**：此前 CI 只在 `tags: v*` / 手动触发（无 push/PR），远程又只有 gitcode → 09-12~09-15 共 **98 次提交 CI 执行 0 次**；人只跑「受影响面」340/2315 = **15%**，**85% 测试面是提交盲区**，跨模块连带破坏（改 A 撞 B）只能等实弹。91 个碰 `src` 的提交里 **38 个（41%）零测试**。
    - ⚠ **统计日提交数必须用 `--since="YYYY-MM-DD 00:00"`**：裸日期 `--since="YYYY-MM-DD"` 会**漏掉当天前段**（实测 09-12 只算到 22 个，真实 49 个）。
    - 紧急绕过用 `git commit --no-verify`，但**必须在当日日志里记明原因**（绕过是显性行为，不许静默）。
11. **⚠ 两条红线的判据是启发式，写文案会撞（缺口 G2）**：`test_state_ownership`、`test_degrade_visibility` 曾用纯字符串出现次数做判据 —— **注释/日志文案里出现 `state.json` / `SILENT_DEGRADE` 字面量也会计数**。已实证：`092e55f` 补的 `degrade()` 文案含 "state.json"，**22 分钟后** `4bd2ec7` 只能改文案（语义完全没变）。
    - 2026-09-15 已升级为**语义判据**（`state_ownership` 走 AST 只数路径字面量；`degrade_visibility` 走 `tokenize` 只数 COMMENT）→ 文案不再计分。
    - 纪律：改这两条红线或新增降级点时，**先确认判据是"路径/注释"而非"任意字符串"**，不要把"为了让扫描过关"当成修复（那是 churn 的主引擎）。
    - **进而「配额 → 契约」（同日收口）**：判据语义化只解决"数得准"，没解决"管什么"。两条红线的**计数闸门已降为看板**，闸门改为**成员资格**：
      · 降级点 → 必须在 `core/infra/degrade_registry.py` 登记（未登记 / 僵尸条目均 FAIL）；
      · 豁免 → 必须写 `reason=<枚举>`（模糊理由另需 `ref=<登记单>`；悬空 ref FAIL）；
      · 状态写入 → 必须是业主，或在 `STATE_OWNERSHIP_TOLERATED` **具名**登记（reason+target）。
      **纪律**：新增监管能力先问「这是成员资格校验，还是配额计数」——配额拦住的是数量，拦不住资格（把 N 处写入集中到一个无权模块，计数不变而所有权已崩塌）；能用成员资格表达的，不要退化成配额。
12. **同类缺陷族必须一次列全（缺口 G4）**：修一处缺陷前，先枚举**全部同类入口**并在登记单写明「已覆盖 N / 共 M」。
    - **反面教材**：金三门禁链因每次只补下一个调用点（写时→批末→`/appeal`→rewrite→框架化），被跟进 **7 次**；degrade 可见化链按 except 点逐个清，**6 次**。
    - **正面范式（应制度化）**：`25a3293` 修完动作强度 → `479fcfc` 立刻把口径写成 M1–M6 矩阵红线 → **16 分钟后** `ddb4f14` 就被新红线抓出 `padding_repetition_abnormal` 作用域错配。**「修完立刻把口径棘轮化」是有效动作，不是可选项。**
13. **失败必须先归因再动手（缺口 G5）**：看到任何测试/指标失败，**先判定归属**（是不是我引入的），再改代码。
    - 首选工具 `python scripts/baseline_diff.py --base <基线sha>`（P2，一条命令给失败差集）；退化方案 `git archive <父sha> | tar -x -C $(mktemp -d)` 在快照上跑对照。
    - **反例代价**：`4e3c8ce`~`41dde29` 期间的 2 条存量失败因基线未刷新，导致后续每次全量都需重新甄别；基线三天漂移 5 版。
    - **基线刷新是改动的一部分**：改了代码就顺手把 `../.workbuddy/memory/MEMORY.md` 的基线行改掉，**禁止攒批**（基线不清 → 下一次判定就得重来）。
14. **监管动作必须包含"否决型"（缺口 G6）**：新增监管能力时，问一句「这是**发现**（detect）还是**否决**（prevent）」。
    - 现状全是 detect（红线测试 / `doctor` / `runtime_selfcheck` / 巡检）—— 红线起的是"事后罚款"，不是"事前设计约束"。刺眼证据：`1d52f7f` 的提交标题自带「**+红线豁免修补**」，即特性上线同一提交就给自己开豁免。
    - 纪律：**特性上线前先自问"这会撞哪条红线"**，而不是撞了再补豁免；能用 schema/关卡表达的约束，不要退化成"人记得检查"。
15. **★ 知识 ≠ 机制**：判据避坑、基线纪律这类经验，**写进 `../.workbuddy/memory/MEMORY.md` 不等于拦住复发**（G2 那个坑 09-13 就写进记忆第 79 行，`092e55f` 照样踩了）。
    - 能落成**红线断言 / 关卡 / schema 校验**的，一律落成机制；记忆只用于"暂时无法机制化"的取舍。

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
| `src/agent/core/story/`                   | 故事领域模型（设定/伏笔/章节/高潮曲线）                   |
| `src/agent/core/quality/`                 | 质量保障体系（护栏/一致性/评分/改写）                    |
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
| `scripts/githooks/`                       | 提交关卡钩子（pre-commit 跑红线 / pre-push 跑全量，`install.sh` 安装） |
| `scripts/baseline_diff.py`                | 失败归因工具：HEAD vs 基线失败差集（先归因再改码）          |
| `src/agent/core/infra/degrade_registry.py` | 降级命名空间**契约表**（新增 / 改名降级点必须同步登记）        |
| `../.workbuddy/memory/MEMORY.md`          | 长期记忆（测试基线、已收口缺陷、通用约定）                  |
| `../.workbuddy/reports/`                  | 复盘/回溯报告（含 2026-09-15 监管体系回溯）               |
| `.agents/skills/debugging.md`             | 调试方法论                                   |
| `.agents/skills/refactoring.md`           | 重构检查清单                                  |
| `.agents/skills/code-review.md`           | 代码审查清单                                  |

<br />
