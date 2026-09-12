# AGENTS.md - tests/ 业务测试

## 职责

pytest 测试套件，覆盖所有业务层代码。测试文件随里程碑/批次增长，**本文件只做子目录概览，不做逐文件清单**——具体文件直接看目录。

## 目录结构（2026-09-12）

| 位置 | 范围 |
|------|------|
| `tests/`（根，约 120 个 `test_*.py`） | M 系工作流（m1~m23）、G 系门禁（g2~g12）、E 系评估（e2~e4）、P 系、RAG、状态机/命令注册/路由、质量检查、回滚预算、滚动体检检查点（`test_rolling_eval_checkpoint.py`）、桥段禁用与教训（`test_beat_ban_and_lessons.py`）、事实抽取（`test_m5_fact_extractor.py`）、设定正典（`test_setting_canon.py`）、RAG 回滚同步（`test_rag_rollback_sync.py`）等 |
| `tests/architecture/` | **架构红线测试族（改架构前必看，零失败）** |
| `tests/core/` | core 层测试 |
| `tests/daemon/` | daemon：任务队列/daemon、进程管理、删除护栏容忍、D3 路由与解锁 |
| `tests/phase0~5/` | 分阶段回归测试 |
| `conftest.py` / `_g3_fakes.py` | 共享 fixtures / G3 假件 |

## 架构红线测试族（tests/architecture/）

| 文件 | 红线 |
|------|------|
| `test_state_ownership.py` | 状态所有权：冻结 state.json 直写面（豁免棘轮只减不增） |
| `test_degrade_visibility.py` | F-1 降级可见化：降级必须走 degrade()，静默点清零 |
| `test_capability_parity.py` | 能力对账：接口 + 副作用 hook 差集真机制 |
| `test_hostile_delete_env.py` | 宿主敌意：safe-delete 护栏不被 SystemExit 逃逸 |
| `test_setting_canon_wiring.py` | 设定回写接线（归档 hook → 生产入口） |
| `test_redlines_agent.py` | agents 层红线 |
| `test_f7_f8_guards.py` | F7/F8 守门 |
| `test_write_lock_unification.py` | 写锁统一 |
| `test_parity_registry_catalog.py` | 注册表/目录对账 |
| `test_quality_policy.py` | 质量策略 |
| `test_m5_run_deprecated.py` | M5 旧入口废弃守卫 |

## 运行方式

```bash
# 全量口径 = 无参数（含 tests/ + llmagent_tests/，约 2000+ 用例）
python -m pytest -q --tb=short

# 判定看 FAILED 行，不看退出码（退出码可能被收尾污染）
# 宿主沙箱环境须 CODEBUDDY_SAFE_DELETE_ENABLED=0 再跑长测
```
