"""A 系列：各阶段 Agent 引导式问答模板（静态数据）

为「设定世界 / 脉络讨论 / 故事架构 / 创作大纲 / 角色设计」5 个内容阶段定义
「挨个询问 → 选项选择 → 一键跳过（用默认值）→ 末轮补充」的问答模板。

结构：stage_key -> {
    "title": 阶段名,
    "questions": [{
        "key": 短中文名词（注入 prompt 时作为标签，如 "故事倾向"）,
        "question": 提问文案,
        "options": [{"value": 值, "label": 展示文案}],
        "default": 跳过时采用的值,
        "default_label": 跳过时展示的文案,
    }],
}

前端面板收集 answers = {key: 选中 label}，skipped = {key: bool}（true=跳过用默认），
supplementary = 末轮补充文本；序列化后由 Web 层保存到 .state/qa/{stage}.json，
生成工作流读取并注入 prompt（见 workflows/qa_sync.py）。
"""

QA_TEMPLATES: dict[str, dict] = {
    "world": {
        "title": "设定世界",
        "questions": [
            {
                "key": "故事倾向",
                "question": "故事整体更偏向哪种倾向？",
                "options": [
                    {"value": "逆袭", "label": "逆袭复仇"},
                    {"value": "成长", "label": "成长历练"},
                    {"value": "探秘", "label": "探秘解谜"},
                    {"value": "群像", "label": "群像史诗"},
                    {"value": "日常", "label": "轻松日常"},
                ],
                "default": "成长",
                "default_label": "成长历练",
            },
            {
                "key": "主角人设",
                "question": "主角的初始人设更接近哪类？",
                "options": [
                    {"value": "天骄", "label": "天之骄子"},
                    {"value": "废柴", "label": "废柴逆袭"},
                    {"value": "平凡", "label": "普通少年"},
                    {"value": "重生", "label": "重生者"},
                    {"value": "穿越", "label": "穿越者"},
                ],
                "default": "平凡",
                "default_label": "普通少年",
            },
            {
                "key": "金手指偏好",
                "question": "金手指（特殊能力）更偏好哪种？",
                "options": [
                    {"value": "传承", "label": "神秘传承"},
                    {"value": "系统", "label": "系统流"},
                    {"value": "体质", "label": "天赋体质"},
                    {"value": "法宝", "label": "外挂法宝"},
                    {"value": "硬实力", "label": "无金手指，靠硬实力"},
                ],
                "default": "传承",
                "default_label": "神秘传承",
            },
            {
                "key": "世界氛围",
                "question": "世界观整体氛围偏好？",
                "options": [
                    {"value": "光明", "label": "光明正义"},
                    {"value": "灰色", "label": "灰色现实"},
                    {"value": "暗黑", "label": "暗黑残酷"},
                    {"value": "诙谐", "label": "轻松诙谐"},
                ],
                "default": "光明",
                "default_label": "光明正义",
            },
        ],
    },
    "discussion": {
        "title": "脉络讨论",
        "questions": [
            {
                "key": "主线聚焦",
                "question": "主线想重点讲什么？",
                "options": [
                    {"value": "升级", "label": "升级打怪"},
                    {"value": "权谋", "label": "权谋争斗"},
                    {"value": "情感", "label": "情感羁绊"},
                    {"value": "家国", "label": "家国天下"},
                    {"value": "探索", "label": "探索未知"},
                ],
                "default": "升级",
                "default_label": "升级打怪",
            },
            {
                "key": "成长弧光",
                "question": "主角的成长弧光更偏好哪种？",
                "options": [
                    {"value": "巅峰", "label": "从弱小到巅峰"},
                    {"value": "觉醒", "label": "从迷失到觉醒"},
                    {"value": "担当", "label": "从自私到担当"},
                ],
                "default": "巅峰",
                "default_label": "从弱小到巅峰",
            },
            {
                "key": "故事节奏",
                "question": "故事节奏偏好？",
                "options": [
                    {"value": "爽快", "label": "爽快紧凑"},
                    {"value": "张弛", "label": "张弛有度"},
                    {"value": "慢热", "label": "慢热细腻"},
                ],
                "default": "爽快",
                "default_label": "爽快紧凑",
            },
            {
                "key": "支线偏好",
                "question": "更希望突出哪类支线？",
                "options": [
                    {"value": "感情", "label": "感情线"},
                    {"value": "兄弟", "label": "兄弟情"},
                    {"value": "恩怨", "label": "恩怨线"},
                    {"value": "探索", "label": "探索线"},
                ],
                "default": "感情",
                "default_label": "感情线",
            },
        ],
    },
    "architecture": {
        "title": "故事架构",
        "questions": [
            {
                "key": "结局走向",
                "question": "预期结局走向？",
                "options": [
                    {"value": "圆满", "label": "圆满收束"},
                    {"value": "开放", "label": "开放式结局"},
                    {"value": "悲剧", "label": "悲剧收场"},
                    {"value": "反转", "label": "意料之外的反转"},
                ],
                "default": "圆满",
                "default_label": "圆满收束",
            },
            {
                "key": "主线复杂度",
                "question": "主线复杂程度偏好？",
                "options": [
                    {"value": "单线", "label": "单主线聚焦"},
                    {"value": "双线", "label": "双线并行"},
                    {"value": "多线", "label": "多线交织"},
                ],
                "default": "单线",
                "default_label": "单主线聚焦",
            },
            {
                "key": "情感基调",
                "question": "整本书的情感基调？",
                "options": [
                    {"value": "热血", "label": "热血激昂"},
                    {"value": "治愈", "label": "温暖治愈"},
                    {"value": "沉重", "label": "深沉厚重"},
                    {"value": "诙谐", "label": "轻松诙谐"},
                ],
                "default": "热血",
                "default_label": "热血激昂",
            },
            {
                "key": "反派塑造",
                "question": "反派的塑造方式偏好？",
                "options": [
                    {"value": "传统", "label": "传统恶人"},
                    {"value": "悲情", "label": "复杂悲情"},
                    {"value": "亦正", "label": "亦正亦邪"},
                    {"value": "无反派", "label": "无明确反派，以环境为敌"},
                ],
                "default": "亦正",
                "default_label": "亦正亦邪",
            },
        ],
    },
    "outline": {
        "title": "创作大纲",
        "questions": [
            {
                "key": "篇幅分配",
                "question": "篇幅分布更倾向哪种？",
                "options": [
                    {"value": "平均", "label": "平均分配"},
                    {"value": "前重", "label": "前期铺垫多"},
                    {"value": "中重", "label": "中期高潮多"},
                    {"value": "后重", "label": "后期收尾快"},
                ],
                "default": "中重",
                "default_label": "中期高潮多",
            },
            {
                "key": "高潮安排",
                "question": "高潮节点的安排方式？",
                "options": [
                    {"value": "三幕", "label": "三幕式"},
                    {"value": "多节点", "label": "多节点递进"},
                    {"value": "递进", "label": "一路走高式"},
                ],
                "default": "三幕",
                "default_label": "三幕式",
            },
            {
                "key": "支线数量",
                "question": "支线数量偏好？",
                "options": [
                    {"value": "精简", "label": "精简 1-2 条"},
                    {"value": "适中", "label": "适中 3-5 条"},
                    {"value": "丰富", "label": "丰富 5 条以上"},
                ],
                "default": "适中",
                "default_label": "适中 3-5 条",
            },
            {
                "key": "结尾处理",
                "question": "结尾处理方式偏好？",
                "options": [
                    {"value": "留白", "label": "留白想象"},
                    {"value": "团圆", "label": "大团圆"},
                    {"value": "续作", "label": "开放续作"},
                ],
                "default": "团圆",
                "default_label": "大团圆",
            },
        ],
    },
    "characters": {
        "title": "角色设计",
        "questions": [
            {
                "key": "主角性格",
                "question": "主角的主要性格特质？",
                "options": [
                    {"value": "沉稳", "label": "沉稳冷静"},
                    {"value": "热血", "label": "热血直率"},
                    {"value": "腹黑", "label": "腹黑谋略"},
                    {"value": "乐观", "label": "乐观开朗"},
                ],
                "default": "热血",
                "default_label": "热血直率",
            },
            {
                "key": "配角侧重",
                "question": "配角阵容更侧重哪类？",
                "options": [
                    {"value": "亦敌", "label": "亦敌亦友"},
                    {"value": "伙伴", "label": "忠诚伙伴"},
                    {"value": "对手", "label": "成长对手"},
                    {"value": "宿敌", "label": "宿命之敌"},
                ],
                "default": "伙伴",
                "default_label": "忠诚伙伴",
            },
            {
                "key": "角色成长",
                "question": "角色成长方式偏好？",
                "options": [
                    {"value": "渐进", "label": "渐进式成长"},
                    {"value": "突变", "label": "关键事件突变"},
                ],
                "default": "渐进",
                "default_label": "渐进式成长",
            },
            {
                "key": "感情线",
                "question": "感情线安排偏好？",
                "options": [
                    {"value": "单女主", "label": "单女主"},
                    {"value": "无CP", "label": "无 CP，专注事业"},
                    {"value": "多线", "label": "多线暧昧"},
                    {"value": "群像", "label": "群像情感"},
                ],
                "default": "单女主",
                "default_label": "单女主",
            },
        ],
    },
}


def get_qa_template(stage_key: str) -> dict | None:
    """按阶段 key 取模板（不存在返回 None）。"""
    return QA_TEMPLATES.get(stage_key)
