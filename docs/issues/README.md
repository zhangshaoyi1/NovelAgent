# 问题归档索引（极品医仙生成 + 项目重构全过程）

> 本目录归档「生成《极品医仙》+ 项目重构」过程中**遇到的所有问题**，按「一个问题一个文档」组织。
> 每篇文档结构统一：问题描述 / 现象证据 / 根因 / 解决方案 / 影响 / 改进建议 / 复现验证。
> 脚本用法见同级 `../SCRIPTS_USAGE.md`。

## 一、问题清单（13 个）

### A. 小说生成类（5）
| # | 文档 | 一句话 |
|---|---|---|
| 01 | [issue-01-glm-rate-limit.md](./issue-01-glm-rate-limit.md) | 智谱 GLM 限流（429/RPM）→ 章节写入频繁失败，靠冷却 + 指数退避重试 |
| 02 | [issue-02-stale-draft-safe-delete.md](./issue-02-stale-draft-safe-delete.md) | 失败残留草稿 + safe-delete 在沙箱「回收站不可用」失败关闭，卡死续写（54 次失败） |
| 03 | [issue-03-sandbox-timeout-batch-resume.md](./issue-03-sandbox-timeout-batch-resume.md) | 沙箱长任务超时 → 分批续写 + JSON 日志断点恢复 |
| 04 | [issue-04-prev-validation-world-conflict.md](./issue-04-prev-validation-world-conflict.md) | pre_validation 拦截世界观冲突（仙尊 vs 渡劫、终局支线越界），硬错误需改大纲 |
| 05 | [issue-05-subline-range-misalign.md](./issue-05-subline-range-misalign.md) | 支线章节范围在 subline.md / outline.md / 驱动常量三处不一致，需手写修复 |

### B. 工程 / 结构类（8）
| # | 文档 | 一句话 |
|---|---|---|
| 06 | [issue-06-package-empty-wheel.md](./issue-06-package-empty-wheel.md) | `package-dir` hack 生成空 wheel / editable 不暴露 `agent` → 改 src 布局 |
| 07 | [issue-07-editable-install-no-isolation.md](./issue-07-editable-install-no-isolation.md) | editable 安装 `--no-build-isolation` 缺 `wheel` 失败 → 改用隔离构建 |
| 08 | [issue-08-dotenv-discovery.md](./issue-08-dotenv-discovery.md) | 全局安装找不到项目 `.env`（LLM key 缺失）→ 补仓库根候选 + PYTHONPATH 注入 |
| 09 | [issue-09-git-bash-path-conversion.md](./issue-09-git-bash-path-conversion.md) | Git Bash `/d/...`→`C:\d\...` 路径转换坑，git/python 要用 `D:/...` |
| 10 | [issue-10-git-push-hang-gcm.md](./issue-10-git-push-hang-gcm.md) | `git push` 在沙箱被 GCM 交互授权挂死 → `GIT_TERMINAL_PROMPT=0` |
| 11 | [issue-11-three-repo-split.md](./issue-11-three-repo-split.md) | 三目录应为并列三仓库而非单一嵌套 git |
| 12 | [issue-12-stale-doc-links.md](./issue-12-stale-doc-links.md) | 中文文档失效链接（docs/ACDEF…md 等）+ 结构树过时 |
| 13 | [issue-13-project-path-drift.md](./issue-13-project-path-drift.md) | 项目位置漂移（agent/projects → NovelAgent/小说/projects）放大 safe-delete 失败 |

## 二、改进建议总览（「看看有什么可以改进的」）

按「是否值得内核改动」分为两类：

### 值得 NovelAgent 内核改的（根治根因，一劳永逸）
1. **safe-delete 提供硬删除开关 / 失败降级**（issue-02 根因）
   非交互环境（沙箱 / CI / Docker / 服务器）回收站不可用，当前 fail-closed 会卡死续写。新增 `NOVEL_AGENT_HARD_DELETE=1` 或配置项，直接 `os.unlink`；或更稳妥：回收站不可用时自动降级为直接删 + 告警，而非拒绝删除。
2. **`write` 内置限流退避 + 抖动**（issue-01）
   把驱动层的 429/超时指数退避下沉到 `write` 工作流，让单次 `write` 自身鲁棒，外部驱动只需简单循环。
3. **内核原生续写参数**（issue-03）
   `agent.cli write` 增加 `--resume` / `--max-chars` / `--max-chapters`，断点由 `state.json` 承载，减少对外挂驱动的依赖；并可提供常驻 / 守护模式应对沙箱超时。
4. **冲突可一键仲裁 / 豁免**（issue-04）
   提供 `adjust-conflict` 或 `write --force`，把「接受新设定并回填设定集」做成显式动作并记录到状态机，避免每次手工改大纲。
5. **支线范围单一来源（SSOT）**（issue-05）
   支线章节范围由状态机自动生成到 `subline.md` / `outline.md`，删除代码常量第二份真相；加生成前后一致性校验。

### 工程 / 流程层面（低成本、立即能做）
6. **打包模板固化**（issue-06/07）：新仓库一律 src 布局，README 写明 `pip install -e . --no-deps`；CI 加「wheel 体积 / 含 agent 模块」校验防回归。
7. **`.env` 支持显式指定**（issue-08）：增加 `NOVEL_AGENT_ENV=<path>`，文档固化「`.env` 随代码放仓库根、不进 wheel」。
8. **路径规范固化**（issue-09/13）：文档写明 Windows+Git Bash 下 git/python 一律 `D:/...`；区分 WorkBuddy 工作区 `D:/project/agent` 与项目根 `D:/project/NovelAgent`。
9. **推送走 SSH / deploy token**（issue-10）：自动化场景绕开 GCM 交互；固化 `GIT_TERMINAL_PROMPT=0` + 有界 `ls-remote` 探测。
10. **三仓库远程策略明确**（issue-11）：目前仅 `agent` 上 gitcode；`项目文档` / `小说` 是否上云待确认；根目录加说明文件讲清「并列三仓库」。
11. **文档链接检查**（issue-12）：目录变动后全仓扫 `docs/` 引用；加 markdown 链接校验。

## 三、关键结论
- 极品医仙最终成书：**51 章 / 176,078 字 / 状态 COMPLETE**（最后支线 S05_终极清算_仙尊登临）。
- 所有 13 个问题**均已解决或可缓解**；其中 issue-02（safe-delete）、issue-01（限流）、issue-03（续写）、issue-04（冲突仲裁）、issue-05（范围 SSOT）建议优先做内核层根治。
- 证据主要来自：`_driver_log_jipin.json`（144 条运行记录，含 54 次 clean_draft 失败、多次 max_retries_exhausted、1 次 pre_validation_blocked）、`_fix_subline_ranges.py`、`pyproject.toml`、`README.md`、推送诊断记录。
