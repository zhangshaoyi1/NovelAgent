"""AgenticPipelineWorkflow 拆分的 Mixin（机械搬移，行为零改动）。

拆分背景：单文件 1600+ 行不利维护（对齐 m5 Mixin 拆分模式，架构评审 P2-9）。
主文件保留类定义、``__init__`` 与 ``run()``；本文件（G3 设定集自检 + 自主规划编排 + 降级占位）承载对应方法组。
仅供 ``AgenticPipelineWorkflow`` 继承组合，不要单独使用。
"""

from __future__ import annotations

import os

from agent.core.engine.state_machine import TRANSITIONS, State
from agent.core.story.setting_manager import SettingManager
from agent.core.quality.guardrails import is_architecture_confirmed
from agent.workflows.pipeline.agentic_pipeline_types import (
    _CANON,
    _EVENTS,
    _PlanStepResult,
)

class _PipelinePlanningMixin:
    # ---------------------------------------------------------------- 设定集自检/引导（G3）
    def _ensure_setting_set(self) -> None:
        """自主模式引导（G3 薄壳）：委托 ``_autoplan_full_book`` 串联真实
        M1→M2→M14→M3→M4 复用规划工作流，使下游 M5 ``_load_context`` 不再因缺文件
        而抛错。保留公开签名（``run`` 仍调用），幂等、不崩。
        """
        self._plan_blocked = False
        self._plan_block_reason = ""
        self._autoplan_full_book()

    # ---------------------------------------------------------------- G3 自主规划编排器
    def _autoplan_full_book(self) -> None:
        """自主规划整本书：串联真实 M1→M2→M14→M3→M4，复用同一
        ``llm_client / state_machine / SettingManager / console``（设计 §1.1）。

        每步「产物已存在且有效则跳过」（幂等，§2.2），从任意半残态续跑均安全。
        关键前置（M1 world / M14 架构确认）失败 → 置 ``_plan_blocked`` 并提前
        return（安全退出，不进写章，拍板 #2）；非关键（M2/M3/M4）失败 →
        ``_safe_step`` 重试（默认 2 次）后降级占位继续。
        """
        from agent.workflows.planning.m1_config import M1ConfigWorkflow, M1Input
        from agent.workflows.planning.m2_discuss import M2DiscussWorkflow, M2Input
        from agent.workflows.evaluation.m14_architecture import M14ArchitectureWorkflow
        from agent.workflows.planning.m3_outline import M3OutlineWorkflow
        from agent.workflows.planning.m4_character import M4CharacterWorkflow

        # G4 进度回调（T4）：规划阶段
        self._emit_progress("planning", 0, 100)

        # 自动模式：显式设定 auto 档（设计 §9 #5），使下游交互默认跳过。
        # 注意：必须先 load() 再 set_mode，否则会用内存默认 INIT 覆盖既有状态/进度。
        try:
            self.state_machine.load()
            self.state_machine.set_mode("auto")
        except Exception:  # noqa: BLE001
            pass

        sm = SettingManager(self.project_dir)
        llm = self._traced_llm()

        def wf(cls: type, **extra: Any) -> Any:
            return cls(
                self.project_dir,
                llm_client=llm,
                setting_manager=sm,
                state_machine=self.state_machine,
                console=self.console,
                **extra,
            )

        # ---- M1 配置（关键前置）----
        if (self.project_dir / "world.md").exists():
            self._advance_state_to(State.DISCUSSING)
        else:
            m1_input = M1Input(
                title=(self.brief[:30] or "未命名作品").strip(),
                scope="long",
                genre=os.getenv("G3_GENRE", "xiuxian"),
                story_core=self.brief or "（未提供创作思路，请基于默认值自主生成）",
            )
            ok = self._safe_step(
                key=True, name="M1 世界观生成",
                fn=lambda: wf(M1ConfigWorkflow).run(m1_input),
            ).ok
            # G4 熔断检查点：规划每步后
            if self._check_budget("plan_step"):
                self._plan_tripped = True
                self._plan_blocked = True
                self._plan_block_reason = "Token 预算超限或墙钟超时熔断（规划阶段）"
                return
            if not ok:
                self._plan_blocked = True
                self._plan_block_reason = "M1 世界观生成失败（关键前置），已安全退出，不进入写章。"
                return
            self._advance_state_to(State.DISCUSSING)

        # ---- M2 脉络讨论（非关键，非交互）----
        if (self.project_dir / "discussion.md").exists():
            self._advance_state_to(State.ARCHITECTING)
        else:
            m2_input = M2Input(
                max_rounds=int(os.getenv("G3_M2_ROUNDS", "1")),
                preset_answers=[
                    self.brief or "（请基于世界观直接收敛主线与关键冲突）"
                ],
            )
            self._safe_step(
                key=False, name="M2 脉络讨论",
                fn=lambda: wf(M2DiscussWorkflow).run(m2_input),
            )
            # G4 熔断检查点：规划每步后
            if self._check_budget("plan_step"):
                self._plan_tripped = True
                self._plan_blocked = True
                self._plan_block_reason = "Token 预算超限或墙钟超时熔断（规划阶段）"
                return
            self._advance_state_to(State.ARCHITECTING)

        # ---- M14 架构生成 + 确认（关键前置）----
        if is_architecture_confirmed(self.project_dir):
            self._advance_state_to(State.ARCH_CONFIRMED)
        else:
            m14 = wf(M14ArchitectureWorkflow)
            if not (self.project_dir / "architecture.md").exists():
                gen_ok = self._safe_step(
                    key=True, name="M14 架构生成",
                    fn=lambda: m14.generate(),
                ).ok
                # G4 熔断检查点：规划每步后
                if self._check_budget("plan_step"):
                    self._plan_tripped = True
                    self._plan_blocked = True
                    self._plan_block_reason = "Token 预算超限或墙钟超时熔断（规划阶段）"
                    return
                if not gen_ok:
                    self._plan_blocked = True
                    self._plan_block_reason = "M14 架构生成失败（关键前置），已安全退出，不进入写章。"
                    return
            conf_ok = self._safe_step(
                key=True, name="M14 架构确认",
                fn=lambda: m14.with_confirm_yes(True).confirm(),
            ).ok
            # G4 熔断检查点：规划每步后
            if self._check_budget("plan_step"):
                self._plan_tripped = True
                self._plan_blocked = True
                self._plan_block_reason = "Token 预算超限或墙钟超时熔断（规划阶段）"
                return
            if not conf_ok:
                self._plan_blocked = True
                self._plan_block_reason = "M14 架构确认失败（关键前置），已安全退出，不进入写章。"
                return
            self._advance_state_to(State.ARCH_CONFIRMED)

        # ---- M3 大纲生成（非关键）----
        if (self.project_dir / "outline.md").exists():
            self._advance_state_to(State.OUTLINING)
        else:
            self._safe_step(
                key=False, name="M3 大纲生成",
                fn=lambda: wf(M3OutlineWorkflow, method_enabled=self.method_enabled).run(),  # G11：方法模板注入
                degrade=self._write_placeholder_outline,
            )
            # G4 熔断检查点：规划每步后
            if self._check_budget("plan_step"):
                self._plan_tripped = True
                self._plan_blocked = True
                self._plan_block_reason = "Token 预算超限或墙钟超时熔断（规划阶段）"
                return
            self._advance_state_to(State.OUTLINING)

        # ---- M4 角色设计（非关键）----
        if self._m4_done():
            self._advance_state_to(State.CHARACTER_DESIGN)
        else:
            self._safe_step(
                key=False, name="M4 角色设计",
                fn=lambda: wf(M4CharacterWorkflow).run(),
                degrade=self._write_placeholder_characters,
            )
            # G4 熔断检查点：规划每步后
            if self._check_budget("plan_step"):
                self._plan_tripped = True
                self._plan_blocked = True
                self._plan_block_reason = "Token 预算超限或墙钟超时熔断（规划阶段）"
                return
            self._advance_state_to(State.CHARACTER_DESIGN)

        # 规划完成：推进到 WRITING（对齐拍板 #6），写章循环在 CHARACTER_DESIGN/WRITING 下运行。
        self._advance_state_to(State.WRITING)

    def _m4_done(self) -> bool:
        """判断 M4 产物是否已齐备（幂等跳过的依据，§2.2）。"""
        chars_dir = self.project_dir / "characters"
        if chars_dir.exists() and any(chars_dir.glob("*.md")):
            return True
        if (self.project_dir / "protagonist_route.md").exists():
            return True
        return False

    def _advance_state_to(self, target: State) -> None:
        """状态调和器：沿规范链单向推进到 ``target``（处理「产物存在但状态落后」的续跑态）。

        已越过 target（如 WRITING 续写场景）则保持不变，绝不降级状态。
        """
        self.state_machine.load()
        for _ in range(len(_EVENTS) + 1):
            cur = self.state_machine.state
            if cur == target or _CANON.index(cur) >= _CANON.index(target):
                break
            advanced = False
            for ev in _EVENTS:
                if (cur, ev) in TRANSITIONS:
                    try:
                        self.state_machine.transition(ev)
                        self.state_machine.save()
                        advanced = True
                        break
                    except ValueError:
                        break
            if not advanced:
                break

    def _safe_step(
        self,
        *,
        key: bool,
        name: str,
        fn: Callable[[], Any],
        retries: int = 2,
        degrade: Callable[[], None] | None = None,
    ) -> _PlanStepResult:
        """包装单步规划调用（失败不阻断，拍板 #2）。

        Args:
            key: True=关键前置（耗尽重试后安全退出，置 ``_plan_blocked``）；
                 False=非关键（耗尽重试后调用 ``degrade`` 占位并继续）。
            fn: 单步执行函数。
            retries: 统一重试上限（默认 2，即最多尝试 3 次）。
            degrade: 非关键最终失败时的降级占位回调（可选）。
        """
        last: Exception | None = None
        for attempt in range(retries + 1):
            try:
                return _PlanStepResult(ok=True, value=fn())
            except Exception as e:  # noqa: BLE001
                last = e
                self.console.print(
                    f"[yellow]⚠ 规划步骤[{name}] 第{attempt + 1}次失败：{e}[/yellow]"
                )
                self._alert_cost(name)
        if key:
            return _PlanStepResult(ok=False)
        if degrade is not None:
            try:
                degrade()
            except Exception:  # noqa: BLE001
                pass
        return _PlanStepResult(ok=False)

    # ---------------------------------------------------------------- 降级占位（非关键失败）
    def _write_placeholder_outline(self) -> None:
        """M3 耗尽重试后的降级：写最小 outline.md，使 M4._load_outline 不崩。"""
        f = self.project_dir / "outline.md"
        f.write_text(
            "---\nsublines: []\n---\n\n# 故事大纲（自主规划降级占位）\n\n"
            "## 故事简介\n（大纲生成失败，已降级占位；请手动 /outline 补生成）\n",
            encoding="utf-8",
        )

    def _write_placeholder_characters(self) -> None:
        """M4 耗尽重试后的降级：用 M4 模板渲染最小占位角色集，使 G2 Evaluator 有对象可读。"""
        from agent.workflows.planning.m4_character import M4CharacterWorkflow

        wf = M4CharacterWorkflow(
            self.project_dir,
            llm_client=self._traced_llm(),
            setting_manager=SettingManager(self.project_dir),
            state_machine=self.state_machine,
            console=self.console,
        )
        placeholder = [{
            "name": "主角（自主规划占位）",
            "role": "protagonist",
            "identity": "（占位）待规划补全",
            "core_motivation": "（占位）",
            "arc": {"start": "（占位）", "end": "（占位）"},
            "language_fingerprint": {
                "catchphrase": "", "sentence_style": "",
                "vocabulary": "", "banned_words": [],
            },
            "relations": "（占位）",
        }]
        try:
            title = ""
            try:
                title = (
                    SettingManager(self.project_dir).load_world()["metadata"].get("title", "")
                )
            except Exception:  # noqa: BLE001
                title = ""
            wf._render_characters(placeholder, title)
            wf._render_graph({})
            wf._render_foreshadows([])
            wf._render_golden_finger({})
            wf._render_route({})
        except Exception:  # noqa: BLE001
            pass

