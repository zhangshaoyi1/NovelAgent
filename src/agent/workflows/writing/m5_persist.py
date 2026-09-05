from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import frontmatter
from rich.panel import Panel

from agent.core.quality.consistency import ConflictReport
from agent.core.story.evidence_chain import EvidenceChain, EvidenceRef
from agent.workflows.writing.m5_text_hygiene import hard_replace_english

logger = logging.getLogger(__name__)


@dataclass
class PreValidationResult:
    """E3 前置冲突检测结论"""

    decision: str  # "continue" | "interrupt"
    report: ConflictReport
    auto_resolved: list[str] = field(default_factory=list)



class M5PersistMixin:
    """依据链 / 持久化 / 归档 / 进度 / 呈现 / E3 前置门禁（由 m5_write_chapter 拆出，仅由 M5WriteChapterWorkflow 组合使用）"""

    # ============================================================
    # 5. 依据链
    # ============================================================
    def _build_evidence_chain(self, ctx: dict[str, Any]) -> EvidenceChain:
        """构建本章引用的设定条目（E4 结构化分类引用）

        分类：
            - settings：世界观 / 境界 / 金手指 / 支线 / 路线 / 关系网
            - characters：本章涉及角色档案
            - foreshadows：伏笔登记表 + 本章伏笔任务涉及的 F-ID
        """
        wi = ctx["world_info"]
        settings: list[EvidenceRef] = [
            EvidenceRef(name=wi.get("title", ""), field="世界观/故事简介", source="world.md"),
        ]
        if wi.get("realm_system"):
            settings.append(
                EvidenceRef(name="境界体系", field="境界体系（冻结）", source="world.md")
            )
        if wi.get("golden_finger_info"):
            settings.append(EvidenceRef(name="金手指", field="金手指登记", source="world.md"))
        settings.append(
            EvidenceRef(
                name=ctx["subline_name"],
                field="支线目标",
                source=f"sublines/{ctx['subline_id']}/subline.md",
            )
        )
        settings.append(
            EvidenceRef(
                name=ctx["route_node_id"],
                field="主角路线节点",
                source="protagonist_route.md",
            )
        )
        settings.append(
            EvidenceRef(name="关系网", field="关系当前状态", source="relations/graph.md")
        )

        characters: list[EvidenceRef] = []
        for line in ctx["characters_info"].splitlines():
            m = re.search(r"\*\*(.+?)\*\*", line)
            if m:
                name = m.group(1).strip()
                characters.append(
                    EvidenceRef(name=name, field="身份/动机", source=f"characters/{name}.md")
                )

        foreshadows: list[EvidenceRef] = [
            EvidenceRef(name="伏笔登记表", field="全局伏笔", source="foreshadows.md"),
        ]
        for fid in re.findall(r"F-\d+", ctx.get("foreshadow_task", "")):
            foreshadows.append(
                EvidenceRef(ref_id=fid, field="本章伏笔任务", source="foreshadows.md")
            )

        return EvidenceChain(characters=characters, foreshadows=foreshadows, settings=settings)
    # ============================================================
    # 6. 持久化
    # ============================================================
    def _save_chapter(
        self,
        ctx: dict[str, Any],
        text: str,
        title: str,
        word_count: int,
        quality_passed: bool,
        revision_attempts: int,
        evidence_chain: EvidenceChain,
    ) -> Path:
        """保存章节文件（frontmatter 含 E4 结构化证据链）"""
        self.chapters_dir.mkdir(parents=True, exist_ok=True)
        file = self.chapters_dir / f"ch{ctx['chapter_num']:03d}.md"

        metadata = {
            "chapter": ctx["chapter_num"],
            "subline": ctx["subline_id"],
            "route_node": ctx["route_node_id"],
            "pressure_stage": ctx["pressure_stage"],
            "title": title,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "word_count": word_count,
            "quality_passed": quality_passed,
            "revision_attempts": revision_attempts,
            "evidence_chain": evidence_chain.to_dict(),
        }
        # ---- G-EN：落盘前绝对零英文关卡（单一写盘点，任何写章路径都过此门）----
        # 同时做元信息清理兜底（剔除模型误输出的标题/原文标题/编辑批注），
        # 保证不同写章入口（M5 / agentic_write）成稿都干净、字数统计准确。
        text = self._clean_chapter_body(text)
        # P-DEDUP：剔除 LLM 把整章正文重复输出两遍的情况（正文中途再次出现章节标题）。
        # 必须在字数统计之前，保证字数基于去重后的最终文本。
        text = self._dedup_repeated_chapter(text)
        # P-DEDUP-2：剔除章节尾部把前面已写段落整段复读的循环重复（无标题锚点）。
        # 与 P-DEDUP 并排，同为落盘前确定性去重，避免重复段虚增字数、影响门禁准判。
        text = self._dedup_tail_loop(text)
        # P-FMT：统一写盘点强制段落格式化（M5 / agentic_write 共用本方法）。
        # 兜底 LLM 完全不输出段落分隔的情况（正文被压成单段），按句自动分段。
        # 注意：这段必须在 word_count 计算之前，保证字数统计基于最终落盘文本。
        text = self._format_chapter_body(text)
        # 不论上游 _quality_check_and_revise 的 G-EN 块是否生效，这里都再做一次确定性兜底，
        # 保证写到磁盘的正文一定零英文（已知词翻译、未知串剔除）。
        clean_text, _still = hard_replace_english(text)
        if clean_text != text:
            logger.warning(
                "[no_english] _save_chapter 落盘前确定性清理英文残留(上游兜底未生效)"
            )
        text = clean_text
        word_count = len(text.replace("\n", "").replace(" ", ""))
        metadata["word_count"] = word_count
        body = f"# 第 {ctx['chapter_num']} 章 · {title}\n\n{text}"
        post = frontmatter.Post(body, **metadata)
        file.write_text(frontmatter.dumps(post), encoding="utf-8")
        return file
    def _archive_chapter(self, ctx: dict[str, Any], chapter_title: str) -> None:
        """G15 章后归档 hook：本章最小交接归档进连续性账本 + 伏笔 beats 标记落地。

        - 向 `ContinuityLedgerStore.commit` 写入本章交接（source_commit_id=本章 ID），
          `latest_handoff()` 即成为下一章投影的「上一章交接」来源。
        - 把规划锚指向本章（``anchor_chapter == 本章``）的伏笔 beat 标记为 committed，
          由纯函数 `derive_status` 自动推进线程状态。
        - 缺账本 / 任何异常 → 静默降级，绝不阻断写章（与「降级不阻断」一致）。
        """
        try:
            from agent.core.continuity import ContinuityHandoff, ContinuityLedgerStore
            from agent.core.story.foresight import ForesightBeat, ForesightStore, mark_committed

            chapter_num = ctx["chapter_num"]
            commit_id = f"ch{chapter_num:03d}"

            ledger = ContinuityLedgerStore(self.project_dir)
            ledger.load()
            ledger.commit(
                chapter=chapter_num,
                facts=[],
                knowledge=[],
                open_loops=[],
                handoff=ContinuityHandoff(
                    chapter=chapter_num,
                    summary=f"第{chapter_num}章《{chapter_title}》",
                    must_carry=[],
                    next_chapter_constraints=[],
                    source_commit_id=commit_id,
                ),
            )

            store = ForesightStore(self.project_dir)
            threads = store.load()
            changed = False
            for t in threads:
                for b in t.beats:
                    if b.anchor_chapter == chapter_num and b.exec_status != "committed":
                        mark_committed(t, ForesightBeat.model_validate(b), commit_id)
                        changed = True
            if changed:
                store.save(threads)
        except Exception:  # noqa: BLE001 - 归档失败降级不阻断
            logger.debug("[continuity] 章后归档失败，已降级（不影响本章产出）", exc_info=True)
    def _pre_validation(self, ctx: dict[str, Any]) -> PreValidationResult:
        """E3 前置冲突检测门禁

        Returns:
            PreValidationResult：
                - 无冲突 → continue
                - 高严重度冲突 → interrupt（需用户仲裁）
                - 低/中冲突 → 自动仲裁（写入 world.md 修订日志）后 continue
        """
        planned = self._build_planned_setting(ctx)
        report = self.conflict_arbiter.check_new_setting(  # type: ignore[union-attr]
            planned, subline_id=ctx["subline_id"]
        )

        if not report.has_conflict:
            return PreValidationResult("continue", report)

        if report.needs_arbitration:
            # 高严重度：记录到 world.md 修订日志并中断生成
            high_fields = ", ".join(
                c.field for c in report.conflicts if c.severity == "high"
            )
            self.sm.append_revision_log(
                f"[仲裁-高] 前置冲突检测拦截生成：{report.summary}"
                f"（高严重度字段：{high_fields}）"
            )
            return PreValidationResult("interrupt", report)

        # 低/中严重度：自动采用新设定，记录仲裁结果
        for c in report.conflicts:
            self.sm.append_revision_log(
                f"[仲裁-自动] 字段 {c.field}（{c.severity}）："
                f"{c.suggestion or '自动采用新设定，继续生成'}"
            )
        return PreValidationResult(
            "continue", report, auto_resolved=[c.field for c in report.conflicts]
        )
    # ============================================================
    # E4 证据链校验
    # ============================================================
    def _validate_evidence(self, chain: EvidenceChain) -> EvidenceChain:
        """F-E4.3 落盘前校验所有引用源文件是否存在

        缺失的源仅记录告警，不阻断落盘（引用源本就来自已加载文件）。
        """
        missing: list[str] = []
        for r in chain.all_refs():
            if r.source and not (self.project_dir / r.source).exists():
                missing.append(r.source)
        chain.missing_sources = missing
        if missing:
            self.console.print(
                f"[yellow]⚠ 证据链中有 {len(missing)} 个引用源不存在："
                f"{', '.join(missing)}[/yellow]"
            )
        return chain
    # ============================================================
    # 7. 更新进度
    # ============================================================
    def _update_progress(self, ctx: dict[str, Any]) -> None:
        """更新 state.json 的 progress 字段。

        G8（拍板 4/补充边界 1）关键兼容点：**合并写入**（progress.update），
        保留 mainline_visited / ending_mode / mainline_* / ending_* 等既有键；
        禁止全新 dict 覆盖（否则 G8 状态每次写章被抹掉）。
        """
        self.state_machine.load()
        progress = dict(self.state_machine.progress or {})
        progress.update({
            "current_subline": ctx["subline_id"],
            "current_chapter": ctx["chapter_num"],
            "total_written": ctx["chapter_num"],
            "last_written_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        # G8：mainline_visited 双保险初始化（未记录时以当前支线打底；已存在则保留）
        visited = progress.get("mainline_visited")
        if not isinstance(visited, list):
            visited = []
        if ctx["subline_id"] and ctx["subline_id"] not in visited:
            visited.append(ctx["subline_id"])
        progress["mainline_visited"] = visited
        self.state_machine.progress = progress
        self.state_machine.save()
    # ============================================================
    # 8. 呈现
    # ============================================================
    def _present(
        self,
        chapter_file: Path,
        ctx: dict[str, Any],
        word_count: int,
        quality_passed: bool,
        revision_attempts: int,
    ) -> None:
        """展示章节摘要"""
        status = "[green]✓ 通过[/green]" if quality_passed else "[yellow]△ 未完全通过[/yellow]"
        self.console.print(
            Panel(
                f"第 {ctx['chapter_num']} 章 · {ctx['pressure_stage']}阶段\n"
                f"字数：{word_count} | 质量：{status} | 修订：{revision_attempts} 次\n"
                f"文件：{chapter_file.relative_to(self.project_dir)}",
                title=f"ch{ctx['chapter_num']:03d}.md",
                border_style="green" if quality_passed else "yellow",
                expand=False,
            )
        )
