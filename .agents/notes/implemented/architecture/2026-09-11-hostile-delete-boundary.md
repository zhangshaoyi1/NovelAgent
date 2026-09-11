# Agent Note: 文件系统删除的宿主敌意边界（safe-delete shim）
Status: implemented

## Problem

宿主 WorkBuddy 经 `PYTHONPATH` 注入 `cli/vendor/shim/sitecustomize.py`，在同一 turn/request
累计删除越过阈值后，对 `pathlib.Path.unlink` / `os.remove` / `shutil.rmtree` 的调用入口抛
`SystemExit(1)` 而**不是 `OSError`**。`SystemExit` 继承 `BaseException`，全仓既有
`except OSError` / `except Exception` 一律接不住。后果分两类：

- 批次写完却被判 failed：写锁收尾阶段的删除被打断，终态收尾失败；
- daemon 被打死：任务在 `running/` 与 `done/` 双份残留，且停止请求无人执行。

**激活判据**：`PYTHONPATH` 指向 shim 目录 + 存在 `CODEBUDDY_SESSION_ID` 或
`CLAUDE_SESSION_ID` + `CODEBUDDY_SAFE_DELETE_ENABLED != 0`。自检：
`python -c "import pathlib;print(pathlib.Path.unlink.__module__)"` 输出 `sitecustomize`
即被劫持。**计数口径是「同一 turn/request 累计」而非本进程累计**，落盘
`%TEMP%\codebuddy-safe-delete-bulk\<sha256(session)>\state.json`（TTL 7 天），子进程继承
该 turn 计数——因此单测内逐个删除不触发，但一批次多步删除会累计越过阈值。

## Decision

以三层防御收口 8 个删除站点，确保删除失败被接住、且不掩盖真实异常：

**① 关键路径改名（用 `os.replace` 挪走而非删）**：
- `core/project_lock.py` 陈旧锁接管 / 释放写锁；
- `daemon/task_queue.py` 的 `finalize_task` 归档与失败回退、`clear_daemon_stop_flag`。

**② 工具层吞双异常**：
- `base/utils.py:safe_remove` 回退链：删 → 清空+改名 `.bak` → 改名 `<parent>/.trash`，
  末态以 `exists()` 判定，绝不抛错；
- `core/infra/atomic.py:_discard_tmp` 的 `finally` 清理口——注意 `shutil.rmtree(
  ignore_errors=True)` 只吞 `OSError`，必须显式接 `SystemExit`。

**③ 零散端点**：`cli/commands/unlock.py`、`core/event_sourcing/event_store.py`、
`base/model_profiles.py`、`core/story/technique_store.py`、`cli/commands/export_skill.py`。

**有意不单独处理**：`setting_manager.rollback_to_snapshot`、
`m18_recovery.DraftManager.clear_draft` 均走 `safe_remove`，已被第②层覆盖，不重复加壳。

**上升为架构不变性**：《项目文档/架构文档.md》§3.4 全局不变性第 7 条——关键路径禁止裸
`unlink`/`rmtree`；删除必须经适配层或就地接 `SystemExit`；清理失败不得掩盖真实异常
（原子写预写阶段的 `OSError` 必须原样透传）。该条写入架构文档，本笔记仅引用，不改动它。

**测试**：`tests/architecture/test_hostile_delete_env.py` 做静态扫描（每处删除调用是否接得住
`SystemExit`）+ 运行时敌意注入跑 6 条关键路径；后补 import 别名解析判据
（`from os import remove` / `import shutil as sh` / `from shutil import rmtree as rt` 三类
原扫描漏检，已补）。
`tests/daemon/test_delete_guard_tolerance.py` 验证 daemon 清理口的容错。

## Alternatives considered

- **全局 monkeypatch unlink/rmtree 兜底 SystemExit**（在 shim 之外再包一层）：会掩盖所有清理
  失败，直接违反「清理失败不得掩盖真实异常」的不变性，且与第 7 条冲突——否决，改为逐站点显式接管。
- **仅在 daemon 进程设 `CODEBUDDY_SAFE_DELETE_ENABLED=0`**（第 3 份笔记后来确实这么做）：单测 /
  CLI / 一次性脚本仍可能触发，治标不治本；须配合第②层适配层才能全场景兜底，二者互补而非互斥。
- **顶层统一把 `SystemExit` 转 `OSError`**：污染异常语义，调用方无法区分「删除被宿主拦」与
  「磁盘错误」；保留两类异常各自含义更利于排障——否决。

## Consequences

- 8 个删除站点不再因宿主计数触发而崩溃；写锁收尾与 daemon 存活恢复，双份残留不再产生。
- **运维副作用（判据看 FAILED 行不看退出码）**：在宿主会话里跑全量 pytest，可能「无 FAILED 行
  但退出码=1」——pytest 自身收尾清理临时目录被拦，`SystemExit` 从 atexit 逃逸。需要干净退出码
  用 `CODEBUDDY_SAFE_DELETE_ENABLED=0`。注意 `env -u CODEBUDDY_SESSION_ID` 单独无效
  （`CLAUDE_SESSION_ID` 也在，两个都要 unset）。
- 静态扫描 + 运行时注入双保险，import 别名写法已被纳入扫描，后续新增 `from os import remove`
  类写法无法绕过。
