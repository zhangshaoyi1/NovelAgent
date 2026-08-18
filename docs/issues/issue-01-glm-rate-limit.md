# 问题 #01：智谱 GLM 限流（429 / RPM）导致章节写入频繁失败与重试

| 项 | 内容 |
|---|---|
| 分类 | 小说生成 |
| 严重度 | 高（直接决定生成速度，不处理则几乎无法跑完整书） |
| 状态 | 已解决（驱动层退避重试 + 写前冷却） |
| 关联文件 | `drivers/_write_driver_jipin.py`、`drivers/_write_driver.py`、`.env`（GLM 配置） |
| 证据 | `_driver_log_jipin.json` 中多次 `write` 步骤出现 `3 次重试仍失败` / `max_retries_exhausted` |

## 1. 问题描述
调用真实 LLM（智谱 GLM-4.7，`https://open.bigmodel.cn/api/paas/v4/`）生成章节时，免费 / 按量账户存在 **RPM（每分钟请求数）上限**。一旦瞬时请求过多或上一章刚结束立刻发起下一章，服务端返回 `429` 或连接超时，导致 `write` 子命令失败。

## 2. 现象 / 证据
- 驱动日志 `_driver_log_jipin.json` 中多次出现：
  - `#37 write :: {"error": "3 次重试仍失败, 中止本批"}`
  - `#44 / #47 / #49 / #87 / #120 / #136 write :: {"error": "max_retries_exhausted, 中止本批"}`
- 由于 `write` 在限流 / 超时时 stdout 往往不是结构化 JSON（而是 rich UI 文本或空串），错误信息落在 `_raw` / `_stderr`，无法直接用 `res["error"]["code"]` 判定，必须扫描原始输出。

## 3. 根因
- 智谱按量账户 RPM 限额较低；驱动是单线程同步循环，章节间无间隔会瞬间烧光配额。
- `write` 失败时错误载体不稳定（UI 文本 / 空串），导致「是否限流」判定困难，容易误判为不可重试错误而放弃。

## 4. 解决方案（已在驱动中实现）
`drivers/_write_driver_jipin.py` 做了两层防护：
1. **写前冷却**：`COOLDOWN = 45`（秒），每章发起前 `time.sleep(COOLDOWN)`，主动避让 RPM。
2. **指数退避重试**：`write_chapter()` 用退避表 `[10, 30, 60, 120, 180, 300, 600, 900]` 秒，最多 9 次；仅对结构性硬错误（`_is_hard_error`）跳过重试，限流 / 超时 / 瞬断一律长退避后重试。
3. **限流判定 `_is_rate_limited()`**：扫描原始输出中的 `429` / `速率限制` / `rate limit` / `request timed out` / `timeout` / `draft` 关键字，避免误判。

## 5. 影响
- 单次 `write` 在限流下会从「秒级」拉长到「分钟级」（退避叠加）；整本 51 章、约 17.6 万字因此耗时较长，但**未丢章、未中断**，靠分批续写（见 issue-03）跑完。
- 若不做退避，批次会因连续 `max_retries_exhausted` 频繁中止，实际几乎无法生成完整小说。

## 6. 改进建议
- **下沉到 NovelAgent 内核**：`write` 工作流应内置 429 / 超时的指数退避 + 抖动，使单次 `write` 自身鲁棒，外部驱动只需简单循环，降低脚本复杂度。
- **冷却参数化**：`COOLDOWN` 应作为驱动 / CLI 参数，便于按账户配额档位调节。
- **配额探测**：首次运行可主动探测 RPM，自动设定冷却，而非硬编码 45 秒。

## 7. 复现 / 验证命令
```bash
# 跑真实驱动（会真实调用 GLM，观察限流退避日志）
PYTHONPATH=D:/project/NovelAgent/agent/src python D:/project/NovelAgent/agent/drivers/_write_driver_jipin.py
# 仅验证 LLM 连通性与配置（不写章节）
PYTHONPATH=D:/project/NovelAgent/agent/src python D:/project/NovelAgent/agent/scripts/_smoke_test_jipin.py
```
