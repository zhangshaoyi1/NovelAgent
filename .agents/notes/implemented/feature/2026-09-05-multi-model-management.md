# Agent Note: 多模型管理（Web UI 配置模型档案 + 按次指定写作模型）

Status: implemented

## Problem

模型配置此前只能通过本地 `.env`（`LLM_MODEL_ID` / `LLM_API_KEY` / `LLM_BASE_URL` 等单一组变量）：
- 想换模型要手改 `.env` 并重启，无界面操作入口；
- 无法保存多套模型端点，写作时不能按次选择模型；
- 顺带发现两个 Gateway 层缺陷：`chat_creative(model=...)` 传入的显式模型被
  `ComplexityRouter` 静默忽略；`_GatewayModelProvider.complete()` 硬编码
  `enable_thinking=None` / `timeout=120`，导致 `.env` 的 `LLM_ENABLE_THINKING`
  与 `LLM_TIMEOUT` 在 Gateway 路径上完全失效（`temperature` 同样丢失 hint 值）。

## Decision

1. **模型档案库** `agent/base/model_profiles.py`（base 层，纯标准库）：
   JSON 存储 `models.json`（agent 仓库根，与 .env 同级，已加 .gitignore）。
   档案字段：name / provider(openai|ollama) / base_url / api_key / model /
   enable_thinking / timeout / enabled / notes。
2. **解析优先级**（`base/config.py::_build_llm_config_from_env`）：
   `NOVEL_MODEL_PROFILE` 环境变量（按次指定）> 档案库激活档案 > 纯 env。
   档案未填字段逐项回退 env；无档案时行为与旧版完全一致（向后兼容）。
3. **按次指定写作模型**：Web 端 `/api/run` 新增 `profile` 表单字段 →
   `runner.execute` 向 CLI 子进程注入 `NOVEL_MODEL_PROFILE=<id>` →
   子进程 ConfigLoader 解析为对应档案。复用既有子进程架构，零侵入 CLI 命令。
4. **Gateway 显式路由**（`llmagent/gateway/router.py`）：`ComplexityRouter.decide()`
   优先命中 `req.extra["provider"]/["model"]`（strategy="explicit"），
   指定未知 provider 不静默改道；`PackedRequest` 新增 `temperature` /
   `enable_thinking` 透传（packer 从 hint/extra 填充），`gateway_adapter.complete()`
   改为：按次值 > Provider 配置值（`LLM_ENABLE_THINKING` / `LLM_TIMEOUT` 生效）。
5. **Web 管理页** `/models` + API：`GET/POST /api/models(/save)`、
   `/api/models/{id}/activate|delete|test`、`/api/models/import-env`（.env 一键迁移）。
   API Key 前端脱敏展示；编辑留空 = 保留原值；`/api/run` 与新建项目支持 `profile`。
   实时写作间两个入口（单章 / 自动续写）增加模型选择下拉。
6. **UI 美化**：`style.css` 升级为设计系统 v2（渐变强调色 / 毛玻璃顶栏 / 大圆角卡片 /
   层次阴影 / 焦点环 / 滚动条与动效），完整保留 v1 全部类名；`base.html`
   顶栏新增「模型管理」导航；新增 `models.html` 档案卡片网格页。

## Alternatives considered

- **runner 注入原始 LLM_* 环境变量**（而非 NOVEL_MODEL_PROFILE）：会把 API Key
  泄漏进子进程环境快照，且 load_dotenv(override=False) 与 shell env 的优先级
  纠缠，可观测性差；改为注入档案 id，子进程自行解析，密钥不经过 runner。
- **在 Gateway 注册全部档案为多 Provider**：需改动路由与故障转移语义（卡片成本
  全为 0 时 min/max 选择不稳定），且 CLI 子进程一次只用一套端点；单 Provider +
  档案解析更贴合现有架构。后续如需「创作/校验双模型分工」再扩展。
- **档案库放 SQLite / 每项目配置**：v1 场景（全局几套端点 + 按次覆盖）用单 JSON
  文件 + 原子写足够，无新增依赖。

## Consequences

- `models.json` 含 API Key，已加入 `.gitignore`（与 .env 同级敏感文件）。
- 显式模型 / temperature / enable_thinking 现在真正生效：依赖旧「被忽略」行为的
  调用方（理论不存在）会出现行为变化——这是缺陷修复而非回归。
- 存量 .env 用户零影响：无档案库时解析链路与旧版一致；Web 端提供一键导入迁移。
- 新测试：`tests/test_model_profiles.py`（档案 CRUD + 配置优先级 13 例）、
  `tests/test_gateway_model_routing.py`（显式路由 7 例）。
