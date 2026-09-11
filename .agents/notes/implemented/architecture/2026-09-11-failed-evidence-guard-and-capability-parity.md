# Agent Note: 失效证据守门下沉 + 能力对账真机制 + 运行时自检
Status: implemented

## Problem

2026-09-10~11 两天累计 17 次回滚 / 84 章 / 39.7 万字被重写（重写率 67%），LLM 调用失败率
从 3.9% 升到 18.5%。根因收敛为一句话：**同一个守门原则在四个决策层只落实了一层**——
「证据不可信 ⇒ 只复评、不做不可逆处置」这句不变式，只存在于 HA-Eval L4 的
`evaluate_with_repair()`，其余三处入口绕过它直接做不可逆处置（回滚 / 中断整批）。

## Decision

**① 失效证据守门下沉到全部入口**：
- `EvaluatorAgent.evaluate()`（滚动体检 / CLI / service）：原 `overall_pass=False` 即
  `trigger_rollback()`，现先过 `DispositionPolicy` + `DispositionGate`，只有「硬指标 + 证据可信」
  失败才回滚；
- `_rolling_eval_checkpoint`（`workflows/pipeline/agentic_pipeline_agents.py`）：原
  `overall_pass=False` 即 `break` 中断整批，现做四象限裁决 `block`/`warn`/`recheck`。
- 配套类型：`evaluator_types.py` 增 `DimensionResult.trustworthy` / `credible_failed`，
  `to_dict()` 导出 `confidence`；`NovelHealthReport` 增 `trustworthy` / `hard_failed` /
  `soft_failed` / `gate_decision()`（pass/warn/block/recheck）。

**关键边界（防后人「优化」掉）**：只对 `confidence=0` 豁免处置，**不放宽任何阈值**；可信的硬指标
失败照样回滚 / 中断。软维度（如 `appeal`）的 `evidence=None` ⇒ `confidence` 恒 1.0，故 P0-A 对
它们无效；真正拦住软维度断批的是 P0-B 的 `required` 分级。

**② 能力对账由白名单升级为差集真机制**（`tests/architecture/test_capability_parity.py`）：
原硬编码白名单只对账 `_archive_chapter` 一个 hook，14:30 排查报告列的 P1×4 一个都发现不了。
现 AST 自动提取 `M5WriteChapterWorkflow.run()` 链路可达的私有能力（`self._xxx`），与
`AgenticWriteWorkflow.run()` 链路内出现的调用名做差集，差集必须 ⊆ `_PARITY_EXEMPT` 豁免登记表；
另加前置自检（能力集不得过小、豁免表不得含失效条目）。

**③ 哑火接线**（`workflows/writing/agentic_write.py` 全部与 M5 同位）：F-E4.3 `_validate_evidence`
落盘前；M18 草稿三件套 生成后 save / 落盘后 clear / 写章前 check；F-E2.2 注入套路 任务构建后追加、
用毕清除；P2-3 爽点剧本空转在 `m5_context.py` 读取处告警一次（类级去重）。
**有意取舍**：E3 `_pre_validation` 在生产入口显式 `pre_validate=False`，由生成后 M21 审稿兜底，
登记为能力对账豁免、不接线。

**④ 运行时自检** `core/infra/runtime_selfcheck.py`（新，329 行）：把本进程实际生效的运行时做成
可断言指纹——源码聚合指纹（sha256/文件数/最新改动）、生效档位（id/model/timeout/max_tokens/
thinking）、关键 env、宿主 shim 状态；支持陈旧进程检测。挂载：`doctor` 新增 `runtime` 模块；
daemon `run_forever` 启动时登记指纹并写 `<root>/.daemon/runtime.log`。动机是两天内 5 次
「改动没生效」（改 .py 不影响已启动进程、档位 timeout=90s 与上游思考型模型脱节、shim 语义随宿主变化）。

**⑤ 附带**：档位 timeout 90→300s（`models.json` 的 `mp-d209dce09b`；成功调用延迟 p90 实测 92.3s
恰好越过 90s 线，是 112 次超时的主因；改配置须重启进程，`models.json` 在 .gitignore 不入库）；
删除红线补 import 别名解析（见上一份笔记）；清理 `daemon/process_manager.py` 不可达死代码。

## Alternatives considered

- **继续用硬编码白名单扩列**：排查已证明白名单会漏检且维护即腐化，AST 差集自动发现未知缺口——
  否决白名单。
- **置信度低也放宽阈值**：会放软对硬指标失败的拦截，违背守门不变式核心——只豁免处置、不放松标准。
- **运行时自检用启动参数而非源码指纹**：改 .py 不重启进程时参数不变，指纹抓不到「陈旧进程」，
  故指纹含源码聚合哈希才能识别。

## Consequences

- `tests/architecture` 67 passed（基线 64 + 3：删除别名自检、能力对账差集、能力对账前置自检）。
- 灵敏度实证：模拟生产入口丢失 `_validate_evidence` / `_collect_injected_tropes` 接线后立刻被
  差集拦下，能力对账红线开始生效。
- 重写率与失败率数据待下一周期复测确认（本次仅交付守门机制，未做回滚率回归验证——**待核实**）。
- 相关 commit：`451bd4b`。
