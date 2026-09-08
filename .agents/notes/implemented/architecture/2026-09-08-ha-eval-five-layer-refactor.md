# Agent Note: HA-Eval 五层高可用评估架构重构（缓存碰撞根治 + 坏数据守门）

Status: implemented

> 作者：agent · 日期：2026-09-08
> 事故样本：`novels/五灵破归档`（eval 五维串值假失败 → 误回滚重写 → 第 25 章写崩）

***

## Problem

2026-09-08 全书体检 `overall_pass=false`（29.61/100），触发回滚重写，最终第 25 章写崩、
预算超支 +12.3%、重写稿比原稿更短。排查发现这不是内容质量问题，而是**缓存键碰撞引发的
假失败**：

1. 网关语义缓存 `SemanticCache._make_key`（`llmagent/gateway/rate_limiter.py`）只采
   `messages[-2:]` 的**末 200 字符**做哈希；而评估 prompt 的维度差异在**开头**
   （`f"请评估…在「{维度标签}」维度…"`），采样窗口与差异点完全错开 → 五个维度算出同一 key，
   后四次命中缓存、共用第一份响应（trace 指纹：1 次真实 + 4 条 `latency=0` 且 token 全同）。
2. 计数维（缺陷条数，阈值 0）与 0-100 评分维（85/80）共用裸 `float` 通道，串值在类型层不可见；
3. 降级逻辑**方向反了**：只防「LLM 没输出」（给通过值），不防「LLM 输出坏值」（直接采信）；
4. 决策层 `evaluate_with_repair` 对可疑分数零校验，直接 `trigger_rollback()`（删章）+ `rewriter()`
   （几十万 token），无复评、无 dry-run、无代价预估。

且这是**第三次同族事故**（9/7 G8 窗口前置假失败、9/8 eval 串值、本次），共同模式 =
**作用域/量纲错配 + 单点证据直连不可逆动作**。全仓库 22 文件 37 处 `chat_utility` 暴露在同一缺陷下。

## Decision

引入五层防御，每层只向上一层暴露**已验证**的结果（自下而上）：

| 层 | 模块 | 职责 |
|---|---|---|
| L1 缓存语义层 | `llmagent/gateway/cache_policy.py` | `CacheClass{DETERMINISTIC/JUDGMENT/CREATIVE}`，**默认拒绝**缓存（只有显式 opt-in 才启用）；全量哈希键（role + 完整 content + model + temperature + max_tokens）；`ChatResponse` 增 `cache_hit/cache_key`；删除 `RequestGate._cache` 死缓存 |
| L2 维度契约层 | `core/quality/dimension_registry.py` | `DimensionSpec`（unit/direction/scope/value_range/safe_default/counted_by_issues）SSOT 登记表，收口原 8 处散落语义；`DimensionResult` 携带 `spec`，构造期 `enforce_contract` 校验量纲 |
| L3 证据校验层 | `core/quality/eval_evidence.py` + `validators.py` | `EvalEvidence`（prompt_hash/response_hash/cache_hit/confidence）；`DimensionValidator` 四类检测（批级串值/量纲自洽/评分维下限/缓存命中）→ 坏数据降 `confidence=0` |
| L4 处置策略层 | `core/quality/disposition.py` | `DispositionRule` 声明式映射表 + `DispositionGate` 四道守门（置信度/双证据/代价预估/dry-run），替代 `evaluator.py` 的 golden/mainline/ending 三处 name-prefix 特判 |
| L5 可观测层 | `core/quality/audit.py` + `cli/commands/eval_audit.py` | 维度级审计日志 `.state/quality_audit.jsonl`（只追加不阻断）；`eval_audit` 命令离线回放点名串值/恒值/缓存命中 |

核心不变式：**证据不可信（`confidence=0`）⇒ 只复评，绝不删章重写**（`Action.RETRY_EVAL`
优先于一切处置）。`evaluate_with_repair` 重写为：失败维度 → `DispositionPolicy.plan()`
→ 按 `Action` 分发（RETRY_EVAL 复评一次 / ESCALATE 上报 / LOCAL_REPAIR 定向修末章 /
ROLLBACK_REWRITE 过守门器）。

### 兼容性保证（关键）

- `DimensionResult.to_dict()` 字段**完全不变** → `to_markdown()` / Web UI / `.state` 落盘零改动；
- 22 文件 37 处 `chat_utility` 调用**零改动**即可自动免疫（默认 `JUDGMENT` 不缓存）；
- 旧 8 处散落语义（`_SOFT_MARGIN`/`COUNT_DIMS`/`_EVAL_DIM_LABELS`/`_DIM_SCOPE` 等）
  改为由登记表**派生别名**，标记 deprecated，一个里程碑后删除。

## Alternatives considered

### Why not 只修缓存键（止血 patch）？

改 `_make_key` 为全量哈希只能堵住「末 200 字符」这一种碰撞，堵不住将来任何
「判定类结果被复用」的场景。且决策层对坏数据零校验的问题依然存在，任何一处新缓存
实现都会复发。主理人明确拍板「不要止血，要可行方案，重构都可以」。

### Why not 让 quality_critical 强制不走缓存（绕过缓存）？

原 `SemanticCache` 已有 `if req.hint.quality_critical: return` 豁免，但 `chat_utility()`
硬编码 `quality_critical=False`、`TracedLLMClient._build_hint` 是
`quality_critical=(use != "utility")`——所有 utility 通道一律无豁免。即使改成强制豁免，
也只是「关掉缓存」而非「让缓存安全」，丢掉了确定性转换类调用（格式转换/确定性抽取）应有的
复用收益，且没有解决契约层/证据层/决策层的结构性缺陷。

### Why not 直接删掉语义缓存？

确定性抽取类调用（同一份正文跑多次抽取）确实有复用价值。方案保留缓存能力，但把
「能不能缓存」从「由调用方式隐式决定」改为「被显式声明（`CacheClass`）、集中裁决
（`CachePolicy`）、可观测（`cache_hit` 埋点）」的属性。默认拒绝 + 显式 opt-in 是
fail-safe 的默认值。

## Consequences

### 收益

- 缓存碰撞 / 量纲错配 / 坏数据在到达决策层前被结构性拦截；
- 事故定位从「人工比对 token」变为「`eval_audit` 一条命令点名」；
- 新增维度只要在 `dimension_registry.DIMENSIONS` 登记 unit/direction/scope，
  处置行为即自动正确，无需再写 name-prefix 特判。

### 代价 / 风险

- **评分类 LLM 调用不再复用**，token 可能上涨（但评分类 prompt 都含章节正文，真实复用率
  本就极低，实测影响可忽略）；
- `DimensionResult` 构造期 `enforce_contract` 默认严格模式，可能对未登记的维度抛
  `DimensionContractError`——已提供 `NOVEL_AGENT_DIM_CONTRACT_WARN=1` 环境变量降级为
  warn-only，供生产先跑一轮观察误报；
- 旧测试 `test_evaluator_escalates_when_repair_fails` 的 `bad_score` 返回
  `coherence=0`，被 L3 正确判为坏数据 → 改为返回 70（合法但低于 85 合格线），
  语义更贴近「真实失败」而非「坏数据」。
