"""AgenticPipelineWorkflow 拆分的 Mixin（机械搬移，行为零改动）。

拆分背景：单文件 1600+ 行不利维护（对齐 m5 Mixin 拆分模式，架构评审 P2-9）。
主文件保留类定义、``__init__`` 与 ``run()``；本文件（G9 进度事件流 + 审稿意见格式化 + G9 收尾）承载对应方法组。
仅供 ``AgenticPipelineWorkflow`` 继承组合，不要单独使用。
"""

from __future__ import annotations

import json

import frontmatter

from agent.workflows.pipeline.agentic_pipeline_types import PipelineResult, _now_iso

class _PipelineEventsMixin:
    def _emit_progress(self, phase: str, current: int, total: int) -> None:
        """触发进度回调（若订阅）。

        G4 进度可见性骨架：由 CLI 订阅并在 stderr 输出（避免污染 stdout JSON 信封）。

        Args:
            phase: 阶段名称（"planning"/"writing"/"evaluating"）。
            current: 当前进度。
            total: 总量。
        """
        if self.on_progress:
            try:
                self.on_progress(phase, current, total)
            except Exception:  # noqa: BLE001
                pass

    # ---------------------------------------------------------------- G9 事件发射（只读观察层）
    def _emit_event(self, type_: str, **fields: Any) -> None:
        """G9：发射事件（全 try/except，bus 内兜底；不阻断主流程）。"""
        try:
            self._event_bus.emit(type_, **fields)
        except Exception:  # noqa: BLE001
            pass

    def _emit_substage(self, partial: dict[str, Any]) -> None:
        """G9：writer 层子阶段事件入口（注入给 m5/agentic_write 的 event_emitter）。"""
        try:
            self._event_bus.emit_partial(partial)
        except Exception:  # noqa: BLE001
            pass

    def _emit_failure(self, step: str, reason: str, severity: str = "error") -> None:
        """G9：发射 failure 事件（含 next_steps；确定性零 LLM，不阻断主流程）。"""
        try:
            from agent.core.engine.events import next_steps_for

            self._emit_event(
                "failure",
                step=step,
                reason=reason,
                severity=severity,
                next_steps=next_steps_for(step, self.project_dir),
            )
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # G14：门禁打回重写辅助
    # ------------------------------------------------------------------
    def _format_guardrail_critique(self, violations: list[dict[str, Any]]) -> str:
        """把 Guardrails 违例编译成 Writer 可读的修正要求（决策①：重写提示）。"""
        lines = ["以下为上一版门禁（Guardrails）未通过项，请逐项修订后重新提交章节："]
        for i, v in enumerate(violations, 1):
            rid = v.get("rule_id", "?")
            msg = v.get("message", "")
            if rid == "non_chinese_junk":
                lines.append(f"{i}. 英文/杂质残留：{msg} —— 删除所有英文单词与系统提示片段，仅保留纯中文小说正文。")
            elif rid == "title_placeholder":
                lines.append(f"{i}. 标题占位/重复：{msg} —— 改写为一句有信息量、非模板化的场景化章节标题。")
            elif rid == "paragraph_dup":
                lines.append(f"{i}. 跨章重复：{msg} —— 重写该段落，避免与前述章节雷同（换场景/换视角/换措辞）。")
            elif rid == "meta_instruction_leak":
                lines.append(f"{i}. 写作元指令泄漏：{msg} —— 删除所有「章末悬念/留下悬念」等写作指令标记，它们不是小说正文，不得出现在成书中。")
            else:
                lines.append(f"{i}. [{rid}] {msg}")
        return "\n".join(lines)

    def _format_edit_critique(self, conflicts: list[Any]) -> str:
        """把编辑器一致性阻塞冲突编译成 Writer 可读的修正要求（post-write 硬门禁重写提示）。"""
        lines = ["以下为上一版一致性审查（ConsistencyChecker）未通过的阻断项，请逐项修订后重新提交章节："]
        for i, c in enumerate(conflicts, 1):
            rid = getattr(c, "rule_id", "?")
            desc = getattr(c, "description", "")
            if rid == "timeline_conflict":
                lines.append(
                    f"{i}. 时间线/生死矛盾：{desc} —— 以 characters/ 角色档案为唯一真源："
                    f"若本章确需交代该角色死亡，须先更新角色档案生死状态与对应章节，再回写正文；"
                    f"否则改写为与角色当前生死状态一致的内容。"
                )
            elif rid == "relation_conflict":
                lines.append(f"{i}. 关系网一致性：{desc} —— 核实该角色生死与 relations/graph.md 是否同步更新。")
            else:
                lines.append(f"{i}. [{rid}] {desc}")
        return "\n".join(lines)

    def _flag_chapter_quality(
        self, chapter: int, violations: list[dict[str, Any]], ch_text: str
    ) -> None:
        """门禁重写后仍不达标 → 标记告警（决策①：保留该章、不阻断，写 .state/chapter_quality_flags.json）。"""
        flag = {
            "chapter": chapter,
            "violations": [v.get("message", v.get("rule_id", "")) for v in violations],
            "flagged_at": _now_iso(),
        }
        self._quality_flags.append(flag)
        # 持久化（追加写，原子）
        try:
            from agent.core.quality.guardrails import DEFAULT_GUARDRAIL_CONFIG_PATH  # 仅引用，避免误用
            qf_path = self.project_dir / ".state" / "chapter_quality_flags.json"
            qf_path.parent.mkdir(parents=True, exist_ok=True)
            existing: list[dict[str, Any]] = []
            if qf_path.exists():
                try:
                    existing = json.loads(qf_path.read_text(encoding="utf-8")).get("flags", [])
                except Exception:  # noqa: BLE001
                    existing = []
            existing.append(flag)
            tmp = qf_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"flags": existing}, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(qf_path)
        except Exception:  # noqa: BLE001 - 持久化失败仅留内存记录，不阻断
            pass

    def _compute_eta_s(self, target: int) -> Optional[int]:
        """G9：ETA = 已写章平均耗时 × 剩余章数（拍板 4；无可计算时 None）。"""
        try:
            from agent.core.engine.events import compute_eta_s

            return compute_eta_s(self._event_bus.events, target, self._current_total())
        except Exception:  # noqa: BLE001 - ETA 计算失败降级 None，不阻断
            return None

    def _prev_pressure_stage(self) -> str:
        """G9：best-effort 读上一章 frontmatter 的 pressure_stage（首章/缺失置 ""）。

        共享知识 #11：pipeline 写前无法精确得知本章压力阶段；取上一章
        chapters/ch{total_written}.md frontmatter（缺失/首章置 ""），
        精确值随 chapter_substage（writer 层 ctx 已知）出现。
        """
        try:
            total = self._current_total()
            if total <= 0:
                return ""
            f = self.project_dir / "chapters" / f"ch{total:03d}.md"
            if not f.exists():
                return ""
            post = frontmatter.load(f)
            return str(post.metadata.get("pressure_stage", "") or "")
        except Exception:  # noqa: BLE001 - 读取失败降级为空串，不阻断
            return ""

    def _finalize_g9(self, result: PipelineResult) -> None:
        """G9：填充 result.failures / progress_file / summary + done 事件 + 最终落盘。

        纯读 bus，降级占位不阻断（G3 哲学）；done 事件先入 events，
        build_run_summary 再聚合（含 done 的总耗时/结局标志）。
        """
        try:
            import time

            from agent.core.engine.events import build_run_summary

            result.failures = [
                e for e in self._event_bus.events if e.get("type") == "failure"
            ]
            result.progress_file = (
                str(self._event_bus.progress_file)
                if self._event_bus.progress_file is not None
                else None
            )
            # done 事件（含 chapters_written/blocked/tripped/escalated/total_elapsed_s）
            self._emit_event(
                "done",
                chapters_written=result.chapters_written,
                blocked=result.blocked,
                tripped=result.tripped,
                escalated=result.escalated,
                total_elapsed_s=round(time.monotonic() - self._start_time),
            )
            result.summary = build_run_summary(self._event_bus.events, result)
            self._event_bus.flush(result.summary)
        except Exception:  # noqa: BLE001 - 摘要失败不阻断主流程（G3 哲学）
            pass

    # ---------------------------------------------------------------- 主流程
    def _finalize_cost(self, result: PipelineResult) -> None:
        """G7（拍板 4）：成本汇总（纯复用，异常降级占位不阻断）。"""
        try:
            from agent.core.llmops import build_cost_summary

            result.cost = build_cost_summary(
                self.project_dir, self._cost_tier, self._resolve_target()
            )
        except Exception:  # noqa: BLE001 - 成本汇总失败不阻断主流程（G3）
            result.cost = None

