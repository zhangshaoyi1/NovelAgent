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

from pydantic import BaseModel, Field, ValidationError
from rich.console import Console

from agent.client import LLMClient
from agent.core.infra.prompt_manager import pm
from agent.core.story.method_style import load_method_text  # G11：写作方法模板
from agent.core.story.setting_manager import SettingManager
from agent.core.base.structured_output import StructuredOutputError
from agent.prompts import G11_METHOD_INSTRUCTION_TEMPLATE  # G11：方法模板注入常量


# ============================================================
# Master Plan 结构（可被 chat_structured 强制的结构化输出）
# ============================================================
class QualityTargets(BaseModel):
    """七维"不崩"合格线（对应设计文档 §1.2；标 not_relaxable 的不可放宽）。"""

    character_stability_high: int = Field(default=0, description="人设硬伤高严重度数，=0 不可放宽")
    setting_consistency_high: int = Field(default=0, description="设定一致性高严重度冲突，=0 不可放宽")
    foreshadow_recycle_rate: float = Field(default=0.90, description="伏笔回收率下限，默认 90%")
    # G2 收紧 80→85 / 75→80（与 evaluator_agent.qt 默认、_PLANNER_SYSTEM 三处同步）
    coherence: float = Field(default=85.0, description="连贯性自评下限（/100）")
    readability: float = Field(default=80.0, description="追读力综合评分下限（/100）")
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
    # G2 收紧 coherence=80→85 / readability=75→80（与 evaluator_agent.qt 默认、QualityTargets 默认三处同步）
    "quality_targets 默认值：foreshadow_recycle_rate=0.90, coherence=85, readability=80,\n"
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
        # G11：写作方法模板开关（默认开：project/method.md 存在即注入）
        method_enabled: bool = True,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.llm = llm_client
        self.console = console or Console()
        self.memory = memory
        self._decide = decide
        self._decide_async = decide_async
        self.plan_file = self.project_dir / ".state" / "plan.json"
        # G11：方法模板开关
        self.method_enabled = method_enabled

        # G4 新增：Schema 降级标志（供 pipeline 读取）
        self._schema_degraded: bool = False

    # ---------------------------------------------------------------- 上下文
    def _method_suffix(self) -> str:
        """G11：写作方法模板追加段（project/method.md 存在即注入；缺失/关闭 → ""）。"""
        if not self.method_enabled:
            return ""
        try:
            method_text, _name = load_method_text(self.project_dir, enabled=True)
            if not method_text:
                return ""
            return G11_METHOD_INSTRUCTION_TEMPLATE.format(method_text=method_text)
        except Exception:  # noqa: BLE001 - 模板读取失败降级为空，不阻断
            return ""

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
                strict=True,  # G4 开启 strict=True 强校验
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
                strict=True,  # G4 开启 strict=True 强校验
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
        user_msg += self._method_suffix()  # G11：写作方法模板注入（存在即追加）
        decide = self._make_decide()
        messages = [
            {"role": "system", "content": pm.get("agents.planner").system},
            {"role": "user", "content": user_msg},
        ]
        try:
            data = decide(messages)
            # G4 修复：关键字段缺失（默认值不触发 ValidationError）显式检测 → 走分级策略
            if self._explicit_missing_critical(data):
                plan = self._validate_masterplan(data, brief, None, messages=messages)
            else:
                # 优先严格构造；结构化输出若部分字段缺失/类型不符导致校验失败，
                # 走 G4 分级策略（关键字段重试 → 安全降级；非关键字段按条目降级）。
                try:
                    if isinstance(data, MasterPlan):
                        plan = data
                    else:
                        plan = MasterPlan(**(data if isinstance(data, dict) else dict(data)))
                except (ValidationError, StructuredOutputError) as ve:  # noqa: BLE001 - G4 精确捕获
                    self.console.print(
                        f"[yellow]Planner 结构化输出字段校验失败，启动分级策略（{ve}）[/yellow]"
                    )
                    plan = self._validate_masterplan(data, brief, ve, messages=messages)
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
        user_msg += self._method_suffix()  # G11：写作方法模板注入（存在即追加）
        decide_async = await self._make_decide_async()
        messages = [
            {"role": "system", "content": pm.get("agents.planner").system},
            {"role": "user", "content": user_msg},
        ]
        try:
            data = await decide_async(messages)
            # G4 修复：关键字段缺失（默认值不触发 ValidationError）显式检测 → 走分级策略
            if self._explicit_missing_critical(data):
                plan = self._validate_masterplan(data, brief, None, messages=messages)
            else:
                try:
                    if isinstance(data, MasterPlan):
                        plan = data
                    else:
                        plan = MasterPlan(**(data if isinstance(data, dict) else dict(data)))
                except (ValidationError, StructuredOutputError) as ve:  # noqa: BLE001 - G4 精确捕获
                    self.console.print(
                        f"[yellow]Planner 结构化输出字段校验失败，启动分级策略（{ve}）[/yellow]"
                    )
                    plan = self._validate_masterplan(data, brief, ve, messages=messages)
        except Exception as e:  # noqa: BLE001
            self.console.print(f"[yellow]Planner 决策失败（{e}），使用空计划[/yellow]")
            plan = MasterPlan(brief=brief)
        plan.brief = brief or plan.brief
        self._save(plan)
        self._write_memory(plan)
        return plan

    # ---------------------------------------------------------------- 宽松重建（G4 收敛：返回丢弃字段清单）
    def _build_plan_lenient(self, data: Any, brief: str) -> dict[str, Any]:
        """宽松重建 Master Plan（G4 收敛：仅非关键字段降级）。

        结构化输出偶尔会返回字段缺失或类型不符的 dict（嵌套必填项如
        ``CharacterSketch.name`` 缺失），直接 ``MasterPlan(**data)`` 会抛
        ``ValidationError`` 并连带丢光整份规划。这里逐模型挑出合法字段、
        跳过非法条目，尽可能保留有效部分（修复 bug1）。

        G4 收敛：返回值改为字典，包含丢弃字段清单（用于日志）。

        Args:
            data: ``chat_structured`` 返回的（可能不规范的）dict。
            brief: 用户思路（始终保留）。

        Returns:
            {"plan": MasterPlan, "discarded_characters": list[str], "discarded_foreshadows": list[str]}
        """
        if not isinstance(data, dict):
            return {
                "plan": MasterPlan(brief=brief),
                "discarded_characters": [],
                "discarded_foreshadows": [],
            }

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

        # G4 收敛：记录被丢弃的非关键字段（角色/伏笔）
        discarded_chars: list[str] = []
        discarded_foreshadows: list[str] = []

        def _keep_char(raw: Any):
            if not isinstance(raw, dict):
                discarded_chars.append(f"非字典类型: {type(raw)}")
                return None
            try:
                c = CharacterSketch(**{k: v for k, v in raw.items() if k in CharacterSketch.model_fields})
                # G4 收敛：缺 name 字段时丢弃该条目并记录
                if not c.name:
                    discarded_chars.append(f"缺 name 字段: {raw}")
                    return None
                return c
            except Exception as e:
                discarded_chars.append(f"校验失败: {e}")
                return None

        def _keep_fore(raw: Any):
            if not isinstance(raw, dict):
                discarded_foreshadows.append(f"非字典类型: {type(raw)}")
                return None
            try:
                f = PlannedForeshadow(**{k: v for k, v in raw.items() if k in PlannedForeshadow.model_fields})
                # G4 收敛：缺 id 字段时丢弃该条目并记录
                if not f.id:
                    discarded_foreshadows.append(f"缺 id 字段: {raw}")
                    return None
                return f
            except Exception as e:
                discarded_foreshadows.append(f"校验失败: {e}")
                return None

        chars = (
            [c for c in (_keep_char(x) for x in data.get("character_skeleton") or []) if c]
            if isinstance(data.get("character_skeleton"), list)
            else []
        )
        fs = (
            [f for f in (_keep_fore(x) for x in data.get("foreshadow_plan") or []) if f]
            if isinstance(data.get("foreshadow_plan"), list)
            else []
        )

        # G4 收敛：打印被丢弃字段清单（占位）
        if discarded_chars or discarded_foreshadows:
            self.console.print(
                f"[yellow]非关键字段降级：丢弃 {len(discarded_chars)} 个角色、"
                f"{len(discarded_foreshadows)} 个伏笔[/yellow]"
            )

        return {
            "plan": MasterPlan(
                brief=brief,
                genre=genre,
                title=title,
                total_chapters=total,
                episode_tree=arcs,
                character_skeleton=chars,
                foreshadow_plan=fs,
                quality_targets=_keep(QualityTargets, data.get("quality_targets")) or QualityTargets(),
                notes=notes,
            ),
            "discarded_characters": discarded_chars,
            "discarded_foreshadows": discarded_foreshadows,
        }

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

    # ---------------------------------------------------------------- G4 T7 落地：分级校验策略
    def _explicit_missing_critical(self, data: Any) -> list[str]:
        """显式检查关键字段是否缺失/非法（G4 修复）。

        ``MasterPlan`` 中 ``brief/genre/title/total_chapters/episode_tree`` 均有默认值，
        缺失时不会触发 ``ValidationError``，导致"关键字段缺失→重试"的分级策略形同虚设。
        故单独显式检查，使 PRD 拍板 #2（关键字段硬拒重试→失败安全降级）真正生效。
        """
        if not isinstance(data, dict):
            return ["brief", "genre", "title", "total_chapters", "episode_tree"]
        miss: list[str] = []
        if not str(data.get("brief") or "").strip():
            miss.append("brief")
        if not str(data.get("genre") or "").strip():
            miss.append("genre")
        if not str(data.get("title") or "").strip():
            miss.append("title")
        tc = data.get("total_chapters")
        if not isinstance(tc, int) or isinstance(tc, bool) or tc <= 0:
            miss.append("total_chapters")
        et = data.get("episode_tree")
        if not isinstance(et, list) or len(et) == 0:
            miss.append("episode_tree")
        return miss

    def _validate_masterplan(
        self,
        data: Any,
        brief: str,
        last_error: Exception,
        retries: int = 2,
        messages: list[dict[str, str]] | None = None,
    ) -> MasterPlan:
        """强校验 + 分级策略（G4 T7 落地）。

        1) 关键字段缺失/类型非法 → 硬拒，重试 N 次（retries 默认 2，即最多 3 次）。
        2) 非关键字段 → 按条目降级，记日志。
        3) 重试耗尽仍未修复关键字段 → 安全降级为 minimal MasterPlan(brief)，
           标记 self._schema_degraded = True。

        Args:
            data: chat_structured 返回的（可能不规范）数据。
            brief: 用户思路（始终保留）。
            last_error: 上次 ValidationError（用于判断关键字段）。
            retries: 重试上限（默认 2）。
            messages: 原始消息列表（用于重试）。

        Returns:
            MasterPlan（尽可能完整，或 minimal 占位）。
        """
        # 步骤 1: 判断关键字段是否缺失/非法
        missing_critical = self._extract_missing_critical_fields(last_error)
        # G4 修复：默认值字段缺失不触发 ValidationError，显式检查补全关键字段判定
        missing_critical += self._explicit_missing_critical(data)
        missing_critical = list(dict.fromkeys(missing_critical))
        if missing_critical:
            # 关键字段缺失，重试
            if retries > 0:
                self.console.print(
                    f"[yellow]关键字段缺失 {missing_critical}，重试（剩余 {retries} 次）[/yellow]"
                )
                hint = (
                    f"【校验失败提示】上次返回缺失关键字段："
                    f"{', '.join(missing_critical)}，请补全。"
                )
                new_data = self._retry_with_hint(messages, hint)
                # 重新校验新数据，得到真实错误（关键字段可能仍缺失）
                new_error: Exception | None = None
                try:
                    MasterPlan(**(new_data if isinstance(new_data, dict) else dict(new_data)))
                except (ValidationError, StructuredOutputError) as ve:
                    new_error = ve
                return self._validate_masterplan(
                    new_data, brief, new_error, retries - 1, messages
                )
            else:
                # 重试耗尽，安全降级
                self.console.print(
                    f"[red]关键字段重试耗尽，安全降级为 minimal MasterPlan[/red]"
                )
                self._schema_degraded = True
                return MasterPlan(brief=brief)

        # 步骤 2: 非关键字段按条目降级
        result = self._build_plan_lenient(data, brief)
        discarded_chars = result.get("discarded_characters", [])
        discarded_foreshadows = result.get("discarded_foreshadows", [])
        if discarded_chars or discarded_foreshadows:
            self.console.print(
                f"[yellow]非关键字段降级：丢弃 {len(discarded_chars)} 个角色、"
                f"{len(discarded_foreshadows)} 个伏笔[/yellow]"
            )
        return result["plan"]

    @staticmethod
    def _extract_missing_critical_fields(error: Exception) -> list[str]:
        """从 ValidationError 提取缺失的关键字段名。

        Args:
            error: ValidationError 实例。

        Returns:
            缺失的关键字段名列表（如 ["title", "total_chapters"]）。
        """
        if not isinstance(error, ValidationError):
            return []
        missing: list[str] = []
        for err in error.errors():
            field = err["loc"][0] if err["loc"] else None
            if field in {"brief", "genre", "title", "total_chapters", "episode_tree"}:
                missing.append(field)
        return missing

    def _retry_with_hint(
        self,
        messages: list[dict[str, str]] | None,
        hint: str,
        max_retries: int = 2,
    ) -> dict[str, Any]:
        """用 hint 重试 chat_structured(strict=True)。

        Args:
            messages: 原始消息列表。
            hint: 提示信息。
            max_retries: 最大重试次数。

        Returns:
            新的 LLM 响应数据（dict）。

        Raises:
            Exception: 重试耗尽时抛出异常，让外环走安全降级。
        """
        decide = self._make_decide()
        # 构建带 hint 的消息
        retry_messages = list(messages) if messages else []
        retry_messages.append({"role": "user", "content": hint})

        last_error: Exception | None = None
        for attempt in range(max_retries):
            try:
                data = decide(retry_messages)
                return data
            except Exception as e:
                last_error = e
                self.console.print(
                    f"[yellow]重试第 {attempt + 1}/{max_retries} 次失败：{e}[/yellow]"
                )
        raise last_error or RuntimeError("重试耗尽")