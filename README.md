# NovelAgent 使用指南（面向作者 / 使用者）

> 本指南介绍 **NovelAgent 这个写作软件本身怎么用**：如何安装、配置大模型、把一本小说从 0 写到完结、以及日常查看 / 修改 / 导出。  
> 如果你要的是"一口气写完整本"，直接用内置的 **`compose` 命令**（一条命令开新书并写到完本，见 §4.0）。本指南聚焦软件本身用法。

---

## 一、NovelAgent 是什么

NovelAgent 是一个**共创式长篇小说写作 Agent**：给它一段思路（题材 / 核心梗 / 风格 / 体量），它能自主完成「世界观 → 大纲 → 角色 → 逐章写作 → 成书评测」全流程，并以「不崩」七维终审量化保证质量。

它的核心能力：

- **网页工作台**：`novel-agent web` 启动零构建 Web UI——引导向导七步走通创作闭环、实时写作间看 SSE 进度与成本一键续写、改了上游自动提示下游复核（**第六章**）；
- **全自主写作**：`autowrite` 一键输入思路，全自动完成（支持 `auto` 全自主 / `light` 关键节点介入 / `heavy` 每章控制）；`compose` 一键写完整本；
- **长篇一致性**：角色关系网、主角成长路线、金手指登记，随剧情演化但不矛盾；**连续性账本**（事实 / 信息差 / 未闭环 / 章交接）随时可查，伏笔由**确定性状态机**管理（埋设→强化→回收，逾期自动标记）；
- **主线分线预算**：各支线章数上限硬约束（越界自动切下一支线），LLM 主编按进度动态重规划占比；
- **剧集树（支线 / 卷）**：按 `outline.md` + 每条支线的 `subline.md` 推进；
- **质量门禁**：每章多重校验（爽点 / OOC / 连贯性 / 追读力 / AI 味 / 注水 / 黄金三章），不通过自动修订；存量章节可 `deslop` 批量去 AI 味（6 指标分级改写）；
- **成书体检**：`evaluate` 跑「不崩」七维终审 + `appeal` 迷爱看 6 维评分，缺陷自动回溯修复；
- **G14 成书质量护栏**：写时 BLOCK 英文残留 / 占位标题 / 跨章重复 / 元指令泄漏，`guardrail-scan` 全量体检；
- **笔枢对标能力**：Agent 阵容叙事（Web `/team` 页）、冰山建书 60+ 字段（`iceberg`）、双模式连续滑块（`mode --autonomy`）、可拖拽世界关系图谱（`graph`）；
- **可逆操作**：快照、回滚、归档，写错了能后退。

### 两种入口：CLI 与 Web 工作台（能力同源、完全一致）

NovelAgent 提供**两种等价的使用入口**，可以混着用、随时切换：

- **命令行（CLI）**：`novel-agent <命令>`，适合批处理、自动化、脚本驱动（CI / 定时任务 / 外部编排）。
- **网页工作台（Web）**：`novel-agent web` 启动零构建 Web UI（FastAPI + Jinja2 + HTMX），把创作闭环做成可视化界面，适合日常写作、引导式上手、实时看进度与成本。

> **能力完全一致**：Web 端以子进程调用同一份 CLI（`python -m agent.cli <command>`），因此"网页上能做的命令行都能做，命令行能跑的网页「高级命令」面板也都能跑"，不存在"网页少几个功能"。详见 §6。

下面把"同一件事"在两种入口下的做法对照起来，后面章节再分别展开：

| 想做的事         | CLI 命令                           | Web 入口                             |
| ------------ | -------------------------------- | ---------------------------------- |
| 启动软件         | —                                | `novel-agent web` → 浏览器打开          |
| 开新书 / 引导式上手  | `start` / `compose`              | `/p/{name}/guide` 七步向导             |
| 写章节（逐章）      | `write`                          | `/p/{name}/write` 写作间              |
| 自动续写（多章）     | `autowrite`                      | 写作间「⚡ 自动续写」                        |
| 一键写完整本       | `compose`                        | 工作台「✍️ 创作」卡片                       |
| 调自主度 / 模式    | `mode`                           | 工作台「⚙️ 创作调校」滑块                     |
| 看状态 / 进度     | `status`                         | 项目工作台 `/p/{name}`                  |
| 角色 / 关系 / 伏笔 | `design-characters` / `adjust-*` | `/p/{name}/team`、`/p/{name}/graph` |
| 体检 / 评测      | `doctor` / `evaluate`            | `/p/{name}/dashboard`              |
| 导出成书         | `export`                         | 工作台导出入口                            |

命令行入口统一为 `novel-agent`（安装后），等价于 `python -m agent.cli`。所有命令都接受一个 `-d/--dir <项目目录>` 参数指向你的小说项目。

> **状态机**：NovelAgent 内部有状态流转（INIT → 配置/讨论 → 架构确认 → 大纲 → 角色 → 写作 → 完结）。每个命令都有"门禁"——**在错误的阶段运行会被拒绝并提示下一步该做什么**。所以照着下面的顺序走即可，不用担心顺序乱。

---

## 二、安装

### 2.1 环境要求

- **Python 3.11+**（本体运行建议 3.11+；自动化脚本依赖内置 `tomllib`，必须 3.11+）。
- 能联网装包（`pip`）。

### 2.2 安装 agent 包

NovelAgent 由**两个包**组成：应用包 `agent`（本仓库）+ 编排内核包 `llmagent`（旁边的 `../llmagent`，提供统一 LLM 网关 Gateway）。**两个包需在同一工作区根目录下，且要先装内核再装应用**：

```bash
# 方式 A：正式安装（构建 wheel）
pip install ./llmagent
pip install ./agent

# 方式 B：editable 安装（改源码即时生效，推荐开发 / 调试期）
pip install -e ./llmagent
pip install -e ./agent
```

依赖见 `agent/pyproject.toml`（核心：typer、rich、pydantic、jinja2、openai、pyyaml、python-frontmatter、python-dotenv、**fastapi、uvicorn、python-multipart**——后三者用于 Web UI；另有本地依赖 `llmagent`）。

> **Web UI 依赖**：启动网页界面（`novel-agent web`）需要 `fastapi` 与 `uvicorn`，二者已写入 `pyproject.toml` 的 `dependencies`。若你是早先按旧 README 安装、没装过这俩包，重跑一次安装命令即可（见下方 2.2）。

### 2.3 验证安装

```bash
novel-agent --help
# 或（不安装包，从仓库根目录把两个 src 加入 PYTHONPATH，<NovelAgent> 即仓库根）：
# Windows（PowerShell）：
$env:PYTHONPATH="./agent/src;./llmagent/src"; python -m agent.cli --help
# Linux / macOS：
PYTHONPATH=./agent/src:./llmagent/src python -m agent.cli --help
```

能打印出命令列表即安装成功。若报 `No module named 'llmagent'`，说明没装内核包（见 §八 故障排查）。

---

## 三、配置大模型（`.env`）

NovelAgent 通过 `.env` 读取 LLM 配置。**把 `.env` 放在 agent 仓库根目录**（`agent/.env`，已 gitignore，不会入库）。加载顺序会依次尝试：当前工作目录及祖先目录的 `.env`、以及 `agent/.env`（仓库根）。所以"密钥随代码仓库一起放根目录"即可被自动读取。

### 3.1 全部环境变量

| 变量                       | 含义                                                                           | 默认值        |
| ------------------------ | ---------------------------------------------------------------------------- | ---------- |
| `LLM_PROVIDER`           | 提供商：`openai`（兼容 OpenAI 协议）或 `ollama`（本地）                                     | `openai`   |
| `LLM_API_KEY`            | API 密钥（ollama 不需要）                                                           | 空          |
| `LLM_BASE_URL`           | 服务地址。openai 默认 `https://api.openai.com/v1`；ollama 为 `http://localhost:11434` | 按 provider |
| `LLM_MODEL_ID`           | **主模型**（创作：写章节 / 架构 / 角色），质量优先                                               | `glm-5.2`  |
| `LLM_MODEL_UTILITY`      | 轻量模型（校验 / 摘要 / 一致性，省成本），留空则等于主模型                                             | 空          |
| `LLM_TIMEOUT`            | 单次请求超时（秒）                                                                    | `120`      |
| `LLM_MAX_RETRIES`        | 失败重试次数                                                                       | `3`        |
| `LLM_ENABLE_THINKING`    | 思考开关：`true`/`false`/`空`（不干预模型默认）。批量写长篇建议 `false` 提速省 token                   | 空          |
| `LLM_EMBEDDING_MODEL`    | 嵌入模型（RAG 用），留空回退主模型                                                          | 空          |
| `LLM_EMBEDDING_BASE_URL` | 独立嵌入端点（可选）                                                                   | 空          |
| `LLM_EMBEDDING_API_KEY`  | 嵌入端点密钥（可选）                                                                   | 空          |

### 3.2 示例：本项目实际使用的智谱 GLM

新建 `agent/.env`，内容：

```dotenv
LLM_PROVIDER=openai
LLM_API_KEY=你的智谱APIKey
LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4/
LLM_MODEL_ID=glm-4.7
LLM_MODEL_UTILITY=glm-4.7
LLM_TIMEOUT=180
LLM_MAX_RETRIES=3
LLM_ENABLE_THINKING=false
```

### 3.3 其它厂商配置

任何兼容 OpenAI 协议的服务都能用，改三项即可：`LLM_BASE_URL` / `LLM_MODEL_ID` / `LLM_API_KEY`。

```dotenv
# OpenAI
LLM_API_KEY=sk-你的OpenAI密钥
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL_ID=gpt-4o
LLM_MODEL_UTILITY=gpt-4o-mini

# DeepSeek
LLM_API_KEY=你的DeepSeek密钥
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL_ID=deepseek-chat

# Moonshot Kimi
LLM_API_KEY=你的Kimi密钥
LLM_BASE_URL=https://api.moonshot.cn/v1
LLM_MODEL_ID=moonshot-v1-32k
```

`LLM_MODEL_UTILITY`（轻量模型，做校验 / 摘要）留空则复用主模型；配上它能明显省钱省时。

### 3.4 本地模型（ollama，零成本离线写作）

```dotenv
LLM_PROVIDER=ollama
LLM_BASE_URL=http://localhost:11434
LLM_MODEL_ID=qwen2.5:14b
# 无需 LLM_API_KEY
```

> `.env` 含密钥，**不要提交到 git**（项目已配置 `.gitignore`）。

### 3.5 临时指定 .env

部分命令支持 `--env <路径>`（仅本次命令生效，透传给下游 LLM 客户端）：

```bash
novel-agent write -d novels/my-novel --env ./another.env
novel-agent doctor -d novels/my-novel --env ./another.env
```

---

## 四、核心写作流程（从新书到完结）

建议先 `cd` 到你的**工作区根目录**（即 `agent/.env` 所在目录，或任何能让 NovelAgent 找到 `.env` 的目录），再用 `-d` 指定小说项目。项目默认相对路径是 `novels/my-novel`。

### 4.0 全自主写作（推荐，默认模式）

只需给一段思路，Agent 全自动完成整本书：

```bash
novel-agent autowrite -d novels/my-novel --brief "玄幻+废柴逆袭+爽文" --chapters 30
```

全流程自动串联：世界观生成 → 脉络讨论 → 故事架构 → 大纲 → 角色设计 → 逐章写作 → 成书评测。支持 `--mode auto`（默认全自主）/ `--mode light`（关键节点询问）/ `--mode heavy`（每章控制）。

**成书质量体检：**

```bash
novel-agent evaluate -d novels/my-novel              # 「不崩」七维终审（默认真 LLM 实判）
novel-agent appeal -d novels/my-novel --chapter 12    # 「迷爱看」6 维评分
novel-agent guardrail-scan -d novels/my-novel         # 成书质量护栏全量扫描
novel-agent review-book -d novels/my-novel            # 成书多视角对抗式评审（编辑审稿视角）
```

**去 AI 味（存量章节）：**

```bash
novel-agent deslop -d novels/my-novel                    # 6 指标分级扫描（默认只出报告）
novel-agent deslop -d novels/my-novel --apply            # 实际改写并写回（写回前自动备份）
novel-agent deslop-chapter -d novels/my-novel --chapter 5 --level heavy   # 单章深度改写
```

**反馈改写：**

```bash
novel-agent rewrite -d novels/my-novel --chapter 12 --feedback "节奏太快，放慢并补细节"
```

**一键完本：**

```bash
# 开新书并写到完本
novel-agent compose --name "我的修仙路" --scope long --genre xiuxian \
    --story-core "废柴逆袭，一路打脸" --chapters 120

# 续写已有项目（跑到目标章数）
novel-agent compose -d novels/my-novel --chapters 30

# 跳过完本自动体检（evaluate + foreshadow-report）
novel-agent compose -d novels/my-novel --chapters 30 --no-checkup
```

`compose` 生成约束文档后多角色推进至完本，写完全书自动做段落去重扫描。它**不是** `autowrite` 的别名——`autowrite` 是单轮全流程（规划→写→评→修），`compose` 是跨轮编排到完本。

### 4.1 开新书 —— `start`

```bash
novel-agent start -d novels/my-novel
```

交互式收集 **标题 / 体量 / 题材 / 风格 / 故事核心**，然后调 LLM 生成世界观 `world.md`。这是第一步，后续所有命令都依赖它。

### 4.2 脉络讨论 —— `discuss`

```bash
novel-agent discuss -d novels/my-novel            # 默认最多 10 轮
novel-agent discuss -d novels/my-novel -r 15      # 最多 15 轮
```

与 Agent 多轮对话，深化故事思路，产出 `discussion.md`。在对话框输入 `/next` 结束讨论。

### 4.3 大纲 —— `outline`

```bash
novel-agent outline -d novels/my-novel
```

基于已确认的架构生成 `outline.md`（故事简介 + 顶层支线任务列表），并为每条支线创建 `subline.md`。

### 4.4 角色设计 —— `design-characters`

```bash
novel-agent design-characters -d novels/my-novel
```

产出：

- `protagonist_route.md`（树状主角成长路线）
- `characters/<姓名>.md`（角色档案）
- `relations/graph.md`（Mermaid 关系网）
- `foreshadows.md`（初始伏笔表）
- `golden_finger_registration.md`（金手指登记，冻结）

### 4.5 写章节（核心循环）—— `write`

```bash
novel-agent write -d novels/my-novel                  # 写下一章
novel-agent write -d novels/my-novel --no-strict-review   # 跳过 D 严格审查（更快，质量偏弱）
```

每一章的执行链条（Agentic 引擎）：

1. **7 步上下文加载**（world → subline → route → relations → characters → foreshadows → 题材规则）；
2. **WriterAgent 自主起草**（ReAct 循环：自主调用工具检索设定/查伏笔/自检质量），Critic 外环审稿；
3. **质量校验**（9 项通用规则），不通过自动修订（≤2 次）；
4. **主线推进仲裁**：分线预算硬上界（越界自动切下一支线），末段自动进入结局模式；
5. 持久化 `chapters/ch<NNN>.md` 并更新进度指针、伏笔状态机与连续性账本。

- 加 `--json` 会以 JSON 输出结果（供外部脚本 / 自动化驱动调用，stdout 是 JSON，rich UI 走 stderr）。
- 可能触发 **前置冲突检测**（`pre_validation_blocked`）：当世界观出现高严重度冲突时，生成被暂停。此时按报告修改 `world.md` / `subline` / 角色档案，或用下面的 `adjust-*` 调整，再重跑 `write`。

> 想"一口气写完整本"而不手动反复敲命令？用 **`compose`**（见 §4.0），它会自动处理限流退避、续写与完本体检。

### 4.6 一致性演化 —— `adjust-relation` / `adjust-route`

```bash
novel-agent adjust-relation -d novels/my-novel -i "赵无极对林寻从对立转为暗中赏识"
novel-agent adjust-route    -d novels/my-novel -i "让主角在N02选择加入执法堂当卧底，后期再反水"
```

让**关系网** / **主角成长路线**随真实剧情演化：

- 旧关系不会删除，而是标记为 `archived` + 强度 0（归档边单独成章）；
- 旧路线分支保留为 `archived_alt`（备选）；
- 同时产出**一致性影响报告**，标注与 world / 已写章节 / 金手指的冲突（高 / 中 / 低）。

`-i/--intent` 是必填的自然语言意图；也可不加 `-i` 让它交互式询问。

### 4.7 导出成书 —— `export`

```bash
novel-agent export -d novels/my-novel -f txt                 # 导出 TXT
novel-agent export -d novels/my-novel -f markdown -t "我的修仙路"   # 指定书名
novel-agent export -d novels/my-novel -f epub -o ./output    # 导出 EPUB 到指定目录
```

支持 `txt` / `markdown` / `epub`，默认输出到 `<项目>/exports/`。中途或完结都能导出。

> 💡 **Web 等价**：本章每条命令在 Web 工作台都有对应入口——引导式流程见 `/p/{name}/guide` 七步向导，单章/续写见 `/p/{name}/write` 写作间，其余命令可在项目工作台「高级命令」折叠区直接运行（见 §6.6）。

---

## 五、日常辅助命令

### 5.1 查看状态 —— `status`

```bash
novel-agent status -d novels/my-novel            # 富文本：状态/模式/进度/可用命令
novel-agent status -d novels/my-novel --json    # JSON：state/mode/progress/available_commands
```

随时看"写到第几章、当前状态、接下来能跑什么命令"。

### 5.2 介入模式 —— `mode`

```bash
novel-agent mode -d novels/my-novel                    # 查看当前模式
novel-agent mode -d novels/my-novel -t light           # 切到 light
novel-agent mode -d novels/my-novel -t auto            # 切到 auto（全自动，重大决策才打断）
```

三档：**heavy**（每章前问方向、每章后等反馈）/ **light**（仅剧情节点介入）/ **auto**（自主推进）。

**进阶：连续自主度滑块**（0–100，替代固定三档）：

```bash
novel-agent mode -d novels/my-novel --autonomy 80      # 高度自动，但重大决策仍询问
novel-agent mode -d novels/my-novel --autonomy 100     # 预设：auto-driver（完全自主）
novel-agent mode -d novels/my-novel --autonomy 35      # 预设：co-pilot（协作模式）
```

自主度越高越自动，`MAJOR_DECISION` 始终打断作为安全底线。Web UI 项目空间以连续滑块呈现。

### 5.3 健康体检（只读）—— `doctor`

```bash
novel-agent doctor -d novels/my-novel            # 只读诊断，绝不修改
novel-agent doctor -d novels/my-novel --ping     # 额外探测 embedding / LLM 端点可达性
```

逐项检查：结构（阶段产物是否齐全）、状态机、设定集、RAG 索引、依赖配置，并给出**修复命令**。出问题先跑它。

### 5.4 可视化 Dashboard —— `dashboard`

```bash
novel-agent dashboard -d novels/my-novel -o out.html          # 生成自包含只读 HTML（可双击打开）
novel-agent dashboard -d novels/my-novel --serve --port 8080  # 起本地只读服务（Ctrl-C 关闭）
```

聚合关系图、主角路线、伏笔、进度、节奏、健康诊断，渲染为只读可视化。**只读，绝不修改任何项目文件**。

### 5.5 安全网：快照与回滚 —— `snapshot` / `rollback`

```bash
novel-agent snapshot -d novels/my-novel -l before-revision   # 给设定集打快照
novel-agent rollback -d novels/my-novel -c 20     # 回滚到第 20 章（1-19 保留，20+ 归档到 chapters/_archived/）
novel-agent rollback -d novels/my-novel -c 20 -y  # 跳过二次确认
```

写歪了不怕：快照随时存，回滚把后续章节归档（不删），进度指针退回，从指定章重写。

### 5.6 其它维护命令

| 命令            | 作用                     |
| ------------- | ---------------------- |
| `reset-state` | 重置状态机（慎用，回到初始）         |
| `reindex`     | 重建 RAG 索引（长篇章节多了推荐定期跑） |
| `resume`      | 从异常中恢复                 |
| `context`     | 查看当前上下文拼装              |
| `version`     | 打印版本                   |
| `help`        | 查看帮助                   |

### 5.7 长篇管理（主线预算 / 连续性）

| 命令 | 作用 |
| --- | --- |
| `mainline-init` | 初始化/更新分线预算计划：`--subline S01=240 --subline S02=180` 精确指定各支线章数上限（硬约束，越界自动切下一支线）；不 init 则按体量均衡分账 |
| `mainline-show` | 查看分线预算与当前推进状态（各支线预算由 LLM 主编按进度动态重规划） |
| `continuity` | 连续性账本只读查看：结构化的事实 / 信息差（谁知道什么）/ 未闭环项 / 章交接 + 伏笔确定性状态机；`--action import-foreshadow` 可把旧扁平伏笔表一键导入 |
| `foreshadow-check` / `foreshadow-report` | 伏笔一致性检查 / 回收率报表（基于确定性状态机） |

> 💡 **Web 等价**：上述查看 / 诊断 / 安全网命令同样可在 Web 项目工作台的「高级命令」面板一键运行，无需切回终端。

---

## 六、Web 工作台详解（与 CLI 一一对应）

命令行适合批处理、自动化与脚本驱动；**Web 工作台适合日常写作与引导式上手**——两者底层调用同一套命令，能力完全一致。NovelAgent 自带一个**零构建的 Web UI**（FastAPI 服务端渲染 + Jinja2 + HTMX 局部刷新，无需 Node 工具链），把 §4/§5 的 CLI 创作闭环做成可视化工作台：左边推进度，右边出内容，写章过程实时可见。

依赖 `fastapi` + `uvicorn` + `python-multipart`，已写入 `pyproject.toml`，随包安装。

### 6.1 启动

```bash
novel-agent web                                  # 默认 http://127.0.0.1:8000
novel-agent web --host 0.0.0.0 --port 8080       # 指定监听地址 / 端口
```

等价写法（未安装成命令时）：

```bash
python -m agent.cli web      # 走 CLI 的 web 命令
python -m agent.web          # 直接跑 web 模块
```

浏览器打开后停止服务用 `Ctrl-C`。若报 `ModuleNotFoundError: No module named 'uvicorn'`，重跑 `pip install -e ./agent` 即可。

> **小说数据默认放在哪？**  
> Web 工作台扫描的小说目录是一个**独立的数据根目录**，不在 `agent/` 代码仓库内（代码与数据分离：`agent/` 只放代码，小说数据统一落在外面）。
>
> - **默认位置**：`agent/` 仓库的**上一级**目录下的 `novels/`，即仓库结构下的 **`<NovelAgent>/novels/`**（`<NovelAgent>` 指本仓库根目录，Windows/Linux/macOS 均为此路径）。
> - 进页面看到的「项目列表」、以及新建项目 `POST /api/projects` 落地的位置，都来自这个根目录下的子文件夹（每个子文件夹 = 一本小说）。
> - **想换位置**：用环境变量 `NOVEL_DATA_ROOT` 覆盖即可，例如把数据根改到 `agent/` 内的 `projects/`：
>   ```bash
>   NOVEL_DATA_ROOT=<NovelAgent>/agent/projects novel-agent web
>   ```
> - **CLI 对照**：CLI 命令的 `--dir` 帮助占位符写的是 `projects/my-novel`，那只是示例占位、并非硬编码默认值；CLI 每次需显式传 `-d`，而 Web 固定绑定上面的数据根，无需每次指定。

### 6.2 Web 是怎么驱动写作的

理解这一点，后面所有页面行为就都通了：

```
浏览器 ──POST /api/run──▶ FastAPI ──子进程──▶ python -m agent.cli <command> --dir <项目>
   ▲                         │                          │
   │                         │                          ├─ 写 stdout → SSE log 事件
   └──── SSE 事件流 ─────────┤                          └─ 写 .state/progress.json → SSE progress 事件
                             └── 进程结束 → SSE done 事件（退出码 + 看板摘要 + 最新状态）
```

- **不另写一套逻辑**：Web 端以子进程调用同一份 CLI，因此网页上的能力和命令行**永远一致**，不存在"网页少几个功能"。
- **实时性来自两路**：子进程 stdout 逐行推 `log`；同时每 0.4 秒轮询项目 `.state/progress.json`（G9 进度事件总线已落盘），按事件 seq 增量推 `progress`。
- **防假死**：SSE 之外还有 `GET /api/runs/{run_id}` 轮询兜底，断流时前端靠它收尾。
- **不会卡死**：子进程 `stdin=DEVNULL`，任何需要交互输入的命令都会立刻 EOF 失败而不是挂起。
- **门禁同源**：网页上能点哪些操作，由状态机的 `available_commands` 决定，与 CLI 的门禁完全对齐。

### 6.3 引导向导：七步走通创作闭环

`/p/{name}/guide` 是**新书上手的主路径**。它把第四章的流程拆成 7 个阶段页，每页做三件事：**回显上一阶段的产物 → 一键生成本阶段产物 → 就地编辑后保存**。

| # | 阶段页  | 状态机状态              | 产物                  | 一键动作    |
| - | ---- | ------------------ | ------------------- | ------- |
| ① | 开新书  | `INIT`             | `world.md`          | 生成世界观   |
| ② | 脉络讨论 | `DISCUSSING`       | `discussion.md`     | 开始讨论    |
| ③ | 故事架构 | `ARCHITECTING`     | `architecture.md`   | 生成架构    |
| ④ | 架构确认 | `ARCH_CONFIRMED`   | —                   | 确认并解锁下游 |
| ⑤ | 创作大纲 | `OUTLINING`        | `outline.md`        | 生成大纲    |
| ⑥ | 角色设计 | `CHARACTER_DESIGN` | `characters/*.md`   | 设计角色    |
| ⑦ | 写章节  | `WRITING`          | `chapters/chNNN.md` | 进入写作间   |

向导的实用细节：

- **自动定位**：直接访问 `/p/{name}/guide` 会按项目当前状态重定向到你该去的那一页，不需要记 URL。
- **进度条可点击**：七段进度条任意跳转，回看/补改已完成阶段。
- **富文本回显 + 就地编辑**：产物不是源码直出，而是渲染后的富文本；点编辑可直接在网页里改 `world.md` / `outline.md` 并写回本地（`POST /p/{name}/save-stage`）。
- **手动干预豁免**：已生成过产物的阶段允许直接编辑保存，不受状态机门禁限制——想手改大纲随时改。
- **阶段状态标签**：每个阶段标注「已确认 / 受影响·待复核」，上游改了会标黄提醒（见 6.5）。

### 6.4 实时写作间

`/p/{name}/write` 是**日常产出章节的地方**，两种写法的区别要分清：

|      | 单章写入      | ⚡ 自动续写                     |
| ---- | --------- | -------------------------- |
| 底层命令 | `write`   | `autowrite --chapters <N>` |
| 产出   | 下一章       | 连续多章，跑到目标章数为止              |
| 适合   | 想盯着质量逐章打磨 | 想一口气推进一批章节                 |

两者都可选**引擎模式**（`auto` 自主 Agentic Loop / `heavy` 更严 / `light` 更轻）和**严格质量审查**开关（关闭等价于 CLI 的 `--no-strict-review`，更快但质量偏弱）。

**关于自动续写的目标章数**：界面上填的是"再写几章"，前端会把它叠加到当前已写章数上，换算成 `autowrite` 需要的**绝对目标章数**再下发。所以填 5 就是"再多 5 章"，不会因为重跑而从头写。

写作过程中的可见性：

- **实时控制台**：点任意运行动作会弹出运行控制台，分「日志 / 时间线 / 状态」三栏——日志是 CLI 实时输出，时间线是 G9 进度事件（第 N 章、当前阶段、耗时）。
- **成本视图**：章节写入完成后，若触发成本预警会在对应卡片上直接显示预警等级。
- **章节列表**：写作间底部实时刷新已生成章节，点标题直接跳文件查看。

> 项目工作台 `/p/{name}` 的「✍️ 创作」卡片里同样有一键续写入口，不必每次都进写作间。

### 6.5 改了上游，下游怎么跟上

这是 Web 端相对 CLI 的**增量能力**，也是长篇最容易翻车的地方：改了世界观，大纲和已写章节还作数吗？

**阶段复核检查单**（`GET /api/review/{name}?stage=<阶段>`）：

1. 页面检测到某阶段的上游产物被改动过（比对 mtime 与基线），该阶段标为「受影响·待复核」；
2. 点「生成检查单」，调用 M19 复核同步，由 LLM 找出下游阶段**未被覆盖**或**与新设定冲突**的条目；
3. 逐条**采纳 / 忽略**裁决（`POST /api/review/{name}/decision`）；
4. 采纳过的条目进 `adopted_history` 持久化，重新生成时不会重复提同一个问题；
5. 同时列出**可能受影响的已写章节**清单，供你决定要不要抽查重写。

复核完成后点「确认本阶段」记录新基线，黄色标记消除。写作边界也覆盖在内——上游改动同样可能波及已写章节。

**阶段问答模板**（`/api/qa/{name}`）：每个内容阶段内置一组引导问题，回答后随项目保存；用于把作者的隐性意图显性化，比空跑生成更可控。答不上来的可以跳过，也可以补充自由描述。

### 6.6 世界构建与调校

| 页面       | 路径                    | 能做什么                                                           |
| -------- | --------------------- | -------------------------------------------------------------- |
| 世界关系图谱   | `/p/{name}/graph`     | 力导向图**可拖拽编排**，5 类节点（人物/势力/地点/物品/伏笔）按类型着色，点选编辑，全量保存含坐标；可一键填充示例图 |
| Agent 阵容 | `/p/{name}/team`      | 25 位专家 Agent 按四组（世界构建/情节叙事/成文润色/审校把关）展示职责与落地引擎                 |
| 冲突裁决     | `/p/{name}/conflicts` | 多题材同名设定冲突逐条裁决，写回 `world.md`（底层 `merge-genres`）                 |
| 看板       | `/p/{name}/dashboard` | 成本 / 评测 / 模型路由 / MCP 汇总                                        |
| 文件浏览     | `/p/{name}/files`     | 浏览与查看项目全部产物                                                    |

**自主度滑块**（项目工作台「⚙️ 创作调校」）：0–100 连续调节，取代 CLI 的三档固定模式。左侧 `Director`（你掌控多）、中段 `Co-pilot`、右侧 `Auto Driver`（几乎全自动）。快捷按钮一键跳到 100 / 35。无论调到多高，`MAJOR_DECISION` 始终打断，作为安全底线。

详情页底部的「高级命令」折叠区会列出**当前状态机允许的全部命令**并可直接运行——这是 Web 端没有阉割 CLI 的证明，命令行能跑的这里都能跑。

### 6.7 页面与接口速查

**页面**

| 路径                                         | 用途                                                                               |
| ------------------------------------------ | -------------------------------------------------------------------------------- |
| `/`                                        | 工作台：项目列表 / 新建项目（多题材 chips 选择）                                                    |
| `/p/{name}`                                | 项目工作台：下一步 CTA + 创作旅程 + 自主度滑块 + 高级命令                                              |
| `/p/{name}/guide`                          | 引导向导（自动重定向到当前阶段）                                                                 |
| `/p/{name}/guide/{stage}`                  | 单阶段页（world / discussion / architecture / confirm / outline / characters / write） |
| `/p/{name}/write`                          | 实时写作间：单章 + 自动续写 + 成本                                                             |
| `/p/{name}/dashboard`                      | 看板                                                                               |
| `/p/{name}/files` 、 `/p/{name}/file?path=` | 文件浏览 / 单文件查看                                                                     |
| `/p/{name}/team`                           | Agent 阵容                                                                         |
| `/p/{name}/graph`                          | 世界关系图谱                                                                           |
| `/p/{name}/conflicts`                      | 冲突裁决                                                                             |

**常用接口**（前端 HTMX/fetch 消费，自动化脚本也可直接打）

| 方法             | 路径                                                                      | 作用                                            |
| -------------- | ----------------------------------------------------------------------- | --------------------------------------------- |
| `POST`         | `/api/projects`                                                         | 新建项目（非交互 `start`）                             |
| `POST`         | `/api/run`                                                              | 通用命令运行：`project` / `command` / `argv_json`    |
| `GET`          | `/api/runs/{id}/events`                                                 | SSE 事件流（`log` / `progress` / `done` / `ping`） |
| `GET`          | `/api/runs/{id}`                                                        | run 状态查询（SSE 失效时轮询兜底）                         |
| `GET`          | `/api/state/{name}`                                                     | 项目状态 JSON                                     |
| `POST`         | `/p/{name}/save-stage`                                                  | 阶段产物写回本地                                      |
| `GET` / `POST` | `/api/review/{name}`                                                    | 生成 / 读取复核检查单                                  |
| `POST`         | `/api/review/{name}/decision`                                           | 复核条目裁决（采纳 / 忽略）                               |
| `GET` / `POST` | `/api/qa/{name}`                                                        | 阶段问答模板 / 保存回答                                 |
| `POST`         | `/api/mode`                                                             | 设置自主度（0–100）                                  |
| `GET` / `POST` | `/api/relations/{name}`                                                 | 世界图谱读写（`/seed` 填充示例）                          |
| `GET` / `POST` | `/api/conflicts/{name}`（`/resolve`）                                     | 冲突列表 / 裁决执行                                   |
| `GET`          | `/api/chapters/{name}`、`/api/stages/{name}`、`/api/genres`、`/api/roster` | 章节 / 阶段状态 / 题材 / 阵容                           |

---

## 七、题材包（genre packs）

NovelAgent 内置多种题材的规则 / 套路 / 术语（修仙、武侠、都市、悬疑、科幻、重生、末世、玄学、男频爽文……），可注入写作流程：

```bash
novel-agent list-genres                 # 列出所有可用题材
novel-agent genre-info <题材名>          # 查看某题材说明
novel-agent load-genre -d <项目> <题材名>   # 把题材规则加载进项目
novel-agent inject-genre -d <项目> <题材名> # 注入题材套路到写作
novel-agent load-skill <skill名>         # 加载评估 / 写作 skill
```

### 多题材混搭（多选合并 + 冲突裁决）

一本小说可以**同时选择多个题材**（如修仙+武侠+科幻）：

```bash
# CLI 开新书多选
novel-agent start -d novels/my-novel --title "XXX" --genres xiuxian,wuxia

# 查看待裁决的冲突并逐条处理
novel-agent merge-genres -d novels/my-novel
```

题材包采用**渐进式披露**加载：列表只读元信息（中文名/简介），选中后才按需加载全量内容，控制成本。选中题材的世界观模板/术语/套路/质量规则会按段落自动合并；若多个题材定义了同名设定段落，生成**冲突卡**由你裁决，最终收敛为**本小说自己的设定**。

---

## 八、故障排查

| 现象                                                             | 原因                             | 处理                                                                                                                                       |
| -------------------------------------------------------------- | ------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `No module named 'llmagent'`                                    | 没安装旁边的内核包（agent 依赖 `../llmagent`） | 先 `pip install -e ./llmagent` 再 `pip install -e ./agent`；或运行前把 `llmagent/src` 加入 PYTHONPATH（见 §2.3） |
| `LLM_API_KEY 未配置` / 找不到 key                                    | `.env` 没放对或没加载                 | 确认 `agent/.env` 存在且含 `LLM_API_KEY`；或命令加 `--env`                                                                                          |
| `429` / `速率限制` / `rate limit`                                  | LLM 服务商 RPM 限流                 | `write` 会自动退避重试；频繁则调大 `LLM_TIMEOUT`、换额度更高模型，或改用 `compose` 一键驱动（含退避重试）                                                                    |
| `pre_validation_blocked`                                       | 世界观高严重度冲突                      | 按报告改 `world.md` / `subline` / 角色，或用 `adjust-*` 调整后再 `write`                                                                              |
| `ModuleNotFoundError: agent`                                   | 没装包 / PYTHONPATH 没含 src        | `pip install -e ./agent`，或运行前 `PYTHONPATH=.../agent/src`                                                                                 |
| `ModuleNotFoundError: No module named 'uvicorn'` / `'fastapi'` | 早期安装漏装 Web 依赖                  | 重跑 `pip install -e ./agent`（新版 `pyproject.toml` 已含 `fastapi`、`uvicorn`）；或单独 `pip install "fastapi>=0.110.0" "uvicorn[standard]>=0.29.0"` |
| `Form data requires "python-multipart" to be installed`        | Web UI 表单接口需要 python-multipart | 重跑 `pip install -e ./agent`；或单独 `pip install "python-multipart>=0.0.9"`                                                                  |
| 进度丢失 / 状态异常                                                    | 状态文件损坏                         | 先 `doctor` 诊断；必要时 `snapshot` 后 `rollback`，或 `reset-state`                                                                                |
| 想换模型但不生效                                                       | `.env` 未重载                     | 重启终端 / 重新运行命令（`.env` 每次命令启动读取）                                                                                                           |

---

## 九、命令速查表

> 命令名统一用连字符（文件名下划线转连字符）。`*` 表示全局命令（任意阶段可用）。  
> 所有命令均可在 Web 项目工作台「高级命令」面板直接运行（与 CLI 一致）；下表「Web 入口」列仅标注核心创作命令的专属页面，其余命令走高级命令面板即可。

### 核心创作

| 命令                     | 说明                       | 主要参数                                                                                            | Web 入口                  |
| ---------------------- | ------------------------ | ----------------------------------------------------------------------------------------------- | ----------------------- |
| `autowrite`            | 全自主写作（规划→写→评→修）          | `-d`, `--brief`, `--chapters`, `--mode`                                                         | 写作间「⚡ 自动续写」/ 工作台「✍️ 创作」 |
| `compose`              | 一键全自动写书（开新书/续写至完本，含完本去重） | `--name`, `-d`, `--scope`, `--genre`, `--story-core`, `-n/--chapters`, `--mode`, `--no-checkup` | 工作台「✍️ 创作」卡片 / 七步向导     |
| `start`                | 开新书，生成 world.md          | `-d`, `--genres`（多题材）                                                                           | 向导 ① 开新书                |
| `discuss`              | 脉络讨论，产出 discussion.md    | `-d`, `-r/--max-rounds`                                                                         | 向导 ② 脉络讨论               |
| `architecture`         | 故事架构                     | `-d`                                                                                            | 向导 ③ 故事架构               |
| `confirm-architecture` | 确认架构（解锁大纲）               | `-d`                                                                                            | 向导 ④ 架构确认               |
| `outline`              | 大纲 + 各支线 subline         | `-d`                                                                                            | 向导 ⑤ 创作大纲               |
| `design-characters`    | 角色 / 关系 / 伏笔 / 金手指       | `-d`                                                                                            | 向导 ⑥ 角色设计               |
| `write`                | 写下一章（核心循环）               | `-d`, `--mode auto/heavy/light`, `--no-strict-review`, `--json`, `--env`                        | 写作间 单章写入                |
| `adjust-relation`      | 调整角色关系网                  | `-d`, `-i/--intent`, `--json`                                                                   | 项目「高级命令」面板 / 关系图谱页      |
| `adjust-route`         | 调整主角成长路线                 | `-d`, `-i/--intent`, `--json`                                                                   | 项目「高级命令」面板              |
| `export`               | 导出 txt/markdown/epub     | `-d`, `-f/--format`, `-o/--output`, `-t/--title`                                                | 工作台导出入口                 |

### 成书质量

| 命令                | 说明                   |
| ----------------- | -------------------- |
| `evaluate`        | 「不崩」七维终审（默认真 LLM 实判） |
| `appeal`          | 「迷爱看」6 维评分           |
| `guardrail-scan`  | 成书质量护栏全量扫描           |
| `review-book`     | 成书多视角对抗式评审（编辑审稿视角）    |
| `rewrite`         | 反馈→定向改写              |
| `repair`          | 质量修复编排（配合 evaluate）   |
| `deslop`          | 批量去 AI 味（6 指标分级，`--apply` 改写） |
| `deslop-chapter`  | 单章去 AI 味（可选 light/medium/heavy） |
| `bookworm-review` | 书虫视角评审               |
| `track-pacing`    | 节奏追踪                 |

### 长篇一致性

| 命令 | 说明 |
| --- | --- |
| `mainline-init` | 分线预算计划（各支线章数上限，硬约束） |
| `mainline-show` | 查看分线预算与推进状态 |
| `continuity` | 连续性账本（事实/信息差/未闭环/章交接）+ 伏笔状态机 |
| `foreshadow-check` | 伏笔一致性检查 |
| `foreshadow-report` | 伏笔回收报表 |

### 世界构建 & 笔枢对标

| 命令                  | 说明                     |
| ------------------- | ---------------------- |
| `iceberg`           | 冰山建书 60+ 字段清单          |
| `graph`             | 可拖拽世界关系图谱              |
| `mode --autonomy N` | 连续自主度滑块（0–100）         |
| `merge-genres`      | 多题材冲突裁决                |

> Agent 阵容（25 位专家）与世界模拟哲学文案没有 CLI 命令——请在 Web 工作台查看：项目空间「Agent 阵容」页（`/p/{name}/team`）与首页。

### 查看 / 诊断（`*`）

| 命令                    | 说明                               |
| --------------------- | -------------------------------- |
| `status`*             | 查看状态 / 进度 / 可用命令                 |
| `mode`*               | 查看 / 切换介入模式（heavy/light/auto）    |
| `doctor`*             | 只读健康体检 + 修复建议                    |
| `dashboard`*          | 只读可视化 HTML / 本地服务                |
| `web`*                | 启动 Web UI（FastAPI 服务，浏览器访问，见第六章） |
| `cost`*               | LLMOps 看板：调用追踪 / 成本基线 / 评测回归汇总   |
| `context`*            | 查看上下文拼装                          |
| `version`*            | 版本                               |
| `commands`* / `help`* | 列出命令清单（加 `-d` 按当前状态过滤）           |

### 安全 / 维护（`*`）

| 命令                  | 说明                                              |
| ------------------- | ----------------------------------------------- |
| `snapshot`*         | 设定集快照                                           |
| `rollback`          | 回滚到第 N 章（归档不删）                                  |
| `reset-state`*      | 重置状态机                                           |
| `reindex`*          | 重建 RAG 索引                                       |
| `resume`*           | 异常恢复                                            |
| `rollback-setting`* | 回滚设定项                                           |
| `resize-scope`*     | 调整项目体量（short/medium/long/mega/custom）并按新体量重生成大纲 |

### 题材 / 质量 / 分析（`*`）

| 命令                           | 说明              |
| ---------------------------- | --------------- |
| `list-genres`*               | 列出可用题材          |
| `genre-info`*                | 题材说明            |
| `load-genre`                 | 加载题材规则到项目       |
| `inject-genre`               | 注入题材套路          |
| `load-skill`                 | 加载 skill        |
| `export-skill`*              | 导出 skill 为独立分发包 |
| `audit-chapter`              | 审计单章质量          |
| `audit-setting`              | 审计设定集           |
| `summarize-chapter`          | 章节摘要            |
| `summarize-range`            | 区间摘要            |
| `learn`                      | 学习 / 沉淀经验（技法提炼） |
| `show`                       | 章节预览（默认末章）      |
| `cost-plan`                  | 写前成本预估          |
| `payoff-plan`                | 爽点剧本生成          |
| `emotion-track`              | 情绪轨迹 ASCII 渲染   |
| `reader-feedback`            | 读者反馈数据回流        |
| `analyze`                    | 长篇拆文管线（6 阶段深度拆解） |
| `short-scan`                 | 短篇市场扫榜采样        |
| `short-analyze`              | 短篇拆文分析          |
| `setup`                      | 部署写作脚手架（CLAUDE.md / rules / agents） |
| `ecosystem`*                 | 生态看板（MCP/路由/工具） |
| `draft-status`               | 草稿状态            |
| `draft-discard`              | 丢弃草稿            |
| `import-draft`               | 导入外部草稿          |
| `frozen-fields` / `unfreeze` | 冻结 / 解冻字段       |
| `list-snapshots`*            | 列出快照            |
| `completion-extras`*         | 补全扩展            |

---

## 十、最小上手示例

```bash
# 1) 安装
pip install -e ./agent

# 2) 配置（新建 agent/.env，填入你的 LLM key，见第三章）

# 3) 方式 A：网页工作台（推荐新手，见第六章）
novel-agent web
# 浏览器打开 http://127.0.0.1:8000 → 新建项目 → 引导向导七步走 → 写作间一键续写

# 3) 方式 B：全自主写作（一条命令完成）
novel-agent autowrite -d novels/my-first-novel --brief "玄幻+废柴逆袭+爽文" --chapters 10

# 3) 方式 C：分步共创（手把手推进）
novel-agent start    -d novels/my-first-novel
novel-agent discuss  -d novels/my-first-novel
novel-agent outline  -d novels/my-first-novel
novel-agent design-characters -d novels/my-first-novel

# 4) 写前 10 章（手动循环，或改用 compose 一键跑）
novel-agent write -d novels/my-first-novel
# ……反复 write 直到满意……

# 5) 导出成书
novel-agent export -d novels/my-first-novel -f txt
```

更省事的全自动版本：`novel-agent compose --name "..." --story-core "..." --chapters 30`（一键写完一本，非定时，可直接换书复用）。

