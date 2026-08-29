# CLI 命令梳理报告 · 废弃候选清单

> 梳理日期：2026-08-29 · 代码基线：agent 仓 HEAD `7e6d656`
> **本文只做梳理与建议，未修改任何 CLI 代码。**
> 数据来源：typer 实际注册命令（66 个）+ `COMMAND_REGISTRY` 元数据（68 条）+ 测试引用统计 + Web UI 路由交叉核对。

---

## §0 结论速览

| 分级 | 数量 | 含义 | 建议动作 |
|---|---|---|---|
| **A 类 · 缺陷** | 2 | 注册表有元数据、无实现，运行必报错 | 立即修复或移除 |
| **B 类 · 接口重叠** | 5 | 能力有价值，入口可合并 | 合并入口，保留能力 |
| **C 类 · 低价值** | 6 | 零测试覆盖且已被 Web 覆盖/无人知晓 | 降级或移除 |
| **D 类 · 需补测** | 4 | 能力重要但零测试 | 保留并补测试 |
| 合计 | 17 / 66 | | |

**统计口径说明**：测试引用数按命令名的词频统计，`mode`(230)、`cost`(162)、`version`(122)、`status`(116) 等高位数字含通用词噪声（变量名/属性名），**不能作为活跃度证据**；但 **0 次的判定是可靠的**（既无连字符名也无下划线名出现）。

---

## §1 A 类 · 缺陷（P0，必须处理）

这两个命令在 `core/engine/command_router.py` 的 `COMMAND_REGISTRY` 基线里有元数据，但**从未被实现**。后果不是"文档写错了"，而是**会在 Web UI 上出现可点击但必失败的按钮**。

| 命令 | 声明的可用状态 | 实测结果 | 暴露范围 |
|---|---|---|---|
| `/audit`（一致性审计） | `WRITING` | `No such command 'audit'` | WRITING 状态下 Web 可用命令列表 |
| `/revise-architecture`（修订已确认架构） | `ARCH_CONFIRMED`、`WRITING` | `No such command 'revise-architecture'` | ARCH_CONFIRMED / WRITING 两态 |

实测暴露情况：

```
WRITING         可用 61 个 | 幽灵: ['revise-architecture', 'audit']
ARCH_CONFIRMED  可用 51 个 | 幽灵: ['revise-architecture']
OUTLINING       可用 51 个 | 幽灵: []
COMPLETED       可用 52 个 | 幽灵: []
PAUSED          可用 52 个 | 幽灵: []
```

### 为什么 P0：存在一条用户可见的死路

`src/agent/workflows/m14_architecture.py:287` 在架构已确认后抛出：

```python
raise RuntimeError("架构已确认，如需修改请先 /revise-architecture")
```

工作流**主动指引用户去执行一个不存在的命令**。用户照做只会得到 `No such command`。这是文档、代码、命令表三方不一致造成的真实阻断。

### 建议

二选一，**不要维持现状**：

- **方案 1（推荐）**：实现 `/revise-architecture` —— 架构确认后允许带反馈修订并同步下游（大纲/角色/已写章节打受影响标记），能力缺口真实存在，且 Web 端 6.5「改了上游，下游怎么跟上」的复核链路已经为它铺好了路。
- **方案 2**：从 `COMMAND_REGISTRY` 移除 `/revise-architecture` 与 `/audit`，同时**必须**改写 `m14_architecture.py:287` 的错误提示（改为指引到 `reset-state` 回滚或说明当前限制）。

`/audit` 的能力实际已由 `audit-chapter`（章节审核）+ `audit-setting`（设定冲突仲裁）分裂承接，倾向于**直接移除元数据**。

---

## §2 B 类 · 接口重叠（合并入口，保留能力）

### B1. `help` 是 `commands` 的纯别名，且描述与行为不符

```python
# help_.py
@command(global_=True)
def help_() -> None:
    """别名：列出命令"""
    commands()          # ← 直接调用，不带 -d
```

两个问题：

1. `help_` 函数体只有一行 `commands()`，无独立逻辑；而 typer 本身已提供 `novel-agent --help`。
2. `COMMAND_REGISTRY` 里 `/help` 的描述是「列出**当前可用**命令」，但实际调用 `commands()` 不带 `--dir`，输出的是**全量**命令清单 —— 描述与行为不符。

**建议**：保留 `commands`（支持 `-d` 按状态过滤，功能更全），`help` 降级为 `commands -d <当前目录>` 的语义或直接移除；无论如何要修正 `/help` 的元数据描述。

### B2. `summarize-chapter` + `summarize-range` → 可合并为 `summarize --range`

后者是前者的批量版（`-range A-B`），两者共用 `ChapterSummarizer`，仅入参形态不同（单章号 vs 区间）。测试覆盖 16 : 5。

**建议**：合并为 `summarize [--chapter N | --range A-B]`，减少一个入口。优先级低（不影响正确性）。

### B3. `foreshadow-check` + `foreshadow-report` → 职责相邻

- `foreshadow-check`：单支线未回收伏笔检查，不带参数时输出 dashboard；
- `foreshadow-report`：全书回收率报告，生成文件（总/回收/逾期/回收率）。

无参数形态下两者输出有重叠（都是统计视图），但带参数时职责清晰。

**建议**：保留两个，但把 `foreshadow-check` 的无参数 dashboard 形态合并进 `foreshadow-report`，避免"两个命令都能看统计"。

### B4. `ecosystem` 建议并入 `dashboard`

三个只读看板并存：

| 命令 | 输出 | 测试引用 |
|---|---|---|
| `cost` | LLMOps：调用追踪 / 成本基线 / 评测回归 | 通用词噪声 |
| `dashboard` | 只读可视化 HTML（可 `--serve`） | 30 |
| `ecosystem` | MCP 服务器状态 / 模型路由表 / 本地工具清单 | 0 |

`ecosystem` 的 MCP 部分依赖项目 `.state/mcp.json`，**多数项目根本没有该文件**，输出大概率是空看板。

**建议**：把模型路由表并入 `dashboard` 看板页（Web 端 `/p/{name}/dashboard` 已声明展示「成本 / 评测 / 模型路由 / MCP」四项，与 `ecosystem` 完全重合），随后移除 `ecosystem` 命令。

### B5. `compose` vs `autowrite`

两者都能"一键写完一本"，但定位不同：

- `autowrite`：全流程自主写作（规划→写→评→修），`--brief` 起步，Web 端「⚡ 自动续写」直接用它；
- `compose`：生成约束文档 → 多角色推进 → 完本，**完本后跑成书去重扫描**（`fullbook_dup_scan`），与 `scripts/compose.py` 共用 `compose_runner`。

`compose` 零测试覆盖，但它是 Web 端触发多角色长篇编排的唯一入口（`autowrite` 是单循环）。

**建议**：保留，但在 README 里明确分工（当前 README 把两者都写成"一键写书"，容易混淆）。

---

## §3 C 类 · 低价值（降级或移除）

### C1. `roster` / `philosophy` —— 已被 Web 完整覆盖

| 命令 | 本质 | Web 对应 | 测试 |
|---|---|---|---|
| `roster` | 打印 25 位专家 Agent 分组表 | `/p/{name}/team` 页面（内容更丰富，含 trait/engine） | 0 |
| `philosophy` | 打印一段静态设计哲学文案 | 首页 `/` 已渲染 `get_philosophy()` | 0 |

两者都是**纯静态文案输出**，不读项目、不改状态、无任何副作用。既然 Web 端已有更好的呈现，CLI 入口沦为鸡肋。

**建议**：保留函数（Web 在调用），移除 CLI 命令入口与注册表元数据。若担心脚本依赖，可标 `@deprecated` 过渡一个版本。

### C2. `show` —— 已被 Web 文件浏览完整覆盖

章节预览（默认末章、截断 300 字、`--json`）。Web 端 `/p/{name}/files` + `/p/{name}/file?path=` 可浏览并查看**全文**，能力严格更强。测试引用 28（中等）。

**建议**：降级为内部辅助，不再作为对外主推命令；`--json` 形态若有脚本依赖则保留。

### C3. `resize-scope` —— 功能实用但"隐形"，0 测试覆盖

调整项目体量（short/medium/long/mega/custom）并按新体量重新生成大纲，实现完整、有真实用途（长篇改百万字、改单章字数）。

**问题**：梳理前 **README 与功能总览均未记录该命令**（本次梳理已在 README §九 补齐）。零文档 + 零测试 = 事实上无人使用。

**建议**：保留（实现质量不错，且这是唯一能改体量的入口），补测试 + 在 Web 工作台暴露（当前 Web 只在新建项目时能设体量，建完就改不了）。

### C4. `rollback-setting` —— 0 测试覆盖

快照三件套之一（`snapshot` / `list-snapshots` / `rollback-setting`）。前两者有测试（4 / 1），`rollback-setting` 为 0。

**建议**：保留（快照回滚是安全网的一部分），补测试。若要精简，可并入 `rollback --setting`。

### C5. `payoff-plan` —— 0 测试覆盖，Web 未暴露

G12 爽点剧本生成（按压力阶段确定性生成全书爽点/情绪目标，零 LLM）。能力有特色，但 Web 端没有任何入口，CLI 也无测试。

**建议**：要么在 Web 工作台暴露（爽点剧本可视化价值高），要么与 `emotion-track` 合并为「爽点/情绪」一组。

### C6. `merge-genres` —— 0 测试覆盖，但 Web 依赖

多题材冲突裁决，Web 端 `/p/{name}/conflicts` 页面 `POST /api/conflicts/{name}/resolve` 直接驱动它。

**建议**：**保留**（Web 核心链路依赖，虽然 0 测试覆盖说明测试缺口在 Web 侧而非命令本身）。

---

## §4 D 类 · 能力重要但零测试（建议保留 + 补测试）

| 命令 | 为什么不能废弃 | 测试 |
|---|---|---|
| `guardrail-scan` | G14 成书质量护栏全量体检（英文残留 / 占位标题 / 跨章重复），README 主推命令 | 0 |
| `web` | Web 工作台入口，当前主推方向 | 0 |
| `compose` | 多角色长篇编排唯一入口（见 B5） | 0 |
| `iceberg` | 冰山建书 60+ 字段骨架，`--generate` 有真实产出，Web 无对应页面 | 0 |

> **注意**：`web` 零测试覆盖是**系统性风险** —— Web UI 已承载引导向导、实时写作间、复核检查单、问答模板、图谱编辑等核心链路，且 `runner.py` 涉及子进程管理、SSE 流、并发任务，完全没有测试保护。建议单独立项补 Web 层测试，优先级高于任何命令废弃。

---

## §5 零测试覆盖命令全表（12 个）

| 命令 | 分类 | 处置建议 |
|---|---|---|
| `audit` | A 缺陷 | 移除元数据 |
| `revise-architecture` | A 缺陷 | 实现或移除 |
| `cmd-list`（命令名 `commands`） | B1 | 保留（比 `help` 更全） |
| `compose` | B5 / D | 保留，补测试 |
| `ecosystem` | B4 | 并入 `dashboard` 后移除 |
| `guardrail-scan` | D | 保留，补测试 |
| `iceberg` | D | 保留 |
| `merge-genres` | C6 | 保留（Web 依赖） |
| `payoff-plan` | C5 | Web 暴露或合并 |
| `philosophy` | C1 | 移除 CLI 入口 |
| `resize-scope` | C3 | 保留 + 补文档测试 |
| `rollback-setting` | C4 | 保留，补测试 |
| `roster` | C1 | 移除 CLI 入口 |
| `web` | D | 保留，补测试（高优先级） |

---

## §6 附带的文档一致性问题

梳理过程中发现的文档与代码不一致（本次已修复前两项）：

1. ✅ **已修**：README 与功能总览均写「65 个命令」，实际 typer 注册 **66 个** —— 已在 `功能总览.md` 校正。
2. ✅ **已修**：`resize-scope`、`cost`、`commands` 三个命令 README 完全未记录 —— 已在 README §九 补齐。
3. ⏳ **待修**：README §八 命令速查表的 `help` 项若按 B1 调整需同步更新。
4. ⏳ **待修**：`README` 与 `功能总览.md` 未提示 `/audit`、`/revise-architecture` 不可执行（若按方案 2 移除元数据则自动解决）。

---

## §7 建议的执行顺序

| 步骤 | 动作 | 风险 |
|---|---|---|
| 1 | 修复/移除 `/revise-architecture` 与 `/audit`，并改写 `m14_architecture.py:287` 提示 | 低 |
| 2 | 移除 `roster` / `philosophy` 的 CLI 入口（保留函数供 Web 调用） | 低 |
| 3 | 立项补 Web 层测试（`runner.py` / `state.py` / 关键 API） | 中（工作量） |
| 4 | `ecosystem` 并入 `dashboard` 后移除 | 中（需前端配合） |
| 5 | `help` 语义修正、`summarize-*` 合并、`foreshadow-*` 无参形态合并 | 低（可缓） |
| 6 | `resize-scope` / `payoff-plan` 在 Web 暴露 | 低（增量） |

> 步骤 1 建议优先：它是唯一会造成用户操作失败并中断写作流程的问题。
