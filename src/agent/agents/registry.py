"""Agent 阵容注册表（「编制完整创作团队」叙事）

把 NovelAgent 真实的引擎模块 / 工作流 / 质量护栏，包装成一支职责明确的
「专家 Agent 编制」——对标笔枢 Novelbuilt 的 30+ 具名专家 Agent 叙事。

设计要点：
    - 每个阵容成员都 **落地到真实代码模块**，不是虚构头衔（见 ``engine`` 字段）。
    - 四大编制分组与世界构建 / 情节叙事 / 成文润色 / 审校把关一一对应。
    - 对外既可在 CLI ``roster`` 命令以 Rich 表呈现，也可经 Web ``/api/roster`` 供给前端。

叙事母题（沿用竞品分析中「Agent 阵容叙事」可借鉴项）：
    "不是一个写手，而是一支编制完整的创作团队。你担任总编。"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List


class RosterCategory(str, Enum):
    """四大编制分组"""

    WORLD_BUILDING = "世界构建"
    PLOT_NARRATIVE = "情节叙事"
    WRITING_POLISH = "成文润色"
    REVIEW_GUARD = "审校把关"


@dataclass
class RosterAgent:
    """阵容成员（具名专家 Agent）"""

    id: str
    name: str                       # 中文具名，如「世界观架构师」
    glyph: str                      # 单字徽记，如「界」
    category: RosterCategory
    responsibility: str             # 职责描述
    engine: str                     # 真实代码模块 / 组件（落地依据）
    trait: str = ""                 # 人设气质（叙事用）


@dataclass
class RosterGroup:
    """编制分组"""

    category: RosterCategory
    tagline: str
    agents: List[RosterAgent] = field(default_factory=list)


# ============================================================
# 阵容定义（全部落地到真实引擎）
# ============================================================
def _build_registry() -> List[RosterAgent]:
    return [
        # ---------- 世界构建 ----------
        RosterAgent("worldbuilder", "世界观架构师", "界", RosterCategory.WORLD_BUILDING,
                    "设计宇宙规则、力量体系与历史脉络，搭建可计算、可追溯的世界。",
                    "agents.planner_agent / core.setting_manager", "沉稳的造物主"),
        RosterAgent("cartographer", "地理志官", "舆", RosterCategory.WORLD_BUILDING,
                    "构建版图、势力分布与地点风物，构成剧情冲突的稳定底座。",
                    "core.setting_manager", "默记山河的图匠"),
        RosterAgent("character-smith", "人物设定师", "角", RosterCategory.WORLD_BUILDING,
                    "塑造立体人设、动机与成长弧线，杜绝人设崩塌。",
                    "workflows.m4_character", "捏塑灵魂的匠人"),
        RosterAgent("lore-keeper", "设定考据员", "考", RosterCategory.WORLD_BUILDING,
                    "守护设定自洽，比对世界状态机，揪出前后矛盾。",
                    "core.consistency_checker", "较真的史官"),
        RosterAgent("power-architect", "力量体系师", "力", RosterCategory.WORLD_BUILDING,
                    "设计境界 / 金手指边界与禁忌红线，后期不失控。",
                    "core.setting_manager（world.md 冻结字段）", "定规矩的匠师"),
        RosterAgent("timeline-steward", "时间线管家", "时", RosterCategory.WORLD_BUILDING,
                    "维护事件先后、伏笔埋收节点，到点提醒，绝不烂尾。",
                    "core.pacing_store / core.snapshot_manager", "守时的更夫"),

        # ---------- 情节叙事 ----------
        RosterAgent("arc-planner", "卷纲策划", "卷", RosterCategory.PLOT_NARRATIVE,
                    "规划整卷脉络与高潮节奏，给出章节走向草案。",
                    "agents.planner_agent（MasterPlan.arcs）", "布局的棋手"),
        RosterAgent("chapter-plotter", "章节编排师", "章", RosterCategory.PLOT_NARRATIVE,
                    "拆解章节大纲，分配爽点与悬念。",
                    "workflows.m5_write_chapter", "排兵的参军"),
        RosterAgent("conflict-designer", "冲突设计师", "冲", RosterCategory.PLOT_NARRATIVE,
                    "制造张力、反转与利害交锋。",
                    "core.conflict_service", "点火的煽动者"),
        RosterAgent("foreshadow-steward", "伏笔管家", "伏", RosterCategory.PLOT_NARRATIVE,
                    "登记伏笔、追踪回收、防止烂尾。",
                    "core.foreshadow_manager", "埋线的园丁"),
        RosterAgent("pacing-director", "节奏调度官", "律", RosterCategory.PLOT_NARRATIVE,
                    "把控张弛快慢与情绪曲线。",
                    "core.pacing_store", "执棒的指挥"),
        RosterAgent("director", "导演 Agent", "导", RosterCategory.PLOT_NARRATIVE,
                    "在规则约束下编排叙事，保证「好不好看」；与冲突裁决协调走向。",
                    "core.agent_loop / workflows.m8_mode（ModeController）", "掌镜的导演"),
        RosterAgent("route-planner", "路线规划师", "路", RosterCategory.PLOT_NARRATIVE,
                    "动态调整主角路线，保留分支备选。",
                    "core.context_loader（protagonist_route）", "指路的驿丞"),

        # ---------- 成文润色 ----------
        RosterAgent("scene-writer", "场景执笔", "景", RosterCategory.WRITING_POLISH,
                    "将细纲落为画面感十足的正文。",
                    "core.writer_agent（Scene Writer）", "泼墨的画师"),
        RosterAgent("dialogue-artist", "对白师", "白", RosterCategory.WRITING_POLISH,
                    "打磨符合人设的对白与潜台词。",
                    "core.writer_agent（Dialogue Artist）", "听声的伶人"),
        RosterAgent("voice-stylist", "文风塑形师", "风", RosterCategory.WRITING_POLISH,
                    "统一并强化作品独有的叙述声音。",
                    "core.method_style / world.md 风格配置", "定调的乐师"),
        RosterAgent("line-polisher", "金句锤炼师", "句", RosterCategory.WRITING_POLISH,
                    "锤炼标题、章末钩子与名场面金句。",
                    "core.writer_agent（Line Polisher）", "琢玉的锻工"),
        RosterAgent("continuity-writer", "连贯性施工", "续", RosterCategory.WRITING_POLISH,
                    "按分镜精确施工成文，注入上一章结尾摘要，避免重演。",
                    "core.writer_agent + context_loader", "接榫的泥瓦匠"),

        # ---------- 审校把关 ----------
        RosterAgent("continuity-checker", "连贯性审校", "校", RosterCategory.REVIEW_GUARD,
                    "比对世界状态，揪出前后矛盾。",
                    "core.consistency_checker", "吹毛的御史"),
        RosterAgent("logic-editor", "逻辑审读员", "逻", RosterCategory.REVIEW_GUARD,
                    "排查情节漏洞与动机断裂。",
                    "core.quality_checker", "较真的编辑"),
        RosterAgent("reader-advocate", "读者体验官", "读", RosterCategory.REVIEW_GUARD,
                    "以读者视角预判爽感与弃书点。",
                    "core.reader_appeal", "挑刺的看客"),
        RosterAgent("novel-evaluator", "不崩终审", "评", RosterCategory.REVIEW_GUARD,
                    "全书「不崩」终审 + 自动回溯修复（多维评测）。",
                    "agents.evaluator_agent", "终审的判官"),
        RosterAgent("guardrail-inspector", "护栏巡检", "栏", RosterCategory.REVIEW_GUARD,
                    "七维硬门禁 + 去 AI 味规则，不达标默认 block 拒落盘。",
                    "core.guardrails", "守门的铁卫"),
        RosterAgent("budget-controller", "成本调度官", "费", RosterCategory.REVIEW_GUARD,
                    "写前预估、写中已花 / 剩余 / ETA，超预算自动降档。",
                    "core.budget_plan", "算账的司库"),
        RosterAgent("repair-agent", "质量回溯", "溯", RosterCategory.REVIEW_GUARD,
                    "质量未达标时自动回溯重写，结构问题 escalated。",
                    "agents.evaluator_agent（RepairPlan）", "补漏的医匠"),
    ]


AGENT_REGISTRY: List[RosterAgent] = _build_registry()

ROSTER_NARRATIVE = (
    "不是一个写手，而是一支编制完整的创作团队。\n"
    "从世界观架构到金句润色，每一道工序都有专精的 Agent 负责；\n"
    "它们围绕同一个世界状态协同工作，由你担任总编。\n"
    "世界构建 · 情节叙事 · 成文润色 · 审校把关 —— 四组齐备，各司其职。"
)


def get_roster() -> List[RosterAgent]:
    """返回完整阵容"""
    return list(AGENT_REGISTRY)


def get_roster_by_category(category: RosterCategory) -> List[RosterAgent]:
    """按分组返回阵容成员"""
    return [a for a in AGENT_REGISTRY if a.category == category]


def get_groups() -> List[RosterGroup]:
    """返回分组后的阵容（含每组 tagline）"""
    taglines = {
        RosterCategory.WORLD_BUILDING: "先建立一个可计算、可追溯的世界，再让文字从中生长。",
        RosterCategory.PLOT_NARRATIVE: "推演布局、编排审定，让剧情在规则约束下自然涌现。",
        RosterCategory.WRITING_POLISH: "多位写手协作落笔、校验一致性，按你的批注循环重写。",
        RosterCategory.REVIEW_GUARD: "把关设定与伏笔，不达标自动拦截 + 回溯，质量系统兜底。",
    }
    groups: List[RosterGroup] = []
    for cat in RosterCategory:
        agents = get_roster_by_category(cat)
        if agents:
            groups.append(RosterGroup(category=cat, tagline=taglines[cat], agents=agents))
    return groups


def roster_summary() -> str:
    """一句话阵容规模概览"""
    counts = {cat: len(get_roster_by_category(cat)) for cat in RosterCategory}
    total = len(AGENT_REGISTRY)
    return (
        f"编制完整创作团队 · 共 {total} 位专家 Agent："
        f"世界构建 {counts[RosterCategory.WORLD_BUILDING]} · "
        f"情节叙事 {counts[RosterCategory.PLOT_NARRATIVE]} · "
        f"成文润色 {counts[RosterCategory.WRITING_POLISH]} · "
        f"审校把关 {counts[RosterCategory.REVIEW_GUARD]}"
    )
