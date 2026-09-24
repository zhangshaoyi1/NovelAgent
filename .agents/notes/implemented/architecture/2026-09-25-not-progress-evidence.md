# Agent Note: 进展证据分级（NOT_PROGRESS 负面清单——只有产出算进展）

Status: implemented

## Problem

`process_manager._PROGRESS_SIGNALS` 是"任一信号 mtime 新于阈值即视为有进度"，
其 docstring 明写「事件/进度/章节/**日志**任一有更新，即视为健康推进」——把
**活动痕迹**与**产出**混为一谈：

```
.state/progress.json        状态文件刷新
.events/events.jsonl        事件流（含巡检/心跳事件）
.state/quality_audit.jsonl  审计流
.state/tasks/logs           任务日志滚动      ← 日志刷新 ≠ 写了章
chapters                    唯一真正的产出
```

后果：心跳与巡检事件持续写 `events.jsonl`、日志持续滚动 ⇒ mtime 恒新 ⇒
`should_stall` 停滞熔断**永不触发**，挂死任务只能等墙钟 `should_timeout`
（而墙钟已按章数缩放，335 章 ≈ 50h）兜底——等于"慢"和"死"都兜不住。

同族（同一病的不同发作面）：

- `trace.jsonl` 双记 46% / 虚增 99%（把心跳当调用）；
- 心跳 `unknown` 误杀（把存活状态当进展，反向同源）。

三者根因一致：**把"还在动"当成了"有产出"**。

## Decision

引入进展证据分级，**默认不改判定行为，只加可见性**：

1. 信号拆两组：`PROGRESS_SIGNALS_OUTPUT`（产出型，当前仅 `chapters`）与
   `PROGRESS_SIGNALS_TRACE`（痕迹型，其余四项）。
2. `progress_evidence(dir) -> (等级, 产出型 mtime)`，三值闭集：
   - `real` —— 有产出，**唯一允许续期停滞时钟的证据**；
   - `trace_only` —— 只有痕迹在刷新，**不算进展**，且 `logger.warning` 显性留痕；
   - `none` —— 连痕迹都没有，保守交给墙钟。
3. `should_stall(task, now, strict)`：
   - 默认（兼容路径）沿用原"任一信号"语义，**行为零变化**，仅补 `trace_only` 留痕；
   - `strict=True`（或 `NOVEL_STRICT_PROGRESS=1`）时只认产出型 mtime，
     且**仅作用于写命令**（`_is_writer_task`），非写命令（cost/evaluate…）回退兼容路径。

判据形态来自 Chat On Steroids 的负面清单（`AGENTS.md:2388-2391`）：
"Page presence, reloads, metadata revisions and replayed starts do not [renew the clock]."
选它的关键理由：**负面清单不需要标定阈值**，从而绕开"绝对阈值跨对象离散度"
问题（纪律 #26/#27：项目间 p95 极差 1.30；M8 定档前置 `achieved_rate` 0.0%
即正面阈值不可达的实证）。

## Alternatives considered

**Why not 直接把严格模式设为默认（硬拦）？**
纪律 #20：闸门强度必须与证据匹配。当前只有"痕迹恒新"的定性证据，
没有"严格模式下误伤率多少"的实测分布；而误伤形态是**杀掉正在写的任务**
（不可逆）。故默认只留痕，硬化须先拿灰度数据——这同时也是纪律 #13/#17
（判据强度须与修复手段配对）。

**Why not 用 `degrade()` 而非 `logger` 留痕？**
新增降级点须走登记（`test_degrade_visibility` / `test_degrade_contract` 双向差集），
且 `degrade_registry.py` 当前有未提交改动，叠加会污染本次提交边界。
留痕目的是"让假进展可见"，`logger.warning` 已足够，且不触碰降级闭集红线。

**Why not 顺手把 `.events/events.jsonl` 从兼容清单里删掉？**
那会直接改变生产判定行为，等价于默认硬化，理由同上。留痕先跑一段时间，
拿到"trace_only 出现频次 / 其中多少后来真的停滞"的数据再定。

**Why not 在测试里拨真实时钟？**
纪律 #24：wall-clock 判据的测试必须把时间轴拨离边界。`should_stall(now=...)`
本就支持显式传钟，全部用例固定 `now`，不碰真实时间、不需要 `finally` 清理，
也就不存在"清理掩盖主断言"的风险。

## Consequences

- 新增 `tests/daemon/test_progress_evidence.py` 12 项，含三类邻近反向用例：
  严格模式下产出在窗内不误杀 / 非写命令不受严格模式影响（作用域）/
  冷启动无产出时保守不判停滞；以及一条契约红线
  `test_signal_vocabulary_is_disjoint`（产出集与痕迹集互斥且并集覆盖原清单，
  防止新增信号漏归类后又退化成"任一更新即进展"）。
- 消费者：`daemon/core.py:331` 的 `pm.should_stall(current)` 是既有真实调用点，
  本改动在其中生效，无新增伪消费者。
- 回归：`tests/daemon` 全量 69 passed（含原有 `test_process_manager.py`
  停滞判定与 `test_heartbeat_unknown_not_killed.py`），默认路径行为未漂移。
- 未覆盖（同族、另行处理）：① trace 双记 46% 的去重仍靠 `(in,out)+at≤2s`
  启发式，稳定 call-id 方案未做；② `rollback_budget.json` 运行时 stale
  属"lifetime 未声明"问题，需纪律 #32（owner + lifetime + publication boundary）
  统一收口；③ 角色实例 sleep/reuse 未做。
