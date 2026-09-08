"""BudgetPlanner —— LLM 主编动态规划各支线章数预算。

写 .state/mainline.json 的 ``subline_share``，作为 MainlineOrchestrator 推进裁决的
确定性 cap。阶段比值（前期/中期/后期）与分线预算不再静态写死 / 均衡分账，
而是由 LLM 依据当前进度、各支线主题与全书体量动态推导——「预算由 LLM 规划、
执行仍由 orchestrator 确定性完成」，二者解耦（G8 拍板 1 语义保持）。

设计约束 / 权衡（对应 Agent Note: 2026-08-31-dynamic-subline-budget）：
- LLM 调用必须走统一入口 ``chat_structured``（结构化 + 严格 schema 校验 + 自动重试）。
- 失败降级（用户拍板 2）：沿用上次 ``subline_share``（mainline.json 已有值不动）；
  无任何历史时按 ``horizon_chapters / 支线数`` 均衡分账落盘兜底，写章环节绝不被
  预算规划阻塞（G3 哲学）。
- ``phase_ratio``（--ratio 软意图）仅作为 LLM 的参考输入提示，不直接当硬预算
  （用户拍板 3：保留为软意图）。

2026-09-08 补记（五灵破归档实证，三项修正）：
- 输入侧：prompt 注入 ``plan.json`` 的**主角路线节点区间**作锚。此前只喂「支线名 +
  总章数」，LLM 只能凭名字直觉分配（热闹的「万魔殿」+120、抽象的「过客观察」-50），
  导致支线边界与主线节奏骨架错位 500 章。
- 输出侧：预算落盘后同步重写各支线的压力曲线区间（``core/story/subline_curve``）。
  推进裁决取 ``min(曲线上界, cap)``，曲线静态 → 削减生效、扩张永不生效（棘轮效应）。
- 安全侧：新增不可逆保护，新预算不得把「已写/正在写的支线」压到当前章号以下，
  否则支线会在没走完高潮时被整体切走（且无法回退）。
"""

from __future__ import annotations
from agent.core.infra.prompt_manager import pm

import json
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field, ValidationError

from rich.console import Console

from agent.base.structured_output import StructuredOutputError


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
                new_share = self._protect_irreversible(new_share, sublines)
                plan["subline_share"] = new_share
                plan["horizon_chapters"] = int(budget.horizon_chapters) or horizon
                self._write_plan(plan)
                # 曲线同步：解除 min(曲线上界, cap) 的单向钳制（扩张不再被吞）
                self._sync_curves(new_share, sublines)
                self.console.print(
                    "[green]✓ LLM 主编已重规划分线预算："
                    + ", ".join(f"{k}={v}" for k, v in new_share.items())
                    + "[/green]"
                )
                return True
        except Exception as e:  # noqa: BLE001 - 规划失败降级，G3
            try:
                from agent.core.infra.degrade import degrade

                degrade("budget_planner.llm", "LLM 预算规划失败，沿用现值/均衡分账兜底", e)
            except Exception:  # noqa: BLE001, SILENT_DEGRADE
                pass
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

    # ------------------------------------------------- 不可逆保护 + 曲线同步
    def _protect_irreversible(self, share: dict[str, int], sublines: list[str]) -> dict[str, int]:
        """保证「已写/正在写」的支线累计预算不低于当前章号（防半路被切）。

        ``decide_mainline_advance`` 的切换条件是 ``chapter > upper``，其中 upper 为
        **累计口径**。若重规划把某支线的累计 cap 压到当前章号以下，下一裁决点会立刻
        切到下一条支线——该支线的冲突/高潮/舒缓段被整体跳过，且 ``mainline_visited``
        无回退机制，剧情永久断在半路（S01 高潮「宗门大比」被吞即此类）。

        补偿策略：先抬升当前支线到 ``written + 1``，再从**后续**支线依次扣回以维持总和；
        后续不足扣时允许总和超出 horizon（宁可超支，不可断线），并打印告警。

        Args:
            share: LLM 产出的单支线份额（支线 ID → 章数）。
            sublines: 支线有序列表（S01→S0n）。

        Returns:
            修正后的份额；无需修正时原样返回。
        """
        if not sublines:
            return share
        progress = self._read_progress()
        try:
            written = int(progress.get("total_written") or 0)
        except (TypeError, ValueError):
            written = 0
        if written <= 0:
            return share

        caps: list[int] = []
        acc = 0
        for sid in sublines:
            acc += int(share.get(sid) or 0)
            caps.append(acc)

        # 兜底定位：第一个累计 cap 越过已写章号的；全未越过则取最后一条
        idx = next((i for i, c in enumerate(caps) if c > written), len(sublines) - 1)
        current = str(progress.get("current_subline") or "")
        if current in sublines:
            # 状态机的真实写章点优先：正在写的支线被压到已写章号以下，才会触发
            # 「下一裁决点立刻切线」。注意取**当前支线索引本身**，不能与其后的
            # 兜底索引取 max——那是越过已写章号的下一条支线，抬它无效。
            idx = sublines.index(current)

        deficit = written + 1 - caps[idx]
        if deficit <= 0:
            return share

        out = dict(share)
        hold = sublines[idx]
        out[hold] = int(out.get(hold) or 0) + deficit
        rest = deficit
        for j in range(len(sublines) - 1, idx, -1):  # 从末条往前扣，优先保中段完整
            if rest <= 0:
                break
            sid = sublines[j]
            take = min(int(out.get(sid) or 0) - 1, rest)
            if take > 0:
                out[sid] = int(out[sid]) - take
                rest -= take
        self.console.print(
            f"[yellow]⚠ 预算不可逆保护：{hold} 已写至第 {written} 章，"
            f"累计预算 {caps[idx]} 不足，已抬升至 {caps[idx] + deficit}"
            + (f"（后续支线仅扣回 {deficit - rest} 章，总章数将超出 horizon）" if rest > 0 else "")
            + "[/yellow]"
        )
        return out

    def _sync_curves(self, share: dict[str, int], sublines: list[str]) -> None:
        """把新预算换算成累计区间并同步压力曲线（失败只告警，G3 不阻断）。"""
        try:
            from agent.core.story.subline_curve import sync_pressure_curves

            notes = sync_pressure_curves(self.project_dir, share, sublines)
        except Exception as e:  # noqa: BLE001 - 曲线同步失败不影响预算与写章
            try:
                from agent.core.infra.degrade import degrade

                degrade("budget_planner.curve_sync", "压力曲线同步失败，沿用旧曲线", e)
            except Exception:  # noqa: BLE001
                pass  # noqa: SILENT_DEGRADE
            self.console.print(f"[yellow]⚠ 压力曲线同步失败（不影响写章）：{e}[/yellow]")
            return
        if notes:
            self.console.print(
                "[dim]压力曲线已随预算同步：" + "；".join(notes) + "[/dim]"
            )

    # ------------------------------------------------------------------ LLM
    def _ask_llm(
        self, sublines: list[str], horizon: int, plan: dict[str, Any]
    ) -> _BudgetSchema:
        """调用统一 chat_structured 产出主编预算。

        校验/解析失败时附真实错误详情重试一次（与 Writer 同模式）：
        chat_structured 仅以提示词嵌入 Schema（无原生 response_format 硬约束），
        弱遵从度 provider 常漏必填字段（horizon_chapters/subline_budget）；
        首败直接降级会让本支线周期静默走均衡分账，重试可救回大部分。
        """
        if self._llm is None:
            from agent.client.gateway_adapter import create_gateway
            self._llm = create_gateway()
        # 修复：chat_structured 此前未导入，规划必然 NameError 降级（G3 兜底掩盖）
        from agent.client.gateway_adapter import chat_structured

        user_msg = self._build_user_prompt(sublines, horizon, plan)
        base_messages = [
            {"role": "system", "content": pm.get("budget.branch").system},
            {"role": "user", "content": user_msg},
        ]
        retry_messages: list[dict[str, str]] | None = None
        last_error: Exception | None = None
        for attempt in (0, 1):
            try:
                data = chat_structured(
                    self._llm,
                    retry_messages or base_messages,
                    schema=_BudgetSchema,
                    use="creative",
                    temperature=0.5,
                    # 高 max_tokens：V4 Flash 常在 JSON 前先输出较长前言，2048 会被前言吃光、
                    # 截断到 JSON 之前的纯散文（见 .agents/notes/implemented/architecture/
                    # 2026-08-31-dynamic-subline-budget.md），导致 extract 失败并静默降级。
                    max_tokens=8192,
                    enable_thinking=False,
                )
                return data
            except (ValidationError, StructuredOutputError) as ve:  # noqa: BLE001 - G4 精确捕获
                last_error = ve
                if attempt == 1:
                    raise
                retry_messages = list(base_messages) + [
                    {
                        "role": "user",
                        "content": (
                            "【输出格式硬约束】上一次输出不满足 JSON Schema 校验"
                            "（可能是无法解析，或解析成功但缺必填字段）。"
                            "此条必须只输出一个合法 JSON 对象，必填字段一个都不能少：\n"
                            '{"horizon_chapters": 总章数, '
                            '"subline_budget": [{"subline_id": "S01", "chapters": N}, ...], '
                            '"notes": "分配思路"}\n'
                            f"【上一次的具体错误】{ve}"
                        ),
                    }
                ]  # noqa: SILENT_DEGRADE - 重试路径仍可能失败，由 plan() 的 G3 降级兜底
        raise last_error or RuntimeError("预算规划重试耗尽")

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
        # 主角路线节点：全书节奏骨架，支线边界的对齐锚（2026-09-08 新增）
        anchors = self._route_anchor_lines()
        if anchors:
            parts.append("- 主角路线节点（全书节奏骨架，权威）：")
            parts.extend(anchors)
            parts.append(
                "  - 要求：各支线的章节区间边界尽量与上述节点边界对齐。"
                "每个节点覆盖的章段内，必须有支线承载该节点的里程碑事件——"
                "不允许出现「某节点里程碑在全书中无支线负责」的空档。"
            )
        # 当前进度
        progress = self._read_progress()
        if progress:
            parts.append(
                f"- 当前进度：位于支线 {progress.get('current_subline', '（未定）')}，"
                f"已写 {progress.get('total_written', 0)} 章，"
                f"已访问支线 {progress.get('mainline_visited', [])}"
            )
            parts.append(
                "  - 已写过的章数不可回收：不要把已写支线的预算压到已写章数以下"
                "（会导致该支线未走完高潮就被切走）。"
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
    def _route_anchor_lines(self) -> list[str]:
        """读 plan.json 的主角路线节点（章节区间 + 里程碑），作为预算对齐锚。

        plan.json 是全书节奏的权威（``plan_consistency`` 确立）。缺文件/无节点/解析
        失败返回空列表——prompt 少一段锚点只会让 LLM 少些依据，不该阻断规划（G3）。
        """
        try:
            pf = self.project_dir / ".state" / "plan.json"
            if not pf.exists():
                return []
            data = json.loads(pf.read_text(encoding="utf-8"))
            nodes = ((data.get("route") or {}).get("nodes")) or []
            lines: list[str] = []
            for n in nodes:
                rng = str(n.get("chapter_range") or "").strip()
                if not rng:
                    continue
                milestone = str(n.get("milestone") or "").strip()
                lines.append(
                    f"  - {n.get('id', 'N??')} {rng}：{milestone}" if milestone
                    else f"  - {n.get('id', 'N??')} {rng}"
                )
            return lines
        except Exception as e:  # noqa: BLE001 - 锚点缺失降级为空，不阻断规划
            try:
                from agent.core.infra.degrade import degrade

                degrade("budget_planner.route_anchor", "主角路线锚点读取失败，跳过", e)
            except Exception:  # noqa: BLE001
                pass  # noqa: SILENT_DEGRADE
            return []

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