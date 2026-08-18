# 问题 #08：全局安装找不到项目 `.env`（LLM API Key 缺失）

| 项 | 内容 |
|---|---|
| 分类 | 工程 / 配置 |
| 严重度 | 中（导致真实 LLM 验证 / 实际生成失败，报 `LLM_API_KEY 未配置`） |
| 状态 | 已解决（editable 安装 + 驱动 `PYTHONPATH` 注入 + `.env` 发现补仓库根候选） |
| 关联文件 | `src/agent/core/llm_client.py`（`_load_from_env`）、`drivers/_write_driver*.py`（`AGENT_SRC` / `PYTHONPATH` 注入）、`agent/.env` |
| 证据 | 全局（site-packages）安装跑 smoke test 报 `LLM_API_KEY 未配置`；editable 安装 / `PYTHONPATH=src` 后通过 |

## 1. 问题描述
`agent` 的 LLM 配置从 `.env` 读取（`LLM_API_KEY` 等）。当以**全局安装**（装进 `site-packages`）方式运行时，`.env` 发现逻辑只在「包目录 / site-packages 目录」找 `.env`，**找不到仓库根的 `agent/.env`**，于是报 `LLM_API_KEY 未配置`，真实 LLM 调用失败。

## 2. 现象 / 证据
- 全局安装 + 中性 cwd（`/tmp`）跑 `scripts/_smoke_test_jipin.py` → `LLM_API_KEY 未配置`。
- 改用 **editable 安装**（链接源码，`agent/.env` 在源码树内）或驱动脚本把 `PYTHONPATH` 指向 `src` 后，`.env` 被发现，`SMOKE_OK`。

## 3. 根因
src 布局下，Python 包物理位于 `agent/src/agent/`，而 `.env` 按约定放在**仓库根** `agent/.env`（不进包、不随 wheel 发布）。原 `.env` 发现只遍历「cwd 及祖先」与「包目录」，没覆盖「仓库根（src 的上级的上级）」，导致全局安装时漏掉项目 `.env`。

## 4. 解决方案（当前）
两处配合：
1. `src/agent/core/llm_client.py` 的 `_load_from_env` 增加**仓库根候选**：从包目录 `__file__` 回溯 `src/agent` → `src` → 仓库根，逐级探测 `.env` 并 `load_dotenv()`。
2. 驱动脚本 `_write_driver*.py` 注入 `PYTHONPATH = AGENT_SRC + ...`，从源码导入，使 `.env` 落在项目树内被命中。

## 5. 影响
- 真实 LLM 验证（glm-4.7）与极品医仙实际生成依赖此修复；否则任何「非仓库根 cwd + 全局安装」的组合都会因找不到 key 而失败。

## 6. 改进建议
- **支持显式指定**：增加环境变量 `NOVEL_AGENT_ENV=<path>` 直指 `.env`，绕过探测。
- **文档固化**：在 `README.md` 明确「`.env` 必须随代码仓库放在仓库根、不进 wheel」以及两种运行方式（editable / `PYTHONPATH=src`）。
- 探测逻辑已覆盖 cwd 祖先 + 包目录 + 仓库根三层，足够健壮。

## 7. 复现 / 验证
```bash
# 1) 中性 cwd + 全局安装（应失败）
cd /tmp && D:/env/python/python.exe -c "from agent.core.llm_client import LLMClient; print(LLMClient._load_from_env().api_key)"
# 2) 注入 PYTHONPATH 从源码导入（应通过）
cd D:/project/NovelAgent
PYTHONPATH=D:/project/NovelAgent/agent/src D:/env/python/python.exe D:/project/NovelAgent/agent/scripts/_smoke_test_jipin.py
```
