# 问题 #02：失败残留草稿（F18.4）+ NovelAgent safe-delete 在沙箱「失败关闭」阻塞续写

| 项 | 内容 |
|---|---|
| 分类 | 小说生成 |
| 严重度 | 高（残留草稿会卡死后续所有章节写入） |
| 状态 | 已缓解（驱动侧强制 `os.remove` 绕过）；根因未在内核修复 |
| 关联文件 | `drivers/_write_driver_jipin.py`（`clean_stale_draft`）、`src/agent/...` 的 safe-delete / F18.4 恢复机制 |
| 证据 | `_driver_log_jipin.json`：`clean_draft` 失败 **54 次**、成功仅 **2 次**，失败原因为 `windows-sandbox-recycle-bin-unavailable` |

## 1. 问题描述
当某章 `write` 中途失败（限流 / 超时 / 落盘异常）时，会在 `.state/draft.wip`（及 `.wip.bak`）留下**死草稿**。NovelAgent 的 F18.4 恢复机制会**拒绝在残留草稿上覆盖写入**，必须先将草稿清掉才能继续。问题在于：清理草稿依赖 NovelAgent 的「安全删除（safe-delete，先送回收站）」，而沙箱里**回收站不可用**，safe-delete 直接 **FAIL CLOSED（拒绝删除）**，于是下一章永远卡在「草稿未清 → 拒绝写入 → 重试 → 草稿仍清不掉」的死循环。

## 2. 现象 / 证据
日志中大量：
```json
{"step":"clean_draft","error":"[safe-delete][SAFE_DELETE_FAIL_CLOSED] {\"target\":\"D:\\project\\agent\\projects\\jipin-yixian\\.state\\draft.wip.bak\",\"reason\":\"windows-sandbox-recycle-bin-unavailable\"}"}
```
- 统计：`clean_draft` 共 56 次，其中 **OK=2 / FAIL=54**。
- 失败原因恒定：`windows-sandbox-recycle-bin-unavailable`（Windows 沙箱无回收站）。
- 直接后果：紧随其后的 `write` 出现 `max_retries_exhausted, 中止本批`（如 `#87`、`#120`、`#136`）。
- 仅最后两次（#121 / #122，项目已迁到规范路径后）`clean_draft` 才成功 `{"removed":"draft.wip"}`。

> 注：safe-delete 目标路径中出现 `D:/project/agent/projects/jipin-yixian`（旧位置），说明这些失败发生在重构前项目位于 `D:/project/agent/` 之下时（见 issue-13）。

## 3. 根因
- NovelAgent 的 safe-delete 删除策略是「移动到 Windows 回收站」，而非直接 `os.unlink`。
- 在沙箱 / CI / 无桌面环境下回收站不可用，safe-delete 设计为 **fail-closed（宁可不动也不冒险删）**，因此清理失败。
- 驱动侧的 `clean_stale_draft()` 调用 `os.remove()`，但被路由到了该 safe-delete 包装，于是同样失败关闭。

## 4. 解决方案（当前）
`drivers/_write_driver_jipin.py` 的 `clean_stale_draft()` 在**每次 `write` 尝试前**主动删除 `.state/draft.wip` 与 `draft.wip.bak`，仅删 `.wip` 系列、不动已 accepted 的 `chapters/*.md`，把「下一轮 write 前存在的 draft」当作上一轮失败遗留死草稿清掉，从而让 F18.4 不再拒绝覆盖。

## 5. 影响
- 若不清理：任意一次失败章节都会让后续整批生成卡死（这正是日志里多次 `中止本批` 的来源）。
- 当前靠驱动层 hack 缓解，但 **safe-delete 失败关闭本身是内核缺陷**——在非交互环境（沙箱、服务器、Docker、GitHub Actions）下一律会触发，任何「失败后有残留草稿」的场景都会受阻。

## 6. 改进建议（推荐内核修复）
- **提供「强制硬删除」开关**：新增配置 / 环境变量（如 `NOVEL_AGENT_HARD_DELETE=1` 或 `safe_delete=false`），让删除直接 `os.unlink` 绕过回收站，专门用于沙箱 / CI / 自动化场景。
- **safe-delete 降级而非失败关闭**：当回收站不可用时，自动降级为直接删除并记录警告，而不是拒绝删除导致流程卡死。
- **F18.4 恢复区分「用户草稿」与「崩溃残留」**：崩溃残留应允许自动清理，仅用户显式保存的草稿才进入恢复流程。

## 7. 复现 / 验证
- 在沙箱直接触发一次失败写入后观察 `.state/draft.wip` 是否残留，再观察下一次 `write` 是否被 F18.4 拒绝。
- 验证清理函数：
```python
# 在驱动目录下
PYTHONPATH=D:/project/NovelAgent/agent/src python -c "import sys; sys.path.insert(0,'D:/project/NovelAgent/agent/drivers'); import _write_driver_jipin as d; d.clean_stale_draft(); print('cleaned')"
```
