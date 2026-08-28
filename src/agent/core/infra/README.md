# infra/ — 基础设施层

## 职责
提供上下文工程、仪表盘聚合等跨层基础设施服务。

## 包含文件
| 文件 | 职责 |
|------|------|
| `compose_runner.py` | 一键编排运行器（run_compose, resolve_project_dir；G14 去重扫描经回调注入） |
| `context.py` | 写作上下文定义（WritingContext） |
| `context_loader.py` | 上下文加载器（ContextLoader - 7 步上下文加载） |
| `dashboard_aggregator.py` | 仪表盘聚合器（DashboardAggregator） |
| `doctor.py` | 项目健康诊断（ProjectDoctor, DiagnosisResult） |
| `hook_dispatcher.py` | 钩子分发器（HookDispatcher） |

## 依赖规则
- 依赖 base/、client/、engine/（doctor 诊断状态）、story/（setting_manager）
- 不依赖 quality/（冲突仲裁已归位 quality/conflict_service.py；G14 扫描经依赖注入触发）

## 被依赖
- workflows/ (M5 写章、agentic_pipeline)
- cli/ (命令)