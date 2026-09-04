# story/meta — 元设计子包

## 职责

**元层面的设计资料**：产品哲学文案、世界观结构定义、写作方法论。

将这些元数据从 `story/` 主包拆分出来，降低 `story/` 主包的聚合度，提高内聚性。

| 模块 | 职责 |
|------|------|
| `philosophy.py` | 产品设计理念文案（TAGLINE/OPENING/POSITIONING/PILLARS/CLOSING） |
| `worldbuilding_schema.py` | 冰山设定结构定义（IcebergField/icebergDimension/IcebergGroup） |

## 依赖规则

- 依赖：仅 `base/`
- 不依赖其他 `story/` 子包
- 不被其他 `story/` 子包依赖

## 分层定位

`story/meta` → `story/setting` → `story/narrative` → `story/analysis`

所有子包依然在 `story/` 下（同领域），只是按功能进一步内聚拆分。
