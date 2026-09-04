# AGENTS.md - tests/ 业务测试

## 职责

pytest 测试套件，覆盖所有业务层代码。

## 测试文件

| 文件 | 测试范围 |
|------|----------|
| `test_m1_config.py` | M1 配置 |
| `test_m1_genre_wiring.py` | M1 题材绑线 |
| `test_m2_discuss.py` | M2 讨论 |
| `test_m3_outline.py` | M3 大纲 |
| `test_m4_character.py` | M4 角色设计 |
| `test_m4_graceful.py` | M4 优雅降级 |
| `test_m5_dedup_tail_loop.py` | M5 去重尾循环 |
| `test_m6_adjust.py` | M6 调整 |
| `test_m8_mode.py` | M8 模式 |
| `test_m9_genre_pack.py` | M9 题材包 |
| `test_m10_rollback.py` | M10 回滚 |
| `test_m11_export.py` | M11 导出 |
| `test_m12_audit.py` | M12 审计 |
| `test_m13_foreshadow.py` | M13 伏笔 |
| `test_m14_architecture.py` | M14 架构 |
| `test_m15_bookworm.py` | M15 书虫 |
| `test_m16_pacing.py` | M16 节奏 |
| `test_m16_command_router.py` | M16 命令路由 |
| `test_m18_recovery.py` | M18 恢复 |
| `test_m20_analyze.py` | M20 分析 |
| `test_m21_review.py` | M21 评审 |
| `test_m22_setup.py` | M22 设置 |
| `test_m23_short.py` | M23 短篇 |
| `test_command_registry.py` | 命令注册表 |
| `test_command_router.py` | 命令路由 |
| `test_state_machine.py` | 状态机 |
| `test_llm_client.py` | LLM 客户端 |
| `test_llm_client_fallback.py` | LLM 客户端回退 |
| `test_llm_provider_registry.py` | LLM Provider 注册表 |
| `test_setting_manager.py` | 设定管理器 |
| `test_quality_checker.py` | 质量检查器 |
| `test_quality_checker_d.py` | 质量检查器 D |
| `test_reader_appeal.py` | 读者吸引力评分 |
| `test_consistency_checker.py` | 一致性检查器 |
| `test_continuity_ledger.py` | 连续性账本 |
| `test_rag_embeddings.py` | RAG 嵌入 |
| `test_rag_indexer.py` | RAG 索引 |
| `test_rag_retriever.py` | RAG 检索 |
| `test_rag_vector_store.py` | RAG 向量存储 |
| `test_planner_lenient.py` | Planner 宽松模式 |
| `test_timeline.py` | 时间线 |
| `test_safe_remove.py` | 安全删除 |
| `test_usage_report.py` | 用量报告 |
| `test_v1_0_new_modules.py` | v1.0 新模块 |
| `test_e2_inject.py` | E2 注入 |
| `test_e3_prevalidation.py` | E3 预校验 |
| `test_e4_evidence.py` | E4 证据链 |
| `test_enforce_gate.py` | 强制门禁 |
| `test_evaluator_rollback.py` | 评测器回滚 |
| `test_foresight_state_machine.py` | 前瞻状态机 |
| `test_g2_stricter_gate.py` | G2 严格门禁 |
| `test_g3_auto_plan.py` | G3 自动规划 |
| `test_g4_breaker.py` | G4 断路器 |
| `test_g4_cli.py` | G4 CLI |
| `test_g4_schema.py` | G4 Schema |
| `test_g5_cli.py` | G5 CLI |
| `test_g5_gate.py` | G5 门禁 |
| `test_g5_offline.py` | G5 离线 |
| `test_g6_ai_flavor.py` | G6 AI 味 |
| `test_g6_cli.py` | G6 CLI |
| `test_g6_golden_three.py` | G6 黄金三章 |
| `test_g6_padding.py` | G6 填充 |
| `test_g7_cost.py` | G7 成本 |
| `test_g7_human_summary.py` | G7 人工摘要 |
| `test_g7_preview.py` | G7 预览 |
| `test_g8_ending.py` | G8 结局 |
| `test_g8_gate.py` | G8 门禁 |
| `test_g8_mainline.py` | G8 主线 |
| `test_g9_failure.py` | G9 失败处理 |
| `test_g9_progress.py` | G9 进度 |
| `test_g9_stream.py` | G9 流 |
| `test_g10_ai_gate_default.py` | G10 AI 门禁默认 |
| `test_g10_cost_plan.py` | G10 成本计划 |
| `test_g10_cost_view.py` | G10 成本视图 |
| `test_g10_downgrade.py` | G10 降级 |
| `test_g11_export.py` | G11 导出 |
| `test_g11_method.py` | G11 方法 |
| `test_g11_style.py` | G11 风格 |
| `test_g12_emotion.py` | G12 情绪 |
| `test_g12_feedback.py` | G12 反馈 |
| `test_g12_payoff.py` | G12 爽点 |
| `test_boundary_invariants.py` | 边界不变性 |
| `test_cli_json.py` | CLI JSON |
| `test_confirmation.py` | 架构确认 |
| `test_dashboard.py` | 仪表盘 |
| `test_doctor.py` | 诊断器 |
| `test_genre_multigenre_compat.py` | 多题材兼容 |
| `test_hook_dispatcher.py` | Hook 分发器 |
| `test_learn.py` | 学习 |
| `test_learning_imitation.py` | 学习模仿 |
| `test_pipeline_bootstrap.py` | 流水线引导 |
| `test_pipeline_rollback_targeted.py` | 流水线定向回滚 |
| `test_prompt_manager_phase_c.py` | Prompt 管理器 Phase C |
| `conftest.py` | 共享 fixtures |

## 运行方式

```bash
# 全量运行
python -m pytest tests/ -q --tb=short

# 指定测试文件
python -m pytest tests/test_m1_config.py -q --tb=short
```