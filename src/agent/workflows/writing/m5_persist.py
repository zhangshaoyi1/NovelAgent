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
from agent.workflows.writing.m5_text_hygiene import hard_replace_english  # noqa: F401 - 兼容旧导入路径

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# G15 章后归档：确定性事实抽取（纯规则，无 LLM，失败静默降级不阻断写章）
# ---------------------------------------------------------------------------
# 背景（2026-09-11 五灵破 ch181/182 章间矛盾复盘）：旧实现 must_carry/facts 全部
# 硬编码空列表（占位实现，从未接线）→ 上一章动态状态（物品/人数/动作/约定）对
# 下一章不可见 → writer 只能自创 → 触发人设/设定/连贯性硬指标失败。
# 本抽取器从「结尾 800 字」按确定性规则产出最小事实集，宁滥勿缺但封顶防爆。

_ITEM_PATTERN = re.compile(r"[匣盒锁钥匙剑刀玉符信图卷丹印珠瓶牌镜环链杖珠]")
_COUNT_PATTERN = re.compile(r"[0-9一二两三四五六七八九十百千几]+[人多双名只条枚颗道把柄根张块片个]")
_OPEN_LOOP_PATTERN = re.compile(r"未[有着能]|尚未|还没|不曾|正要|刚要|约定|决意|决定|誓要|下一步")
_SENT_SPLIT = re.compile(r"[。！？!?\n]")


def _chapter_body_text(chapter_num: int, chapter_text: str | None, chapters_dir: Path) -> str:
    """取本章正文体：优先调用方传入，缺省回读刚落盘的章节文件（去 frontmatter）。"""
    text = chapter_text
    if not text:
        f = chapters_dir / f"ch{chapter_num:03d}.md"
        if not f.exists():
            return ""
        text = f.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            text = parts[2]
    return re.sub(r"\s*\n\s*", "\n", text.strip())


def _tail_sentences(tail: str) -> list[str]:
    """结尾段切句，去空去重，保持原文顺序。"""
    out: list[str] = []
    for s in _SENT_SPLIT.split(tail):
        s = s.strip()
        if len(s) >= 6 and s not in out:
            out.append(s)
    return out


def _present_characters(body: str, ctx: dict[str, Any]) -> list[str]:
    """从 characters_info（**name** 标记行）解析角色名，返回在本章正文出场者。"""
    names: list[str] = []
    for line in (ctx.get("characters_info") or "").splitlines():
        m = re.search(r"\*\*(.+?)\*\*", line)
        if m:
            name = m.group(1).strip()
            if name and name in body and name not in names:
                names.append(name)
    return names


def _extract_chapter_facts(
    chapter_num: int, body: str, ctx: dict[str, Any]
) -> tuple[list[tuple[str, str, str, str, str]], list[str], list[str]]:
    """确定性抽取最小事实集。

    Returns:
        (facts_raw, must_carry, next_chapter_constraints)
        facts_raw: [(domain, subject_id, field, value, evidence)] → ContinuityFact
        must_carry / next_chapter_constraints: list[str] → ContinuityHandoff
    """
    facts_raw: list[tuple[str, str, str, str, str]] = []
    must_carry: list[str] = []
    constraints: list[str] = []
    if not body:
        return facts_raw, must_carry, constraints

    tail = body[-800:]
    sentences = _tail_sentences(tail)
    commit_id = f"ch{chapter_num:03d}"

    # 1) 出场角色（≤3）：presence 事实 + must_carry
    for name in _present_characters(body, ctx)[:3]:
        snip = next((s for s in sentences if name in s), "")[:80]
        facts_raw.append(
            ("character", name, "presence", f"第{chapter_num}章结尾段在场", snip or commit_id)
        )

    # 2) 计数/量化场景事实（≤2）：人数、物品件数等，防下章自创口径
    count_hits = [s for s in sentences if _COUNT_PATTERN.search(s)]
    for s in count_hits[:2]:
        key = _COUNT_PATTERN.search(s).group(0)
        facts_raw.append(("world", commit_id, "count", key, s[:100]))

    # 3) 关键物品句（≤2）：匣/锁/钥匙等叙事道具，must_carry 权威口径
    item_hits = [s for s in sentences if _ITEM_PATTERN.search(s) and s not in count_hits]
    for s in item_hits[:2]:
        must_carry.append(s[:120])

    # 4) 未闭环动作（≤3）：下一章约束（待续口径）
    for s in sentences:
        if _OPEN_LOOP_PATTERN.search(s) and s not in must_carry:
            constraints.append("待续：" + s[:110])
        if len(constraints) >= 3:
            break

    # 5) must_carry 兜底：仍为空时取结尾最后一到两句（结尾状态永远必须携带）
    if not must_carry and sentences:
        must_carry.append(sentences[-1][:120])

    return facts_raw, must_carry[:5], constraints


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
        # 缺口 B（2026-09-06）：统一改走 _finalize_chapter_text（canonical body 链，
        # 与门禁消费口径同源）。链内各步幂等，上游已 finalize 过时此为确定性兜底。
        text = self._finalize_chapter_text(text)
        word_count = len(text.replace("\n", "").replace(" ", ""))
        metadata["word_count"] = word_count
        # 缺口 B：成文（标题 + 正文）由 compose_chapter_markdown 唯一合成，
        # 落盘形态 == 门禁形态，guardrails 标题检查/查重自此生效。
        body = self.compose_chapter_markdown(ctx["chapter_num"], title, text)
        post = frontmatter.Post(body, **metadata)
        # P0-3（原子落盘）：temp + replace，杜绝进程中断留下截断的半成品章节文件。
        # 与 state_machine.save()（同样 temp+replace）配合，写序为「先正文后状态指针」，
        # 即使中途崩溃也只会出现"章节已存在、进度落后"的可恢复态，不会出现反向损坏。
        from agent.core.infra.atomic import atomic_write_text

        atomic_write_text(file, frontmatter.dumps(post))
        return file
    def _archive_chapter(
        self,
        ctx: dict[str, Any],
        chapter_title: str,
        chapter_text: str | None = None,
    ) -> None:
        """G15 章后归档 hook：本章最小交接归档进连续性账本 + 伏笔 beats 标记落地。

        - 向 `ContinuityLedgerStore.commit` 写入本章交接（source_commit_id=本章 ID），
          `latest_handoff()` 即成为下一章投影的「上一章交接」来源。
        - facts / must_carry / next_chapter_constraints 由确定性抽取器从本章正文
          （优先 ``chapter_text``，缺省回读落盘文件）产出，非空（能力对账红线
          ``test_capability_parity`` 强制：禁止字面量空列表回潮）。
        - 把规划锚指向本章（``anchor_chapter == 本章``）的伏笔 beat 标记为 committed，
          由纯函数 `derive_status` 自动推进线程状态。
        - 缺账本 / 任何异常 → 静默降级，绝不阻断写章（与「降级不阻断」一致）。
        """
        try:
            from agent.core.continuity import ContinuityFact, ContinuityHandoff, ContinuityLedgerStore
            from agent.core.story.foresight import ForesightBeat, ForesightStore, mark_committed

            chapter_num = ctx["chapter_num"]
            commit_id = f"ch{chapter_num:03d}"

            body = _chapter_body_text(chapter_num, chapter_text, self.chapters_dir)
            facts_raw, must_carry, constraints = _extract_chapter_facts(chapter_num, body, ctx)
            facts = [
                ContinuityFact(
                    domain=domain,
                    subject_id=subject_id,
                    field=field_name,
                    value=value,
                    source_commit_id=commit_id,
                    evidence=evidence,
                )
                for domain, subject_id, field_name, value, evidence in facts_raw
            ]
            tail = body[-260:] if body else ""
            summary = (
                f"第{chapter_num}章《{chapter_title}》结尾状态：{tail}" if tail
                else f"第{chapter_num}章《{chapter_title}》"
            )

            ledger = ContinuityLedgerStore(self.project_dir)
            ledger.load()
            ledger.commit(
                chapter=chapter_num,
                facts=facts,
                knowledge=[],
                open_loops=[],
                handoff=ContinuityHandoff(
                    chapter=chapter_num,
                    summary=summary,
                    must_carry=must_carry,
                    next_chapter_constraints=constraints,
                    source_commit_id=commit_id,
                ),
            )

            from agent.workflows.evaluation.m13_foreshadow import seed_foresight_threads

            seed_foresight_threads(self.project_dir)  # P1-4：先播种，mark 才有 beat 可标
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
        # F-7：total_written 只增不回流（max）——并发双进程写同一项目时，
        # 先写完的进程可能被后写的进程用较小 chapter_num 覆盖回退；
        # max(当前, chapter_num) 保证进度单调推进（用户手动归零除外，写章从 1 起）。
        progress.update({
            "current_subline": ctx["subline_id"],
            "current_chapter": ctx["chapter_num"],
            "total_written": max(int(progress.get("total_written", 0) or 0), int(ctx["chapter_num"])),
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
