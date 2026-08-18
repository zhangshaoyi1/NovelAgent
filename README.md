# novel-agent

共创式小说写作 Agent —— 设定集驱动的长篇一致性 + 剧集树 + 关系演化。

本目录是 NovelAgent 的**代码仓库（软件本体）**。与 `项目文档/`、`小说/`、`写作自动化/`
三个目录并列，各自独立 git：

- `agent/`            —— 本仓库：NovelAgent 软件本体（CLI + 核心 + 工作流 + 题材包）
- `项目文档/`          —— 中文需求 / 架构 / 评审文档（独立仓库）
- `小说/`             —— 生成物（各小说的 projects、成书、运行日志，独立仓库）
- `写作自动化/`        —— **自动批量写作驱动与脚本文档**（独立仓库，不属于本软件本体）
                       详见该仓库 README；本仓库不含任何"一键跑书"的驱动脚本。

## 布局（src layout）

```
agent/                      # 代码仓库根（本目录）
├── pyproject.toml          # 项目配置（setuptools，src 布局）
├── README.md
├── .env                    # LLM API Key 等密钥（已 gitignore，不入库）
├── .gitignore
├── src/agent/              # Python 包（cli/ core/ workflows/ skills/ templates/ state_schema/ ...）
├── tests/                  # pytest 测试套件
└── docs/                   # 本软件的使用文档（使用指南.md）
```

## 安装

```bash
# 从 NovelAgent 根目录安装（构建 wheel，装入 src/agent 包）：
pip install ./agent
# 或 editable 安装（开发期改代码即时生效）：
pip install -e ./agent
```

依赖见 `pyproject.toml`（typer / rich / pydantic / jinja2 / openai / pyyaml / python-frontmatter / python-dotenv）。

## 运行

```bash
# 方式一：安装后直接用入口命令
novel-agent --help

# 方式二：不安装，直接从 NovelAgent 根目录把 src 加入 PYTHONPATH
PYTHONPATH=D:/project/NovelAgent/agent/src python -m agent.cli --help
```

> 自动批量写作（一键跑完整本书）**不在本仓库**，见并列的 `写作自动化/` 仓库。

## 配置

LLM 密钥放在本目录的 `.env`（已 gitignore，不入库）。`src/agent/core/llm_client.py` 会
依次从「cwd 及祖先目录的 `.env`」与「包目录 / src 目录 / 仓库根 `agent/.env`」加载，因此
`.env` 随代码仓库一起放置（位于仓库根、不进包、不随 wheel 发布）即可被自动读取。

## 文档

- `docs/使用指南.md` —— **NovelAgent 软件本身的详细使用指南**（安装 / 配置 / 写作流程 / 命令速查）。
- 并列仓库 `../项目文档/` —— 需求、架构、评审等中文文档。
- 并列仓库 `../写作自动化/` —— 自动批量写作驱动（generic_writer 等）及其文档。

## 目录

- `src/agent/cli/`          CLI 入口（typer）
- `src/agent/core/`         核心服务层（状态机 / LLM / 一致性 / 质量 / 冲突仲裁 …）
- `src/agent/workflows/`    各功能工作流（m<N>_<name>.py）
- `src/agent/skills/`       题材包 / 评估 skill 插件层
- `src/agent/templates/`    Jinja2 文件模板
- `src/agent/state_schema/` 状态文件 JSON schema
- `tests/`                  pytest 测试套件
- `docs/`                   本软件使用文档
