# NovelAgent 使用指南（面向作者 / 使用者）

> 本指南介绍 **NovelAgent 这个写作软件本身怎么用**：如何安装、配置大模型、把一本小说从 0 写到完结、以及日常查看 / 修改 / 导出。
> 如果你要的是"用脚本自动批量写作（极品医仙那种一键驱动）"，那套能力已整合进 agent 仓库的 **`scripts/compose.py`（一键写书，见 novel-agent-driver 的 One-Shot Compose）**，本指南聚焦软件本身用法。

---

## 一、NovelAgent 是什么

NovelAgent 是一个**共创式长篇小说写作 Agent**：给它一段思路（题材 / 核心梗 / 风格 / 体量），它能自主完成「世界观 → 大纲 → 角色 → 逐章写作 → 成书评测」全流程，并以「不崩」七维终审量化保证质量。

它的核心能力：

- **全自主写作**：`autowrite` 一键输入思路，全自动完成（支持 `auto` 全自主 / `light` 关键节点介入 / `heavy` 每章控制）；
- **长篇一致性**：角色关系网、主角成长路线、伏笔表、金手指登记，随剧情演化但不矛盾；
- **剧集树（支线 / 卷）**：按 `outline.md` + 每条支线的 `subline.md` 推进；
- **质量门禁**：每章多重校验（爽点 / OOC / 连贯性 / 追读力 / AI 味 / 注水 / 黄金三章），不通过自动修订；
- **成书体检**：`evaluate` 跑「不崩」七维终审 + `appeal` 迷爱看 6 维评分，缺陷自动回溯修复；
- **G14 成书质量护栏**：写时 BLOCK 英文残留 / 占位标题 / 跨章重复，`guardrail-scan` 全量体检；
- **笔枢对标能力**：Agent 阵容叙事（`roster`）、冰山建书 60+ 字段（`iceberg`）、双模式连续滑块（`mode --autonomy`）、可拖拽世界关系图谱（`graph`）；
- **可逆操作**：快照、回滚、归档，写错了能后退。

命令行入口统一为 `novel-agent`（安装后），等价于 `python -m agent.cli`。所有命令都接受一个 `-d/--dir <项目目录>` 参数指向你的小说项目。

> **状态机**：NovelAgent 内部有状态流转（INIT → 配置/讨论 → 架构确认 → 大纲 → 角色 → 写作 → 完结）。每个命令都有"门禁"——**在错误的阶段运行会被拒绝并提示下一步该做什么**。所以照着下面的顺序走即可，不用担心顺序乱。

---

## 二、安装

### 2.1 环境要求
- **Python 3.11+**（本体运行建议 3.11+；自动化脚本依赖内置 `tomllib`，必须 3.11+）。
- 能联网装包（`pip`）。

### 2.2 安装 agent 包
NovelAgent 用 **src 布局**，从 NovelAgent 根目录安装（构建 wheel 会把 `src/agent` 包装进来）：

```bash
# 方式 A：正式安装（构建 wheel）
pip install ./agent

# 方式 B：editable 安装（改源码即时生效，推荐开发 / 调试期）
pip install -e ./agent
```

依赖见 `agent/pyproject.toml`（核心：typer、rich、pydantic、jinja2、openai、pyyaml、python-frontmatter、python-dotenv、**fastapi、uvicorn、python-multipart**——后三者用于 Web UI）。

> **Web UI 依赖**：启动网页界面（`novel-agent web`）需要 `fastapi` 与 `uvicorn`，二者已写入 `pyproject.toml` 的 `dependencies`。若你是早先按旧 README 安装、没装过这俩包，重跑一次安装命令即可（见下方 2.2）。

### 2.3 验证安装
```bash
novel-agent --help
# 或（不安装包，直接从根目录把 src 加入 PYTHONPATH）：
PYTHONPATH=D:/project/NovelAgent/agent/src python -m agent.cli --help
```
能打印出命令列表即安装成功。

---

## 三、配置大模型（`.env`）

NovelAgent 通过 `.env` 读取 LLM 配置。**把 `.env` 放在 agent 仓库根目录**（`agent/.env`，已 gitignore，不会入库）。加载顺序会依次尝试：当前工作目录及祖先目录的 `.env`、以及 `agent/.env`（仓库根）。所以"密钥随代码仓库一起放根目录"即可被自动读取。

### 3.1 全部环境变量

| 变量 | 含义 | 默认值 |
|---|---|---|
| `LLM_PROVIDER` | 提供商：`openai`（兼容 OpenAI 协议）或 `ollama`（本地） | `openai` |
| `LLM_API_KEY` | API 密钥（ollama 不需要） | 空 |
| `LLM_BASE_URL` | 服务地址。openai 默认 `https://api.openai.com/v1`；ollama 为 `http://localhost:11434` | 按 provider |
| `LLM_MODEL_ID` | **主模型**（创作：写章节 / 架构 / 角色），质量优先 | `glm-5.2` |
| `LLM_MODEL_UTILITY` | 轻量模型（校验 / 摘要 / 一致性，省成本），留空则等于主模型 | 空 |
| `LLM_TIMEOUT` | 单次请求超时（秒） | `120` |
| `LLM_MAX_RETRIES` | 失败重试次数 | `3` |
| `LLM_ENABLE_THINKING` | 思考开关：`true`/`false`/`空`（不干预模型默认）。批量写长篇建议 `false` 提速省 token | 空 |
| `LLM_EMBEDDING_MODEL` | 嵌入模型（RAG 用），留空回退主模型 | 空 |
| `LLM_EMBEDDING_BASE_URL` | 独立嵌入端点（可选） | 空 |
| `LLM_EMBEDDING_API_KEY` | 嵌入端点密钥（可选） | 空 |

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
> 换成 OpenAI / DeepSeek / 其它 OpenAI 兼容服务，只需改 `LLM_BASE_URL`、`LLM_MODEL_ID`、`LLM_API_KEY` 三项。

### 3.3 本地模型（ollama，零成本离线写作）
```dotenv
LLM_PROVIDER=ollama
LLM_BASE_URL=http://localhost:11434
LLM_MODEL_ID=qwen2.5:14b
# 无需 LLM_API_KEY
```

### 3.4 临时指定 .env
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
```

**反馈改写：**
```bash
novel-agent rewrite -d novels/my-novel --chapter 12 --feedback "节奏太快，放慢并补细节"
```

**一键完本（全自动写完整本，含 start→autowrite→去重）：**
```bash
novel-agent compose -d novels/my-novel --brief "..." --chapters 30
```

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
每一章的执行链条：
1. **7 步上下文加载**（world → subline → route → relations → characters → foreshadows → 题材规则）；
2. **LLM 生成**章节正文；
3. **质量校验**（9 项通用规则），不通过自动修订（≤2 次）；
4. 持久化 `chapters/ch<NNN>.md` 并更新进度指针。

- 加 `--json` 会以 JSON 输出结果（供外部脚本 / 自动化驱动调用，stdout 是 JSON，rich UI 走 stderr）。
- 可能触发 **前置冲突检测**（`pre_validation_blocked`）：当世界观出现高严重度冲突时，生成被暂停。此时按报告修改 `world.md` / `subline` / 角色档案，或用下面的 `adjust-*` 调整，再重跑 `write`。

> 想"一口气写完整本"而不手动反复敲命令？见 **`scripts/compose.py`（One-Shot Compose）**，一条命令即可循环调用 `write` 并自动处理限流、续写、导出。

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
| 命令 | 作用 |
|---|---|
| `reset-state` | 重置状态机（慎用，回到初始） |
| `reindex` | 重建 RAG 索引（长篇章节多了推荐定期跑） |
| `resume` | 从异常中恢复 |
| `context` | 查看当前上下文拼装 |
| `version` | 打印版本 |
| `help` | 查看帮助 |

---

## 六、题材包（genre packs）

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

## 七、故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| `LLM_API_KEY 未配置` / 找不到 key | `.env` 没放对或没加载 | 确认 `agent/.env` 存在且含 `LLM_API_KEY`；或命令加 `--env` |
| `429` / `速率限制` / `rate limit` | LLM 服务商 RPM 限流 | `write` 会自动退避重试；频繁则调大 `LLM_TIMEOUT`、换额度更高模型，或改用 `scripts/compose.py` 一键驱动（含退避重试） |
| `pre_validation_blocked` | 世界观高严重度冲突 | 按报告改 `world.md` / `subline` / 角色，或用 `adjust-*` 调整后再 `write` |
| `ModuleNotFoundError: agent` | 没装包 / PYTHONPATH 没含 src | `pip install -e ./agent`，或运行前 `PYTHONPATH=.../agent/src` |
| `ModuleNotFoundError: No module named 'uvicorn'` / `'fastapi'` | 早期安装漏装 Web 依赖 | 重跑 `pip install -e ./agent`（新版 `pyproject.toml` 已含 `fastapi`、`uvicorn`）；或单独 `pip install "fastapi>=0.110.0" "uvicorn[standard]>=0.29.0"` |
| `Form data requires "python-multipart" to be installed` | Web UI 表单接口需要 python-multipart | 重跑 `pip install -e ./agent`；或单独 `pip install "python-multipart>=0.0.9"` |
| 进度丢失 / 状态异常 | 状态文件损坏 | 先 `doctor` 诊断；必要时 `snapshot` 后 `rollback`，或 `reset-state` |
| 想换模型但不生效 | `.env` 未重载 | 重启终端 / 重新运行命令（`.env` 每次命令启动读取） |

---

## 八、命令速查表

> 命令名统一用连字符（文件名下划线转连字符）。`*` 表示全局命令（任意阶段可用）。

### 核心创作
| 命令 | 说明 | 主要参数 |
|---|---|---|
| `autowrite` | 全自主写作（规划→写→评→修） | `-d`, `--brief`, `--chapters`, `--mode` |
| `compose` | 一键全自动写书（start→autowrite 至完本） | `-d`, `--brief`, `--chapters` |
| `start` | 开新书，生成 world.md | `-d`, `--genres`（多题材） |
| `discuss` | 脉络讨论，产出 discussion.md | `-d`, `-r/--max-rounds` |
| `architecture` | 故事架构 | `-d` |
| `confirm-architecture` | 确认架构（解锁大纲） | `-d` |
| `outline` | 大纲 + 各支线 subline | `-d` |
| `design-characters` | 角色 / 关系 / 伏笔 / 金手指 | `-d` |
| `write` | 写下一章（核心循环） | `-d`, `--no-strict-review`, `--json`, `--env` |
| `adjust-relation` | 调整角色关系网 | `-d`, `-i/--intent`, `--json` |
| `adjust-route` | 调整主角成长路线 | `-d`, `-i/--intent`, `--json` |
| `export` | 导出 txt/markdown/epub | `-d`, `-f/--format`, `-o/--output`, `-t/--title` |

### 成书质量
| 命令 | 说明 |
|---|---|
| `evaluate` | 「不崩」七维终审（默认真 LLM 实判） |
| `appeal` | 「迷爱看」6 维评分 |
| `guardrail-scan` | 成书质量护栏全量扫描 |
| `rewrite` | 反馈→定向改写 |
| `bookworm-review` | 书虫视角评审 |
| `track-pacing` | 节奏追踪 |

### 世界构建 & 笔枢对标
| 命令 | 说明 |
|---|---|
| `roster` | Agent 阵容叙事（25 位专家分组展示） |
| `iceberg` | 冰山建书 60+ 字段清单 |
| `philosophy` | 世界模拟哲学文案 |
| `graph` | 可拖拽世界关系图谱 |
| `mode --autonomy N` | 连续自主度滑块（0–100） |
| `merge-genres` | 多题材冲突裁决 |

### 查看 / 诊断（`*`）
| 命令 | 说明 |
|---|---|
| `status`* | 查看状态 / 进度 / 可用命令 |
| `mode`* | 查看 / 切换介入模式（heavy/light/auto） |
| `doctor`* | 只读健康体检 + 修复建议 |
| `dashboard`* | 只读可视化 HTML / 本地服务 |
| `web`* | 启动 Web UI（FastAPI 服务，浏览器访问） |
| `context`* | 查看上下文拼装 |
| `version`* | 版本 |
| `help`* | 帮助 |

### 安全 / 维护（`*`）
| 命令 | 说明 |
|---|---|
| `snapshot`* | 设定集快照 |
| `rollback` | 回滚到第 N 章（归档不删） |
| `reset-state`* | 重置状态机 |
| `reindex`* | 重建 RAG 索引 |
| `resume`* | 异常恢复 |
| `rollback-setting` | 回滚设定项 |

### 5.7 网页界面（Web UI）

NovelAgent 自带一个**零构建的 Web UI**（FastAPI 服务端渲染 + Jinja2 + HTMX 局部刷新，无需 Node 工具链），把 CLI 的创作闭环做成可视化工作台。依赖 `fastapi` + `uvicorn`（已随包安装）。

**启动：**

```bash
# 方式一：通过 CLI 命令（推荐）
novel-agent web                                  # 默认 http://127.0.0.1:8000
novel-agent web --host 0.0.0.0 --port 8080       # 指定监听地址 / 端口

# 方式二：直接跑模块（等价于 CLI 的 web 命令）
python -m agent.web                              # 需先把 src 加入 PYTHONPATH
PYTHONPATH=D:/project/NovelAgent/agent/src python -m agent.web --port 8080
```

启动后浏览器访问 `http://<host>:<port>` 即可。页面包含：

- **引导向导** `/`：项目列表 / 新建项目（含多题材 chips 选择 + 一键写书入口）；
- **项目空间** `/p/{name}`：状态机进度 + 当前可用操作（按阶段门禁）+ **自主度连续滑块**；
- **引导向导** `/p/{name}/guide`：按状态机阶段走通创作闭环；
- **实时写作间** `/p/{name}/write`：写章 SSE 实时进度 + 成本视图；
- **看板** `/p/{name}/dashboard`：成本 / 评测 / 模型路由 / MCP；
- **文件浏览** `/p/{name}/files` 与单文件查看 `/p/{name}/file?path=`；
- **冲突裁决** `/p/{name}/conflicts`：多题材同名设定冲突逐条裁决；
- **Agent 阵容** `/p/{name}/roster`：25 位专家 Agent 分组展示；
- **世界关系图谱** `/p/{name}/graph`：力导向图可拖拽编排、点选编辑。

> Web 端与 CLI 共享同一套命令元数据（`available_commands` 一致），所以在网页上能跑的操作和命令行完全对齐。停止服务用 `Ctrl-C`。

### 题材 / 质量 / 分析（`*`）
| 命令 | 说明 |
|---|---|
| `list-genres`* | 列出可用题材 |
| `genre-info`* | 题材说明 |
| `load-genre` | 加载题材规则到项目 |
| `inject-genre` | 注入题材套路 |
| `load-skill` | 加载 skill |
| `export-skill`* | 导出 skill 为独立分发包 |
| `audit-chapter` | 审计单章质量 |
| `audit-setting` | 审计设定集 |
| `foreshadow-check` | 伏笔一致性检查 |
| `foreshadow-report` | 伏笔报表 |
| `summarize-chapter` | 章节摘要 |
| `summarize-range` | 区间摘要 |
| `learn` | 学习 / 沉淀经验 |
| `show` | 章节预览（默认末章） |
| `cost-plan` | 写前成本预估 |
| `payoff-plan` | 爽点剧本生成 |
| `emotion-track` | 情绪轨迹 ASCII 渲染 |
| `reader-feedback` | 读者反馈数据回流 |
| `ecosystem`* | 生态看板（MCP/路由/工具） |
| `draft-status` | 草稿状态 |
| `draft-discard` | 丢弃草稿 |
| `import-draft` | 导入外部草稿 |
| `frozen-fields` / `unfreeze` | 冻结 / 解冻字段 |
| `list-snapshots`* | 列出快照 |
| `completion-extras`* | 补全扩展 |

---

## 九、最小上手示例

```bash
# 1) 安装
pip install -e ./agent

# 2) 配置（新建 agent/.env，填入你的 LLM key，见第三章）

# 3) 方式 A：全自主写作（推荐，一条命令完成）
novel-agent autowrite -d novels/my-first-novel --brief "玄幻+废柴逆袭+爽文" --chapters 10

# 3) 方式 B：分步共创（手把手推进）
novel-agent start    -d novels/my-first-novel
novel-agent discuss  -d novels/my-first-novel
novel-agent outline  -d novels/my-first-novel
novel-agent design-characters -d novels/my-first-novel

# 4) 写前 10 章（手动循环，或改用 scripts/compose.py 一键跑）
novel-agent write -d novels/my-first-novel
# ……反复 write 直到满意……

# 5) 导出成书
novel-agent export -d novels/my-first-novel -f txt
```

更省事的全自动版本，见 **`scripts/compose.py`**（一键写完一本，非定时，可直接换书复用）。
