# AGENTS.md - core/anti_ai/ AI 味检测与压制

## 职责

AI 味检测与压制流水线，消除 AI 生成文本的典型特征。

## 核心模块

| 文件 | 导出 | 作用 |
|------|------|------|
| `detector.py` | `AILikenessDetector`, `LexicalChecker`, `SyntacticChecker`, `SemanticChecker`, `StatisticalChecker`, `DetectionResult`, `AIFlavorScanner`, `AIFlavorReport` | AI 味检测器 |
| `post_processor.py` | `PostProcessor`, `StylisticNoiseInjector`, `DialogueDifferentiator`, `AIismCleaner`, `ProcessingResult` | 后处理器 |
| `rewriter.py` | `DeslopRewriter`, `DeslopResult` | 去模板化重写器 |

## 依赖规则

- 依赖 base、client、story
- 通过延迟导入避免循环依赖