"""评审/反馈意见结构化——"Raw AI review text must not become a model instruction"
（竞品差距改进计划 P1-6，对齐 AI-Novel-Writer v0.9.0 人工确认闭环）。

问题：自由语言的评审/反馈文本（用户输入的、或 ``evaluate``/``review_book`` 产出的
原始 AI 评审）直接拼进改写 prompt，会把评审语气、无关抱怨甚至评审文本里的幻觉
"事实"注入下一轮生成，污染改写方向。

方案：改写前先做**确定性结构化抽取**——把反馈拆成问题清单（位置 / 类型 / 修改指令），
渲染成标准"修改指令"块再入 prompt；结构化结果可先交用户确认（``FeedbackRewriter``
的 ``confirm_fn``），确认后才发起改写调用。

纯规则、零 LLM、零网络；抽取不完美没关系——目标是去掉注入风险并给用户可确认的清单。
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, field_validator

# 反馈类型关键词（按序匹配，先命中先归类）
_TYPE_RULES: tuple[tuple[str, str], ...] = (
    (r"拖|水|冗|节奏|太快|太慢|紧凑", "节奏"),
    (r"逻辑|动机|矛盾|不合理|前后不一|bug|硬伤", "逻辑"),
    (r"人设|性格|ooc|崩坏|不符|立不住", "人设"),
    (r"感情|情感|cp|恋爱|亲密", "情感"),
    (r"文风|文笔|描写|ai味|机器味|生硬", "文风"),
    (r"爽|高潮|锚点|打脸|期待", "爽点"),
    (r"对话|台词|语言指纹|口头禅", "对话"),
)

# 位置关键词
_POSITION_RULES: tuple[tuple[str, str], ...] = (
    (r"开头|开篇|章首|前500|前三百", "章首"),
    (r"结尾|末尾|章尾|收尾|最后", "章尾"),
    (r"中段|中间", "中段"),
    (r"对话|台词", "对话段"),
)

_DEFAULT_TYPE = "其他"
_DEFAULT_POSITION = "整章"
_GOAL_WIDTH = 60


class RewriteIssue(BaseModel):
    """单条修改指令（位置 + 类型 + 要做什么）。"""

    model_config = ConfigDict(extra="forbid")

    position: str = _DEFAULT_POSITION
    type: str = _DEFAULT_TYPE
    instruction: str

    @field_validator("instruction")
    @classmethod
    def _nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("instruction 不可为空")
        return v.strip()


class RewriteInstruction(BaseModel):
    """一章的结构化修改指令（确认后再入 prompt）。"""

    model_config = ConfigDict(extra="forbid")

    chapter: int
    goal: str                    # 一句话改写目标
    issues: list[RewriteIssue] = []
    keep: list[str] = []         # 必须保留的锚点（如伏笔/钩子/衔接句）
    raw_feedback: str = ""       # 原始反馈留档（不入 prompt）

    @field_validator("chapter")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("chapter 必须为正整数")
        return v


def _split_clauses(feedback: str) -> list[str]:
    """把自由语言反馈拆成待分类子句（换行 / 句号 / 分号边界）。"""
    parts = re.split(r"[\n。；;]+", feedback)
    return [p.strip() for p in parts if p.strip()]


def _classify(clause: str, rules: tuple[tuple[str, str], ...], default: str) -> str:
    for pattern, label in rules:
        if re.search(pattern, clause, re.IGNORECASE):
            return label
    return default


def structure_feedback(feedback: str, chapter: int) -> RewriteInstruction:
    """确定性结构化：自由反馈 → 问题清单（位置/类型/修改指令）。

    每个非空子句生成一条 issue；无法归类归入"其他/整章"。
    """
    issues: list[RewriteIssue] = []
    clauses = _split_clauses(feedback)
    for clause in clauses[:10]:  # 防极端长反馈撑爆清单
        issues.append(
            RewriteIssue(
                position=_classify(clause, _POSITION_RULES, _DEFAULT_POSITION),
                type=_classify(clause, _TYPE_RULES, _DEFAULT_TYPE),
                instruction=clause,
            )
        )
    goal = clauses[0][:_GOAL_WIDTH] if clauses else "按反馈定向重写"
    return RewriteInstruction(chapter=chapter, goal=goal, issues=issues, raw_feedback=feedback)


def render_instruction(inst: RewriteInstruction) -> str:
    """渲染为入 prompt 的标准"修改指令"块（替代原始反馈注入）。"""
    lines: list[str] = [f"【结构化修改指令（第 {inst.chapter} 章）】", f"改写目标：{inst.goal}"]
    if inst.issues:
        lines.append("问题清单（逐条命中，未提及的内容一律保留原样）：")
        for i, issue in enumerate(inst.issues, 1):
            lines.append(f"  {i}. [{issue.type}|{issue.position}] {issue.instruction}")
    else:
        lines.append("（反馈无可拆解子句，按改写目标整体处理）")
    if inst.keep:
        lines.append("必须保留：" + "；".join(inst.keep))
    return "\n".join(lines)


__all__ = [
    "RewriteIssue",
    "RewriteInstruction",
    "structure_feedback",
    "render_instruction",
]
