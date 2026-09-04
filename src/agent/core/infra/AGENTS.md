# AGENTS.md - core/infra/ 基础设施层

## 职责

提供跨领域的通用基础设施，供所有上层包使用。

## 核心模块

| 文件 | 导出 | 作用 |
|------|------|------|
| `context.py` | `ContextEngine`, `ContextItem` | 上下文工程（重要性加权/压缩/预算裁剪/Prompt Caching） |
| `context_loader.py` | `ContextLoader`, `LoadedContext` | 上下文加载器（按场景智能加载设定，控制 token 用量） |
| `compose_runner.py` | `run_compose`, `resolve_project_dir` | 一键写书编排器 |
| `dashboard_aggregator.py` | `DashboardAggregator` | 仪表盘聚合器（写作进度数据） |
| `hook_dispatcher.py` | `dispatch_genre_hooks` | Hook 分发器（写作流程中的 Hook 机制） |
| `doctor.py` | `Doctor` | 诊断器（项目问题诊断） |

## 依赖规则

- 依赖 base、client
- 可依赖 story 的部分模块
- 冲突仲裁已归位 quality/consistency/，避免 quality ↔ infra 循环依赖