# Agent Note: 停止执行者缺席与 daemon 静默死亡
Status: implemented

## Problem

Web 点「终止」后页面永久停在「终止中」，任务文件恒 `status=running`，进程仍在跑。

机制：停止的**执行者是 daemon**——Web 只写 `stop_requested`（`web/runner.py:RunManager.stop`
→ `tq.request_stop`），由 daemon 在 `_poll_child` 轮询到后 `taskkill /T /F` 杀进程树并
`finalize_task(STOPPED)`。

daemon 一旦死亡，停止请求永远无人执行（Web 侧 `_watch_task` 只轮询终态，不兜底杀进程）。

## Decision

根因：`WriterDaemon.run_forever` 主循环（及循环前的 `_clear_stale_stop_flags` /
`_recover_orphans`）无异常兜底，而 daemon 由 `ensure_daemon` 以 stdout/stderr=DEVNULL 拉起——
主循环内任何未捕获异常（尤其上一份笔记那类 `SystemExit`）都让进程无声退出，呈现「三无」：
无执行者、无告警、无日志。

注意 `_clear_stale_stop_flags` / `_recover_orphans` 运行在主循环之前，这里崩溃会让 daemon 在
「开始服务前」就死，比循环内崩溃更隐蔽，故同样需要兜底。修复四路：

- **① 主循环兜底**：`run_forever` 逐轮 `try/except BaseException`，启动阶段两处调用同样兜底；
  异常写 `<root>/.daemon/crash.log`（含 traceback，>1MB 截断）后退避 1s 继续服务——daemon 不再
  因单次异常退出，停止执行者始终在线。
- **② 常驻进程禁继承 shim**：`ensure_daemon` 显式传 `CODEBUDDY_SAFE_DELETE_ENABLED=0`，避免宿主
  删除 shim 在主循环清理口触发 `SystemExit`（与上一份架构笔记同源）。
- **③ Web 侧兜底**：`RunManager.stop` 写标记后探 `tq.daemon_alive(pdir.parent)`，daemon 离线则
  Web 兜底 `pm.force_terminate`（杀树 + 归档 STOPPED + 锁自愈）；pid 缺失时直接
  `finalize_task(STOPPED)`。
- **④ 归档前建目录**：`finalize_task` 归档前 `mkdir -p done/`，防止 daemon 崩溃后残留态归档时因目录
  缺失再次失败。

## Alternatives considered

- **Web 侧无条件直接 `taskkill`**：daemon 活着时抢杀会破坏 daemon 的优雅归档与锁清理，造成双份
  残留与锁毒化；故仅在 daemon 离线时 Web 才兜底——否决无条件强杀。
- **给 daemon 加看门狗重启**：治标且引入重启竞态，先靠主循环兜底保活更稳；看门狗留作未做。
- **`stop` 直接返回失败而非尝试兜底**：用户体验无改善，兜底杀树成本可控，故保留兜底路径。

## Consequences

- daemon 活着时的优雅停任务路径是 `tq.request_stop` 写标记（返回 signaled），daemon 约 8 秒生效，
  不必手动 `taskkill`；之后 `agent unlock -d <dir>` 清陈旧锁（按 pid 精确匹配，勿手删 `writer.lock`）。
- 修复对已在内存中运行的 Web 进程不生效，需重启 Web 才能加载 `RunManager.stop` 的新兜底逻辑；
  daemon 侧修复在下次 `ensure_daemon` 拉起时生效。
- `.daemon/crash.log` 现在会留下 daemon 死前 traceback，排障从「全黑盒」变为「可读栈」；与
  `.daemon/runtime.log`（运行时自检笔记写入）构成 daemon 可观测性双文件。
- 验证 daemon 是否存活可用 `tq.daemon_alive`；Web 兜底路径仅在确认离线时触发，避免与 daemon 双杀。
- 前序 `0f3a445` 修陈旧停止标志毒化下次拉起、`20279df` 选项相关，为本修复的铺垫，可一并参考。
- 相关 commit：`9be38f1`。

## 运维验证

- 复现旧症状：kill 掉 daemon 进程 → Web 点「终止」→ 任务恒 running。新版本 daemon 因主循环兜底不再死。
- 确认 daemon 在线：`tq.daemon_alive` 返回 True；离线时 Web 走 `force_terminate` 兜底，终态为 STOPPED。
- 归档前 `mkdir -p done/` 保证崩溃残留态也能归档，不再因目录缺失二次失败。
- 主循环退避 1s 后继续服务，单点异常不会引发 daemon 雪崩式反复退出。
- 排障顺序：先查 `.daemon/runtime.log`（运行时指纹）确认进程是否当前生效，再查 `crash.log`（崩溃栈）。
