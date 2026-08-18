# 问题 #03：沙箱长任务超时 —— 分批续写 + 断点恢复

| 项 | 内容 |
|---|---|
| 分类 | 小说生成 / 工程 |
| 严重度 | 中（不处理则单批次无法跑完整书，但可被分批绕过） |
| 状态 | 已解决（驱动层分批 + 日志断点） |
| 关联文件 | `drivers/_write_driver_jipin.py`（`RUN_TARGET_CHARS`、`load_log`/`save_log`、`arc_index`）、`_driver_log_jipin.json` |
| 证据 | `RUN_TARGET_CHARS = 50_000`（单批增量上限）；`_driver_run_resume.log` 记录 `restart driver`；日志含 `batch_done` / `advance_arc` / `novel_complete` 步骤 |

## 1. 问题描述
生成长篇（目标 ~15–18 万字、数十章）是**长耗时任务**。在 WorkBuddy 沙箱等环境里，单次命令有执行时长上限，无法一气呵成跑完整本书；且 LLM 调用本身受 issue-01 限流影响，单章就可能耗时数分钟。

## 2. 现象 / 证据
- 驱动定义一个 **单批增量上限** `RUN_TARGET_CHARS = 50_000`：本批累计新增字数达到即 `break`（记录 `batch_done`），正常退出，等待下一批。
- 断点恢复：日志 `_driver_log_jipin.json` 保存 `total_chars` / `chapters` / `arc_index` / `records`；重跑脚本时 `load_log()` 读取，`main()` 从 `arc_index` 对齐当前支线（**不硬重置回 S01**），从断点继续。
- `_driver_run_resume.log`：`=== restart driver at Mon Aug 17 19:22:09 ===` 证明曾中途重启续写。
- 完结判定：写到最后一条支线 `S05_终极清算_仙尊登临` 且写满其规划章节数 → 记录 `novel_complete` → 自动 `export` + `dashboard`。

## 3. 根因
- 运行环境对单条命令有超时上限；长任务必须**可中断、可恢复**。
- 原始命令行调用 `agent.cli write` 本身没有「写到第 N 字就停」「从断点续」的原生能力，必须由外部驱动脚本编排。

## 4. 解决方案（当前）
`_write_driver_jipin.py` 实现：
- **分批**：每批只新增约 5 万字就退出，分多批跑完，单批不触碰沙箱超时。
- **断点**：以 JSON 日志为唯一真相源，重启即从 `arc_index` / `total_chars` 续写；`set_subline()` 把状态机对齐到当前支线。
- **兜底硬上限** `HARD_CAP_CHARS = 450_000`：极端情况下也不会无限写。

## 5. 影响
- 使「沙箱里写完整本小说」成为可能：极品医仙最终 51 章 / 176,078 字就是分多批续写完成的。
- 代价：需要人工 / 自动化多次重启驱动；日志文件 `_driver_log_jipin.json` 会随着批次增长（最终 144 条记录）。

## 6. 改进建议
- **内核原生支持续写**：`agent.cli write` 增加 `--resume`、`--max-chars <N>`、`--max-chapters <N>` 参数，断点由状态机（`state.json`）承载，减少对外部驱动的依赖。
- **守护 / 常驻模式**：提供一个 `agent.cli serve` 或 `--daemon`，在沙箱允许的长连接内持续产出，自动在限流时空转而非退出。
- **进度可观测**：把 `batch_done` / `advance_arc` 这类里程碑也写入 `state.json`，让 Dashboard 直接展示「写到哪了、卡在哪」。

## 7. 复现 / 验证
```bash
# 多次运行同一驱动即可分批续写（日志存在则自动断点）
PYTHONPATH=D:/project/NovelAgent/agent/src python D:/project/NovelAgent/agent/drivers/_write_driver_jipin.py
# 查看当前断点
python -c "import json; d=json.load(open(r'D:/project/NovelAgent/小说/_driver_log_jipin.json',encoding='utf-8')); print(d['chapters'], d['total_chars'], d['arc_index'])"
```
