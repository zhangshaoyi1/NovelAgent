# anti_ai/ — AI 味检测与压制

## 职责
检测并压制 AI 生成文本的"AI 味"，提升小说自然度。

## 包含文件
| 文件 | 职责 |
|------|------|
| `detector.py` | AI 味检测器（AILikenessDetector） |
| `post_processor.py` | 后处理器（PostProcessor - 文本去 AI 味） |

## 依赖规则
- 依赖 base/

## 被依赖
- 写作后处理流程