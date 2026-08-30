"""BudgetPlanner —— LLM 主编动态规划各支线章数预算。

写 .state/mainline.json 的 ``subline_share``，作为 MainlineOrchestrator 推进裁决的
确定性 cap。阶段比值（前期/中期/后期）与分线预算不再静态写死 / 均衡分账，
而是由 LLM 依据当前进度、各支线主题与全书体量动态推导——「预算由 LLM 规划、
执行仍由 orchestrator 确定性完成」，二者解耦（G8 拍板 1 语义保持）。

设计约束 / 权衡（对应 Agent Note: 2026-08-31-dynamic-subline-budget）：
- LLM 调用必须走统一入口 ``LLMClient.chat_structured``（结构化 + 严格 schema 校验 + 自动重试）。
- 失败降级（用户拍板 2）：沿用上次 ``subline_share``（mainline.json 已有值不动）；
  无任何历史时按 ``horizon_chapters / 支线数`` 均衡分账落盘兜底，写章环节绝不被
  预算规划阻塞（G3 哲学）。
- ``phase_ratio``（--ratio 软意图）仅作为 LLM 的参考输入提示，不直接当硬预算
  （用户拍板 3：保留为软意图）。
"""

from __future__ import annotations
from agent.core.infra.prompt_manager import pm

import json
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from rich.console import Console


# ---------------------------------------------------------------------------
# 结构化输出 Schema（LLM 必须严格按此产出）
# ---------------------------------------------------------------------------
class _SublineBudget(BaseModel):
    """单个支线的章数预算。"""

    subline_id: str = Field(description="支线 ID，如 S01_过去秘密揭露")
    chapters: int = Field(gt=0, description="该支线在全书累计允许写的章数上限")
    reason: str = Field(default="", description="分给该支线如此多章节的理由（一句话）")


class _BudgetSchema(BaseModel):
    """主编预算规划的结构化输出。"""

    horizon_chapters: int = Field(gt=0, description="全书目标总章数（与输入一致）")
    subline_budget: list[_SublineBudget] = Field(
        description="各支线章数预算清单，需覆盖全部支线且与输入支线集合一致"
    )
    notes: str = Field(default="", description="整体分配思路概述（每支线占比与原因）")


_SYSTEM_PROMPT = """你是一名资深小说主编（Chief Editor）。你的任务是为一本长篇连载小说划分各支线
（故事线）的篇幅预算，目标：整本书在「全书目标总章数」内完成，某条支线不被无限拖长、
主线收束有足够篇幅。请基于给定小说背景，动态判断每个支线该分多少章。

硬性输出要求（违反即任务失败）：
- 只输出一个 JSON 对象，禁止输出任何解释、前言、分析或 Markdown；禁止复述/评价本需求。
- 直接以 JSON 对象作答，不要用代码块围栏（```）包裹。
- JSON 必须使用以下精确结构（字段名一字不差）：
{"horizon_chapters": <整数总章数>, "subline_budget": [{"subline_id": "支线ID", "chapters": <正整数>, "reason": "一句话理由"}], "notes": "整体思路"}
- 注意：每个支线的 chapters 都是该支线在本书的累计上限（正整数）；各支线之和应接近 total_horizon（允许略小，为收束/尾声留余量）。
- 支线_id 必须与输入给定的一字不差，且要覆盖全部支线。"""


_BUDGET_EXAMPLE = (
    '{"horizon_chapters": 1000, "subline_budget": '
    '[{"subline_id": "S01_过去秘密揭露", "chapters": 360, "reason": "前期核心线，埋因果需多给篇幅"}, '
    '{"subline_id": "S02_敌人背景故事", "chapters": 320, "reason": "中期冲突升级"}, '
    '{"subline_id": "S03_仙元真相探索", "chapters": 200, "reason": "后期揭示真相"}, '
    '{"subline_id": "S04_内心孤独挣扎", "chapters": 120, "reason": "贯穿性心理线，篇幅可少"}], '
    '"notes": "前期重展开、中期重冲突、后期压缩收束"}'
)


class BudgetPlanner:
    """LLM 主编动态生成各支线章数预算并落盘。

    Args:
        project_dir: 小说项目目录。
        llm_client: 统一 LLM 客户端（可选；不传惰性创建）。
        console: rich 控制台（可选）。
        plan_file: mainline.json 路径（默认 project_dir/.state/mainline.json）。
    """

    def __init__(
        self,
        project_dir: str | Path,
        llm_client: Any | None = None,
        console: Console | None = None,
        plan_file: str | Path | None = None,
    ) -> None:
        self.project_dir = Path(project_dir)
        self._plan_file = (
            Path(plan_file)
            if plan_file is not None
            else self.project_dir / ".state" / "mainline.json"
        )
        self.console = console or Console()
        self._llm = llm_client

    # ------------------------------------------------------------------ 主入口
    def plan(self) -> bool:
        """执行一次主编预算规划并落盘。

        Returns:
            True=LLM 规划成功并更新了 ``subline_share``；
            False=规划失败（沿用上次值；无历史时已按均衡分账落盘兜底）。
            无论成败都不抛异常，绝不阻断写章（G3）。
        """
        plan = self._read_plan()
        sublines = self._list_sublines()
        horizon = int(plan.get("horizon_chapters") or self._estimate_horizon() or 0)
        if not sublines or horizon < 1:
            return False  # 缺支线/体量时无法规划，交给 orchestrator 原逻辑

        reason_ok = True
        try:
            budget = self._ask_llm(sublines, horizon, plan)
            new_share = self._normalize(budget.subline_budget, sublines, horizon)
            if not new_share:
                reason_ok = False
            else:
                plan["subline_share"] = new_share
                plan["horizon_chapters"] = int(budget.horizon_chapters) or horizon
                self._write_plan(plan)
                self.console.print(
                    "[green]✓ LLM 主编已重规划分线预算："
                    + ", ".join(f"{k}={v}" for k, v in new_share.items())
                    + "[/green]"
                )
                return True
        except Exception as e:  # noqa: BLE001 - 规划失败降级，G3
            self.console.print(f"[yellow]⚠ LLM 预算规划失败，沿用现值：{e}[/yellow]")
            reason_ok = False

        # ---- 降级：沿用上次 subline_share；无历史则均衡分账兜底 ----
        if not plan.get("subline_share"):
            plan_subline_share = self._equal_share(sublines, horizon)
            plan["subline_share"] = plan_subline_share
            self._write_plan(plan)
            self.console.print(
                "[dim]分线预算缺省：按体量均衡分账落盘兜底[/dim]"
            )
        return reason_ok

    # ------------------------------------------------------------------ LLM
    def _ask_llm(
        self, sublines: list[str], horizon: int, plan: dict[str, Any]
    ) -> _BudgetSchema:
        """调用统一 LLMClient.chat_structured 产出主编预算。"""
        if self._llm is None:
            from agent.client import LLMClient
            self._llm = LLMClient()

        user_msg = self._build_user_prompt(sublines, horizon, plan)
        data = self._llm.chat_structured(
            [
                {"role": "system", "content": pm.get("budget.branch").system},
                {"role": "user", "content": user_msg},
            ],
            _BudgetSchema,
            use="creative",
            temperature=0.5,
            # 高 max_tokens：V4 Flash 常在 JSON 前先输出较长前言，2048 会被前言吃光、
            # 截断到 JSON 之前的纯散文（见 .agents/notes/implemented/architecture/
            # 2026-08-31-dynamic-subline-budget.md），导致 extract 失败并静默降级。
            max_tokens=8192,
            enable_thinking=False,
            strict=True,
            name="subline_budget",
        )
        return _BudgetSchema(**data)

    def _build_user_prompt(
        self, sublines: list[str], horizon: int, plan: dict[str, Any]
    ) -> str:
        """组装给 LLM 的小说内容背景（各支线主题 + 当前进度 + 软意图）。"""
        parts: list[str] = []
        parts.append(f"\n# 请为以下连载小说划分各支线章数预算")
        parts.append(f"- 全书目标总章数：{horizon}")
        parts.append(f"- 支线列表（S0x 为支线 ID，其后为支线主题）：")
        sm = self._setting_mgr()
        for sid in sublines:
            title = self._subline_title(sid, sm)
            parts.append(f"  - {sid}：{title}")
        # 当前进度
        progress = self._read_progress()
        if progress:
            parts.append(
                f"- 当前进度：位于支线 {progress.get('current_subline', '（未定）')}，"
                f"已写 {progress.get('total_written', 0)} 章，"
                f"已访问支线 {progress.get('mainline_visited', [])}"
            )
        # 软意图（phase_ratio）
        ratio = plan.get("phase_ratio") or {}
        if ratio:
            hint = " · ".join(f"{k}={v}%分" for k, v in sorted(ratio.items()))
            parts.append(f"- 用户软意图（供参考，不强制）：{hint}")
        parts.append(
            "\n请按主编判断划分各支线章节预算。"
            "只输出一个 JSON 对象，不要任何解释或 Markdown。一个合法仅作格式示范的输出为：\n"
            + _BUDGET_EXAMPLE
        )
        return "\n".join(parts)

    # ------------------------------------------------------------ 数据来源
    def _setting_mgr(self) -> Any:
        from agent.core.story.setting_manager import SettingManager

        return SettingManager(self.project_dir)

    def _list_sublines(self) -> list[str]:
        try:
            return self._setting_mgr().list_sublines()
        except Exception:  # noqa: BLE001
            return []

    def _subline_title(self, subline_id: str, sm: Any) -> str:
        """取支线主题优先 frontmatter ``subline_name``，否则从 ID 拆分。"""
        try:
            md = sm.load_subline(subline_id)
            name = (md.get("metadata") or {}).get("subline_name")
            if name:
                return str(name)
        except Exception:  # noqa: BLE001
            pass
        # fallback：S01_过去秘密揭露 -> 过去秘密揭露
        return subline_id.split("_", 1)[-1] if "_" in subline_id else subline_id

    def _read_progress(self) -> dict[str, Any]:
        sf = self.project_dir / ".state" / "state.json"
        if not sf.exists():
            return {}
        try:
            return dict(json.loads(sf.read_text(encoding="utf-8")).get("progress", {}) or {})
        except Exception:  # noqa: BLE001
            return {}

    def _estimate_horizon(self) -> Optional[int]:
        """缺省全书目标章数：按 world.md 体量估算。"""
        try:
            from agent.core.story.setting_manager import SettingManager
            from agent.core.story.volume import estimate_chapters

            md = SettingManager(self.project_dir).load_world().get("metadata", {}) or {}
            scope = md.get("scope") or "medium"
            total_words = md.get("scope_total_words")
            cl = md.get("scope_chapter_length") or md.get("chapter_length") or 3000
            return int(estimate_chapters(scope, total_words, cl))
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------ 归一化/落盘
    @staticmethod
    def _normalize(
        items: list[_SublineBudget], sublines: list[str], horizon: int
    ) -> dict[str, int]:
        """校验 LLM 输出：只保留属于该书支线集合且 >0 的预算；缺项按均分补齐。"""
        share: dict[str, int] = {}
        for it in items:
            if it.subline_id in sublines and it.chapters > 0:
                share[it.subline_id] = int(it.chapters)
        missing = [s for s in sublines if s not in share]
        if missing:
            base = max(1, horizon // max(1, len(sublines)))
            for s in missing:
                share[s] = base
        return share

    @staticmethod
    def _equal_share(sublines: list[str], horizon: int) -> dict[str, int]:
        """均衡分账（缺默认兜底）。"""
        n = max(1, len(sublines))
        base, rem = divmod(max(1, horizon), n)
        return {sid: base + (1 if i < rem else 0) for i, sid in enumerate(sublines)}

    def _read_plan(self) -> dict[str, Any]:
        if not self._plan_file.exists():
            horizon = self._estimate_horizon() or 0
            return {"version": 1, "horizon_chapters": horizon, "phase_ratio": {}, "subline_share": {}}
        try:
            data = json.loads(self._plan_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    def _write_plan(self, plan: dict[str, Any]) -> None:
        self._plan_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._plan_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._plan_file)