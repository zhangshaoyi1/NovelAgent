"""PlannerAgent —— 架构师（Phase 2 多智能体团队之一）

职责：把用户思路（题材 / 核心梗 / 风格 / 体量）展开为 **Master Plan（结构化）**：
剧集树（Arc）+ 角色骨架 + 伏笔规划 + 质量目标（七维"不崩"合格线）。
写作过程中可据进度 ``revise_plan`` 修订计划。

与设计文档 §2.3 对应：``Planner`` 产出任务流 → ``Writer`` 写 → ``Critic``+``Editor``
并联审查 → ``Evaluator`` 终审。本 Agent 是"任务流"的生产者。

实现说明：
- 这是**结构化输出 Agent**（单次决策产出完整计划），而非 ReAct 循环——
  Planner 不需要在工具循环里反复试探，故直接复用 ``chat_structured(MasterPlan)``。
- ``decide`` / ``decide_async`` 可注入（离线测试用）；生产环境包 ``LLMClient``。
- 计划落 ``<project>/.state/plan.json``，并把角色/世界观/质量目标回写 Memory，
  供 Writer / Editor / Evaluator 取用。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from pydantic import BaseModel, Field
from rich.console import Console

from agent.core.llm_client import LLMClient
from agent.core.setting_manager import SettingManager


# ============================================================
# Master Plan 结构（可被 chat_structured 强制的结构化输出）
# ============================================================
class QualityTargets(BaseModel):
    """七维"不崩"合格线（对应设计文档 §1.2；标 not_relaxable 的不可放宽）。"""

    character_stability_high: int = Field(default=0, description="人设硬伤高严重度数，=0 不可放宽")
    setting_consistency_high: int = Field(default=0, description="设定一致性高严重度冲突，=0 不可放宽")
    foreshadow_recycle_rate: float = Field(default=0.90, description="伏笔回收率下限，默认 90%")
    coherence: float = Field(default=80.0, description="连贯性自评下限（/100）")
    readability: float = Field(default=75.0, description="追读力综合评分下限（/100）")
    pacing_abnormal: float = Field(default=0.03, description="异常章节（注水/赶进度）比例上限")
    logic_holes: int = Field(default=0, description="逻辑漏洞（死亡复活/道具凭空），=0 不可放宽")


class CharacterSketch(BaseModel):
    name: str
    role: str = ""          # 主角/反派/配角/导师...
    faction: str = ""
    realm: str = ""
    arc: str = ""           # 角色弧光简述
    fingerprint: str = ""   # 语言指纹（台词风格）


class Arc(BaseModel):
    id: str
    name: str
    chapter_start: int
    chapter_end: int
    goal: str = ""
    subline_id: str = ""


class PlannedForeshadow(BaseModel):
    id: str
    content: str
    plant_at_est: int = 0       # 预计埋设点（章节）
    expected_resolve_est: int = 0  # 预计回收点（章节）
    related_characters: list[str] = Field(default_factory=list)


class MasterPlan(BaseModel):
    brief: str = ""
    genre: str = "xiuxian"
    title: str = ""
    total_chapters: int = 100
    episode_tree: list[Arc] = Field(default_factory=list)
    character_skeleton: list[CharacterSketch] = Field(default_factory=list)
    foreshadow_plan: list[PlannedForeshadow] = Field(default_factory=list)
    quality_targets: QualityTargets = Field(default_factory=QualityTargets)
    notes: str = ""


# decide 签名：接收 messages，返回 MasterPlan 的 dict（或 MasterPlan 实例）。
PlanDecideFn = Callable[[list[dict[str, str]]], dict[str, Any]]
PlanDecideAsyncFn = Callable[[list[dict[str, str]]], Awaitable[dict[str, Any]]]

_PLANNER_SYSTEM = (
    "你是小说架构师（Planner）。根据用户的创作思路，产出一份结构化 Master Plan，\n"
    "严格按 JSON Schema 输出，字段包括：brief / genre / title / total_chapters /\n"
    "episode_tree（剧集树，每弧含章节区间与目标）/ character_skeleton（角色骨架）/\n"
    "foreshadow_plan（伏笔规划，含预计埋设与回收章节）/ quality_targets（七维不崩合格线）。\n"
    "若用户提供设定集上下文，请尊重其中的世界观/角色/支线，不要与之冲突。\n"
    "quality_targets 默认值：foreshadow_recycle_rate=0.90, coherence=80, readability=75,\n"
    "pacing_abnormal=0.03，其余硬指标（人设/设定硬伤、逻辑漏洞）必须为 0。"
)


class PlannerAgent:
    """架构师 Agent：产出并维护 Master Plan。

    Args:
        project_dir: 小说项目目录（用于读取设定集 + 落 plan.json + 写 Memory）。
        llm_client: LLM 客户端；不传则惰性创建。
        memory: 统一记忆层（可注入，默认按 project_dir 新建）。
        console: rich 控制台。
        decide / decide_async: 注入决策函数（离线测试用）。
    """

    def __init__(
        self,
        project_dir: str | Path,
        llm_client: LLMClient | None = None,
        memory: Any = None,
        console: Console | None = None,
        decide: PlanDecideFn | None = None,
        decide_async: PlanDecideAsyncFn | None = None,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.llm = llm_client
        self.console = console or Console()
        self.memory = memory
        self._decide = decide
        self._decide_async = decide_async
        self.plan_file = self.project_dir / ".state" / "plan.json"

    # ---------------------------------------------------------------- 上下文
    def _load_setting_context(self) -> str:
        """读取现有设定集（world/角色/支线）作为 Planner 的参考上下文。"""
        try:
            sm = SettingManager(self.project_dir)
        except Exception:  # noqa: BLE001 - 设定缺失不阻断规划
            return "（暂无设定集）"
        parts: list[str] = []
        world = sm.load_world()
        if world["exists"]:
            parts.append("【世界观摘要】\n" + world["content"][:1500])
        chars = sm.list_characters()
        if chars:
            parts.append("【已有角色】" + "、".join(chars))
        sublines = sm.list_sublines()
        if sublines:
            parts.append("【已有支线】" + "、".join(sublines))
        return "\n\n".join(parts) if parts else "（暂无设定集）"

    # ---------------------------------------------------------------- decide
    def _make_decide(self) -> PlanDecideFn:
        if self._decide is not None:
            return self._decide
        if self.llm is None:
            self.llm = LLMClient()
        llm = self.llm

        def decide(messages: list[dict[str, str]]) -> dict[str, Any]:
            data = llm.chat_structured(
                messages,
                MasterPlan,
                use="creative",
                temperature=0.7,
                max_tokens=4000,
                enable_thinking=False,
            )
            return data

        return decide

    async def _make_decide_async(self) -> PlanDecideAsyncFn:
        if self._decide_async is not None:
            return self._decide_async
        if self.llm is None:
            self.llm = LLMClient()
        llm = self.llm

        async def decide_async(messages: list[dict[str, str]]) -> dict[str, Any]:
            data = await llm.chat_structured_async(
                messages,
                MasterPlan,
                use="creative",
                temperature=0.7,
                max_tokens=4000,
                enable_thinking=False,
            )
            return data

        return decide_async

    # ---------------------------------------------------------------- 主入口
    def run(self, brief: str, ctx: Any = None) -> MasterPlan:
        """产出 Master Plan 并落盘 + 回写 Memory。

        Args:
            brief: 用户思路（题材/核心梗/风格/体量）。
            ctx: 可选额外上下文（忽略内部使用）。

        Returns:
            MasterPlan
        """
        setting_ctx = self._load_setting_context()
        user_msg = (
            f"【用户创作思路】\n{brief}\n\n"
            f"【现有设定集上下文（若有，请尊重）】\n{setting_ctx}\n\n"
            "请产出 Master Plan。"
        )
        decide = self._make_decide()
        try:
            data = decide(
                [
                    {"role": "system", "content": _PLANNER_SYSTEM},
                    {"role": "user", "content": user_msg},
                ]
            )
            # 优先严格构造；结构化输出若部分字段缺失/类型不符导致校验失败，
            # 退而求其次做「宽松重建」保留有效部分（修复 bug1：避免整体退化为空计划）。
            try:
                if isinstance(data, MasterPlan):
                    plan = data
                else:
                    plan = MasterPlan(**(data if isinstance(data, dict) else dict(data)))
            except Exception as ve:  # noqa: BLE001
                self.console.print(
                    f"[yellow]Planner 结构化输出字段校验不严，宽松重建（{ve}）[/yellow]"
                )
                plan = self._build_plan_lenient(data, brief)
        except Exception as e:  # noqa: BLE001 - 决策彻底失败才降级为空计划（仍落盘，不阻断）
            self.console.print(f"[yellow]Planner 决策失败（{e}），使用空计划[/yellow]")
            plan = MasterPlan(brief=brief)

        plan.brief = brief or plan.brief
        self._save(plan)
        self._write_memory(plan)
        return plan

    async def run_async(self, brief: str, ctx: Any = None) -> MasterPlan:
        setting_ctx = self._load_setting_context()
        user_msg = (
            f"【用户创作思路】\n{brief}\n\n"
            f"【现有设定集上下文】\n{setting_ctx}\n\n请产出 Master Plan。"
        )
        decide_async = await self._make_decide_async()
        try:
            data = await decide_async(
                [
                    {"role": "system", "content": _PLANNER_SYSTEM},
                    {"role": "user", "content": user_msg},
                ]
            )
            try:
                if isinstance(data, MasterPlan):
                    plan = data
                else:
                    plan = MasterPlan(**(data if isinstance(data, dict) else dict(data)))
            except Exception as ve:  # noqa: BLE001
                self.console.print(
                    f"[yellow]Planner 结构化输出字段校验不严，宽松重建（{ve}）[/yellow]"
                )
                plan = self._build_plan_lenient(data, brief)
        except Exception as e:  # noqa: BLE001
            self.console.print(f"[yellow]Planner 决策失败（{e}），使用空计划[/yellow]")
            plan = MasterPlan(brief=brief)
        plan.brief = brief or plan.brief
        self._save(plan)
        self._write_memory(plan)
        return plan

    # ---------------------------------------------------------------- 宽松重建
    def _build_plan_lenient(self, data: Any, brief: str) -> "MasterPlan":
        """宽松重建 Master Plan。

        结构化输出偶尔会返回字段缺失或类型不符的 dict（嵌套必填项如
        ``CharacterSketch.name`` 缺失），直接 ``MasterPlan(**data)`` 会抛
        ``ValidationError`` 并连带丢光整份规划。这里逐模型挑出合法字段、
        跳过非法条目，尽可能保留有效部分（修复 bug1）。

        Args:
            data: ``chat_structured`` 返回的（可能不规范的）dict。
            brief: 用户思路（始终保留）。

        Returns:
            尽可能完整的 ``MasterPlan``；``data`` 非 dict 时退回仅含 brief 的计划。
        """
        if not isinstance(data, dict):
            return MasterPlan(brief=brief)

        def _keep(model: type, raw: Any):
            if not isinstance(raw, dict):
                return None
            try:
                return model(**{k: v for k, v in raw.items() if k in model.model_fields})
            except Exception:  # noqa: BLE001
                return None

        try:
            genre = str(data.get("genre") or "modern")
        except Exception:  # noqa: BLE001
            genre = "modern"
        try:
            title = str(data.get("title") or "")
        except Exception:  # noqa: BLE001
            title = ""
        try:
            total = int(data.get("total_chapters") or 100)
        except Exception:  # noqa: BLE001
            total = 100
        try:
            notes = str(data.get("notes") or "")
        except Exception:  # noqa: BLE001
            notes = ""

        arcs = (
            [a for a in (_keep(Arc, x) for x in data.get("episode_tree") or []) if isinstance(a, Arc)]
            if isinstance(data.get("episode_tree"), list)
            else []
        )
        chars = (
            [c for c in (_keep(CharacterSketch, x) for x in data.get("character_skeleton") or []) if isinstance(c, CharacterSketch)]
            if isinstance(data.get("character_skeleton"), list)
            else []
        )
        fs = (
            [f for f in (_keep(PlannedForeshadow, x) for x in data.get("foreshadow_plan") or []) if isinstance(f, PlannedForeshadow)]
            if isinstance(data.get("foreshadow_plan"), list)
            else []
        )

        return MasterPlan(
            brief=brief,
            genre=genre,
            title=title,
            total_chapters=total,
            episode_tree=arcs,
            character_skeleton=chars,
            foreshadow_plan=fs,
            quality_targets=_keep(QualityTargets, data.get("quality_targets")) or QualityTargets(),
            notes=notes,
        )

    # ---------------------------------------------------------------- 落盘
    def _save(self, plan: MasterPlan) -> None:
        self.plan_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.plan_file.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(plan.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.plan_file)

    def load_plan(self) -> MasterPlan | None:
        """读取已落盘的 Master Plan（不存在返回 None）。"""
        if not self.plan_file.exists():
            return None
        try:
            data = json.loads(self.plan_file.read_text(encoding="utf-8"))
            return MasterPlan(**data)
        except (json.JSONDecodeError, Exception):  # noqa: BLE001
            return None

    def _write_memory(self, plan: MasterPlan) -> None:
        if self.memory is None:
            return
        try:
            for c in plan.character_skeleton:
                self.memory.remember(
                    f"角色{c.name}：{c.role}；{c.arc}",
                    type="character",
                    tags=[c.name],
                    source="plan",
                )
            if plan.episode_tree:
                threads = [f"{a.name}（{a.chapter_start}-{a.chapter_end}）：{a.goal}" for a in plan.episode_tree]
                self.memory.consolidate(
                    plot_threads=threads,
                    quality_targets=plan.quality_targets.model_dump(),
                )
        except Exception:  # noqa: BLE001 - 记忆写入失败不阻断
            pass

    # ---------------------------------------------------------------- 修订
    def revise_plan(self, current_chapter: int, note: str = "") -> MasterPlan:
        """据进度修订计划（best-effort）：补充备注并落盘。

        注：LLM 驱动的"重新规划"留给后续增强；此处保证计划随进度可被标注与持久化。
        """
        plan = self.load_plan() or MasterPlan()
        extra = f"[进度@{current_chapter}] {note}" if note else f"[进度@{current_chapter}]"
        plan.notes = (plan.notes + "\n" + extra).strip()
        self._save(plan)
        if self.memory is not None:
            try:
                self.memory.consolidate(last_consolidated_chapter=current_chapter)
            except Exception:  # noqa: BLE001
                pass
        return plan
