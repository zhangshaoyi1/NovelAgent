# 脚本用法文档（drivers/ 与 scripts/）

> 配套问题归档见 `docs/issues/`。本文档说明本仓库 `drivers/` 与 `scripts/` 下 4 个脚本的**用途、使用方法、可复用性、后续如何复用**。
> 通用前置：本仓库为 **src 布局**，运行任何脚本前确保 `agent` 包可被导入（二选一）：
> - editable 安装：`D:/env/python/python.exe -m pip install -e . --no-deps`（在 `agent/` 仓库根执行）
> - 或直接注入 `PYTHONPATH`：`PYTHONPATH=D:/project/NovelAgent/agent/src`（驱动脚本已自动注入，无需手动）

---

## 1. `drivers/_write_driver_jipin.py` —— 极品医仙真实写作驱动（核心，已跑完整书）

**用途**
循环调用真实 `agent.cli write --json` 把《极品医仙归来：前女友跪求复合》写到完结；自带限流退避、草稿清理、断点续写、支线推进、完结后自动导出 TXT + 生成 Dashboard。这是生成极品医仙（最终 51 章 / 176,078 字）**实际使用的驱动**。

**用法**
```bash
cd D:/project/NovelAgent
D:/env/python/python.exe agent/drivers/_write_driver_jipin.py
# 或带 PYTHONPATH：
PYTHONPATH=D:/project/NovelAgent/agent/src D:/env/python/python.exe D:/project/NovelAgent/agent/drivers/_write_driver_jipin.py
```

**关键常量（文件顶部，按需改）**
| 常量 | 含义 | 当前值 |
|---|---|---|
| `PROJECT` | 小说项目相对路径（相对 `D:/project/NovelAgent`） | `小说/projects/jipin-yixian` |
| `STATE` | 状态机文件绝对路径 | `.../jipin-yixian/state.json` |
| `LOG` | 断点 / 运行日志 JSON | `小说/_driver_log_jipin.json` |
| `PY` | Python 解释器 | `D:/env/python/python.exe` |
| `AGENT_SRC` | 自动推导的 `src` 目录，注入 `PYTHONPATH` | `drivers/../src` |
| `HARD_CAP_CHARS` | 安全硬上限（兜底，正常不会触碰） | `450_000` |
| `RUN_TARGET_CHARS` | 单批增量上限（抗沙箱超时，达到即退出续写） | `50_000` |
| `PER_ARC_LIST` | 每条支线规划章节数 `[S01..S05]` | `[25,17,2,2,3]` |
| `COOLDOWN` | 章节间冷却秒数（避让智谱 RPM） | `45` |
| `ARCS` | 5 条支线名称列表 | `S01_情感清算…S05_终极清算` |

**可复用性：★★★☆☆（专用但结构可借鉴）**
- 强依赖极品医仙的特定支线规划（`PER_ARC_LIST` / `ARCS` / 项目路径），**不能直接套到其他小说**。
- 但其「限流退避 + 草稿清理 + 断点续写 + 支线推进 + 完结导出」的**编排框架高度可复用**：换一本小说只需改 `PROJECT` / `STATE` / `ARCS` / `PER_ARC_LIST` 四个常量即可。
- 建议把它**抽象为一个通用 `NovelDriver` 类 / CLI 参数**，把硬编码常量改为命令行参数（见下方「后续如何使用」）。

**后续如何使用**
1. 换小说：复制本文件，改 `PROJECT` / `STATE` / `ARCS` / `PER_ARC_LIST` / `RUN_TARGET_CHARS`。
2. 续写：直接重跑，脚本读 `LOG` 自动从 `arc_index` / `total_chars` 断点继续（**不要手删 `_driver_log_jipin.json`**，否则从 0 开始）。
3. 调速度：账户配额高就调小 `COOLDOWN`、调大 `RUN_TARGET_CHARS`；配额紧则反向。
4. 观察进度：看 `_driver_log_jipin.json` 的 `chapters` / `total_chars` / `arc_index`，或跑 `dashboard`。

**注意事项 / 坑**
- 必须在 `D:/project/NovelAgent` 为 cwd 运行（脚本里 `cwd="D:/project/NovelAgent"` 且 `PROJECT` 是相对路径）。
- 依赖 `agent.cli` 的 `write` / `adjust-relation` / `adjust-route` / `export` / `dashboard` 子命令与 `--json` 输出格式；若 CLI 改了输出结构需同步改本脚本的解析。
- safe-delete 在沙箱会失败关闭（见 issue-02），本脚本用 `clean_stale_draft()` 绕过；若内核修了该问题，可移除该函数。

---

## 2. `drivers/_write_driver.py` —— 深井回廊（deep-well）写作驱动（模板 / 未跑完整书）

**用途**
与 `_write_driver_jipin.py` 同源的**早期通用模板**，用于《深井回廊》项目：`PROJECT="小说/projects/deep-well"`、`TARGET=50000`、`MAX_CHAPTERS=25`、`ADJUST_EVERY=3`。逻辑更简洁（无断点续写 / 无支线推进 / 无完结导出），每 3 章做一次 `adjust-relation` / `adjust-route`。

**用法**
```bash
cd D:/project/NovelAgent
D:/env/python/python.exe agent/drivers/_write_driver.py
```

**关键常量**
| 常量 | 当前值 |
|---|---|
| `PROJ` | `小说/projects/deep-well` |
| `TARGET` | `50000`（字数目标，达到即停） |
| `MAX_CHAPTERS` | `25` |
| `ADJUST_EVERY` | `3`（每 3 章调一次关系/路线） |
| `LOG` | `小说/_driver_log.json` |

**可复用性：★★★★☆（最干净的模板）**
- 比 jipin 版简单，**最适合作为「新小说驱动模板」** 起点：去掉了支线/断点复杂度，保留了「write + 周期性 adjust + 重试 + 日志」核心循环。
- 没有断点续写，长任务需注意沙箱超时（见 issue-03）；若要写长篇，建议直接基于 jipin 版改造。

**后续如何使用**
- 作为模板：新建 `_write_driver_<书名>.py`，改 `PROJ` / `TARGET` / `MAX_CHAPTERS`。
- 若要断点续写 / 支线推进，从 `_write_driver_jipin.py` 移植对应函数。

---

## 3. `scripts/_fix_subline_ranges.py` —— 支线章节范围一次性修复

**用途**
把极品医仙 5 条支线的「剧集压力曲线」表（`sublines/*/subline.md`）与 `outline.md` 的「章节分配」列，按 `MAPPING`（每条支线连续 25 章：1/26/51/76/101）重写对齐。本质是**修复大纲与驱动计划错位**的应急脚本（见 issue-05）。

**用法**
```bash
cd D:/project/NovelAgent/小说
PYTHONPATH=D:/project/NovelAgent/agent/src D:/env/python/python.exe D:/project/NovelAgent/agent/scripts/_fix_subline_ranges.py
```

**关键常量**
| 常量 | 含义 |
|---|---|
| `BASE` | 项目相对路径 `projects/jipin-yixian` |
| `MAPPING` | 每条支线起始章（连续 25 章映射） |
| `alloc` | `outline.md` 各支线「章节分配」列文案 |

**可复用性：★☆☆☆☆（高度专用，几乎不可复用）**
- 硬编码了极品医仙的 5 支线、125 章旧规划与特定 `MAPPING`，且仅匹配 `outline.md` 的固定表格格式（`| S0X | ... |`）。
- 换小说 / 换大纲格式即失效。**不应作为通用工具**，仅作「本次特定修复的历史存档」。
- 根治方案见 issue-05 改进建议：让支线范围由状态机自动生成，不再需要此类手写修复。

**后续如何使用**
- 一般**不需要再跑**。仅在「大纲章节分配与实际成书严重错位、且决定手写修复」时借鉴其思路（读 subline.md 指定段重写 + 正则替换 outline.md）。
- 跑前务必备份 `subline.md` / `outline.md`（脚本是就地覆盖写）。

---

## 4. `scripts/_smoke_test_jipin.py` —— LLM 连通性冒烟测试

**用途**
验证 `agent` 包能正确读取 `.env` 并真实调用 LLM（glm-4.7）拿到回复。生成前必跑，用于确认「配置 / 网络 / key」三件套正常。生成极品医仙前用它确认了 `SMOKE_OK`。

**用法**
```bash
cd D:/project/NovelAgent
PYTHONPATH=D:/project/NovelAgent/agent/src D:/env/python/python.exe D:/project/NovelAgent/agent/scripts/_smoke_test_jipin.py
```
预期输出含：`provider: openai` / `model: glm-4.7` / `base_url: https://open.bigmodel.cn/api/paas/v4/` / `SMOKE_OK`（失败时 `SMOKE_ERR`）。

**关键内容**
- `from agent.core.llm_client import LLMClient, LLMConfig`
- `LLMClient._load_from_env()` 读取配置；`client.chat(...)` 发一句话请求；按回复长度判定 `SMOKE_OK` / `SMOKE_EMPTY_CONTENT` / `SMOKE_ERR`。

**可复用性：★★★★★（通用，强烈推荐保留）**
- 与具体小说无关，纯验证 LLM 链路。换模型 / 换 key 后跑一遍即可确认可用。
- 建议改名 `scripts/smoke_test_llm.py` 并参数化（模型 / prompt 可传参），作为仓库标准「环境自检」脚本。

**后续如何使用**
- 任何「改了 `.env` / 换了 LLM 提供商 / 重装环境」之后，先跑它确认链路通。
- 可纳入 CI / 启动前自检。

---

## 5. 总览：这些脚本是否可复用、你后续怎么用

| 脚本 | 角色 | 可复用性 | 后续建议 |
|---|---|---|---|
| `_write_driver_jipin.py` | 极品医仙正式驱动 | 框架可复用，参数需改 | 抽象为通用 `NovelDriver` CLI（参数化 PROJECT/ARCS/COOLDOWN） |
| `_write_driver.py` | deep-well 模板 | 适合当新小说模板 | 写长篇时从 jipin 版移植断点/支线逻辑 |
| `_fix_subline_ranges.py` | 一次性修复 | 不可复用（专用） | 仅存档；根治靠状态机自动生成范围 |
| `_smoke_test_jipin.py` | LLM 冒烟测试 | 通用 | 改名 `smoke_test_llm.py`，参数化后作为标准自检 |

**给后续使用者的三条建议**
1. **写新书**：复制 `_write_driver_jipin.py` → 改 4 个常量 → 先跑 `_smoke_test_jipin.py` 确认 LLM 通 → 分批续写。
2. **永远先冒烟**：动环境前跑 LLM 冒烟测试，30 秒排除「key / 网络 / 配置」问题。
3. **别手改大纲范围**：支线章节范围让状态机生成；若已错位且要手写修，先用 `_fix_subline_ranges.py` 的思路但务必备份。

> 所有脚本均依赖 `agent` 包的 src 布局与 `.env` 配置；路径在 Windows + Git Bash 下请用 `D:/...` 绝对路径（见 issue-09）。
