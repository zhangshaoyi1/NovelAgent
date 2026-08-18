# novel-agent

共创式小说写作 Agent —— 设定集驱动的长篇一致性 + 剧集树 + 关系演化。

本目录是 NovelAgent 的**代码仓库**（与 `项目文档/`、`小说/` 两个仓库并列，各自独立 git）。

## 布局（src layout）

```
agent/                      # 代码仓库根（本目录）
├── pyproject.toml          # 项目配置（setuptools，src 布局）
├── README.md
├── .env                    # LLM API Key 等密钥（已 gitignore，不入库）
├── .gitignore
├── src/agent/              # Python 包（cli/ core/ workflows/ skills/ templates/ state_schema/ ...）
├── drivers/                # 写作驱动脚本（_write_driver*.py）
├── scripts/                # 辅助脚本（smoke test / 修复工具）
└── tests/                  # pytest 测试套件
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

# 通用写作驱动（drivers/）会自动把 src 加入 PYTHONPATH，从 agent 仓库根运行即可：
# 默认读同目录 driver_config.toml（已填极品医仙值）；换书改配置后用 --config 指定
python drivers/generic_writer.py
python drivers/generic_writer.py --config drivers/driver_config.<书名>.toml
```

## 配置

LLM 密钥放在本目录的 `.env`（已 gitignore，不入库）。`src/agent/core/llm_client.py` 会
依次从「cwd 及祖先目录的 `.env`」与「包目录 / src 目录 / 仓库根 `agent/.env`」加载，因此
`.env` 随代码仓库一起放置（位于仓库根、不进包、不随 wheel 发布）即可被自动读取。

## 目录

- `src/agent/cli/`        CLI 入口（typer）
- `src/agent/core/`       核心服务层（状态机 / LLM / 一致性 / 质量 / 冲突仲裁 …）
- `src/agent/workflows/`  各功能工作流（m<N>_<name>.py）
- `src/agent/skills/`     题材包 / 评估 skill 插件层
- `src/agent/templates/`  Jinja2 文件模板
- `src/agent/state_schema/` 状态文件 JSON schema
- `drivers/`              写作驱动脚本（_write_driver*.py）
- `scripts/`              辅助脚本（smoke test / 修复工具）
- `tests/`                pytest 测试套件

完整项目文档见并列仓库 `../项目文档/`。
