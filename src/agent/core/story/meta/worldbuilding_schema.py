"""建书冰山 Schema（对标笔枢 Novelbuilt「冰山理论」60+ 设定字段）

水面之上是读者看到的故事；水面之下，是一个在动笔之前就已完整运转的世界。
本模块把「建书深度」结构化，供 ``iceberg`` 命令生成建书骨架 / 校验覆盖度。

结构（共 60 字段）：
    - 世界观：6 一级维度 · 28 子维度
    - 角色系统：14 维模型
    - 故事引擎：3 层 · 18 字段
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class IcebergField:
    """单个设定字段"""

    name: str
    hint: str = ""


@dataclass
class IcebergDimension:
    """一级维度（含若干子字段）"""

    name: str
    fields: List[IcebergField] = field(default_factory=list)


@dataclass
class IcebergGroup:
    """建书分组（世界观 / 角色系统 / 故事引擎）"""

    key: str
    label: str
    metaphor: str
    dimensions: List[IcebergDimension] = field(default_factory=list)

    @property
    def primary_count(self) -> int:
        return len(self.dimensions)

    @property
    def field_count(self) -> int:
        return sum(len(d.fields) for d in self.dimensions)


# ============================================================
# 世界观：6 一级 · 28 子维度
# ============================================================
_WORLDVIEW = IcebergGroup(
    key="worldview",
    label="世界观",
    metaphor="水面之下的地基：规则、版图与文明。",
    dimensions=[
        IcebergDimension("核心法则", [
            IcebergField("力量体系", "力量从何而来、如何运转、上限与代价"),
            IcebergField("世界公理", "这条世界不可违背的底层规律"),
            IcebergField("禁忌红线", "触碰即引发后果的绝对边界"),
            IcebergField("力量感官表现", "力量外显时的视听体感"),
        ]),
        IcebergDimension("时空地理", [
            IcebergField("世界格局", "大陆/星域/位面的总体版图"),
            IcebergField("关键地点", "推动剧情的核心场景与据点"),
            IcebergField("生态资源", "珍稀物产与战略资源分布"),
            IcebergField("时代阶段", "故事所处的历史断代与科技/灵气水平"),
            IcebergField("环境质感", "气候、地貌、光线的整体氛围"),
        ]),
        IcebergDimension("社会权力", [
            IcebergField("种族", "智慧族群及其天赋差异"),
            IcebergField("阶层结构", "阶级流动与固化机制"),
            IcebergField("政治体制", "政权形态与权力继承"),
            IcebergField("势力", "门派/家族/国家等权力实体"),
            IcebergField("势力关系", "同盟/敌对/依附的博弈图谱"),
            IcebergField("权力感知", "角色对权力的体感与渴望"),
        ]),
        IcebergDimension("历史文化", [
            IcebergField("重大事件", "塑造当下的历史转折点"),
            IcebergField("宗教", "信仰体系与教义冲突"),
            IcebergField("风俗习惯", "节庆、礼仪、禁忌日常"),
            IcebergField("经济体系", "货币、贸易与财富逻辑"),
            IcebergField("日常切片", "普通人生活的真实质感"),
        ]),
        IcebergDimension("存在基础", [
            IcebergField("历法", "时间计量与节气节点"),
            IcebergField("寿命", "种族寿命与衰老规则"),
            IcebergField("死亡", "死后世界与灵魂设定"),
            IcebergField("疾病与繁衍", "生老病死的生理逻辑"),
        ]),
        IcebergDimension("信息传播", [
            IcebergField("信息速度", "消息传递的快慢与渠道"),
            IcebergField("知识载体", "典籍/口述/秘传的存储方式"),
            IcebergField("信息壁垒", "谁掌握信息、谁被蒙蔽"),
            IcebergField("流言与真相", "谣言如何塑造局势"),
        ]),
    ],
)

# ============================================================
# 角色系统：14 维模型
# ============================================================
_CHARACTER = IcebergGroup(
    key="character",
    label="角色系统",
    metaphor="阵容级 + 逐角色 · 让每个角色都鲜活立体。",
    dimensions=[
        IcebergDimension("身份与定位（4）", [
            IcebergField("基础信息", "身份/势力/境界/出场阶段"),
            IcebergField("关系网定位", "在关系图中的角色与连线"),
            IcebergField("外貌特征", "易辨识的视觉锚点"),
            IcebergField("能力/技能", "可成长的能力边界"),
        ]),
        IcebergDimension("内核与弧光（4）", [
            IcebergField("动机", "表层目标 / 深层目标 / 秘密"),
            IcebergField("成长弧光", "从起点到终点的内在转变"),
            IcebergField("价值观", "驱动抉择的底层信念"),
            IcebergField("恐惧/弱点", "可被攻破的软肋"),
        ]),
        IcebergDimension("叙事工具（4）", [
            IcebergField("语言指纹", "口头禅 / 句式偏好 / 用词习惯 / 禁用词"),
            IcebergField("性格特质", "稳定可辨的行为模式"),
            IcebergField("关键经历", "塑造其人的过去事件"),
            IcebergField("情感羁绊", "牵动其人的人与事"),
        ]),
        IcebergDimension("质检维度（2）", [
            IcebergField("反派动机合理性", "降智检测：动机是否站得住"),
            IcebergField("角色弧线节点", "转变发生的关键章节"),
        ]),
    ],
)

# ============================================================
# 故事引擎：3 层 · 18 字段
# ============================================================
_STORY_ENGINE = IcebergGroup(
    key="story_engine",
    label="故事引擎",
    metaphor="从一句创意到一章成稿的工序链。",
    dimensions=[
        IcebergDimension("立意定调（6）", [
            IcebergField("题材", "类型与套路选择"),
            IcebergField("基调", "悲/喜/暗黑/轻松"),
            IcebergField("核心冲突", "贯穿全书的主矛盾"),
            IcebergField("主题母题", "想表达的深层命题"),
            IcebergField("目标读者", "为谁而写"),
            IcebergField("情感内核", "希望读者带走的情绪"),
        ]),
        IcebergDimension("推演布局（7）", [
            IcebergField("整卷脉络", "大节奏与高潮分布"),
            IcebergField("章节走向草案", "每章的推演目标"),
            IcebergField("爽点分布", "付费/追更钩子排布"),
            IcebergField("悬念铺设", "开场重演与章末钩子"),
            IcebergField("伏笔台账", "埋设与回收登记"),
            IcebergField("节奏曲线", "铺垫→冲突→高潮→舒缓"),
            IcebergField("冲突节点", "转折与反转的发生点"),
        ]),
        IcebergDimension("编排审定（5）", [
            IcebergField("分卷大纲", "卷级结构"),
            IcebergField("章纲", "章节细纲"),
            IcebergField("人物弧线编排", "多角色弧光交织"),
            IcebergField("结局收敛模式", "主线如何收束"),
            IcebergField("主线支线配比", "主线与支线的篇幅权重"),
        ]),
    ],
)

WORLDBUILDING_ICEBERG: List[IcebergGroup] = [_WORLDVIEW, _CHARACTER, _STORY_ENGINE]


def get_iceberg() -> List[IcebergGroup]:
    return list(WORLDBUILDING_ICEBERG)


def total_fields() -> int:
    return sum(g.field_count for g in WORLDBUILDING_ICEBERG)


def summary() -> str:
    parts = " · ".join(f"{g.label} {g.primary_count}一级/{g.field_count}字段" for g in WORLDBUILDING_ICEBERG)
    return f"建书冰山 · 共 {total_fields()} 设定字段（{parts}）"
