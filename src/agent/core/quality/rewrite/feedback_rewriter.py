"""A3 反馈→定向改写闭环（用户好用核心能力）

把用户针对某一章的反馈（"这章太拖" / "主角太蠢" / "感情戏不够"）变成**局部定向重写**，
而不是只能整章回退或整本重跑。这是把"枪手"变成"听话的枪手"的关键黏性闭环。

设计要点（契合项目"降级不阻断"哲学）：
- 复用 GatewayAdapter（chat_creative 重写 + 可选 chat_utility 生成改动摘要）、
  SettingManager（世界观上下文）、Guardrails（改写后合规校验）、
  LearningStore（把"用户偏好"沉淀进长期记忆，下次自动吸收）。
- **离线/无 LLM 优雅降级**：LLM 调用失败时 `llm_used=False`，保留原章并返回错误说明，
  绝不抛异常中断用户流程（除非用户显式要求 BLOCK 门禁且改写产物违规则不落盘）。
- 改写前自动备份原章到 ``.state/rewrite_backups/``，可随时回退。
- 默认 ``gate_mode="advisory"``：违规仅告警不阻断；切 ``block`` 时命中 error 级违规**拒绝落盘**。

复用方式：
    rewriter = FeedbackRewriter(project_dir, llm_client=...)
    result = rewriter.rewrite(chapter_num=12, feedback="节奏太慢，删水")
    # result.new_text / result.guardrail_passed / result.backup_file
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import frontmatter
from rich.console import Console

from llmagent.gateway import Gateway

from agent.client.gateway_adapter import chat_creative
from agent.core.quality.guardrails import GateMode, Guardrails, build_guardrails
from agent.core.story.setting_manager import SettingManager
from agent.core.infra.degrade import degrade
from agent.core.infra.prompt_manager import pm


# ============================================================
# 结果数据类
# ============================================================
@dataclass
class RewriteResult:
    """定向重写结果。"""

    chapter_file: Path
    chapter_num: int
    old_word_count: int
    new_word_count: int
    new_text: str
    changed_summary: str
    guardrail_passed: bool
    guardrail_report: dict[str, Any] = field(default_factory=dict)
    backup_file: Path | None = None
    blocked: bool = False            # BLOCK 门禁下违规被拒落盘
    llm_used: bool = True            # LLM 不可用降级时为 False
    error: str = ""                  # LLM 失败原因（降级时填充）

    def to_dict(self) -> dict[str, Any]:
        return {
            "chapter": self.chapter_num,
            "chapter_file": str(self.chapter_file),
            "old_word_count": self.old_word_count,
            "new_word_count": self.new_word_count,
            "changed_summary": self.changed_summary,
            "guardrail_passed": self.guardrail_passed,
            "guardrail_report": self.guardrail_report,
            "backup_file": str(self.backup_file) if self.backup_file else None,
            "blocked": self.blocked,
            "llm_used": self.llm_used,
            "error": self.error,
            "rewritten": (not self.blocked and self.llm_used and not self.error),
        }


def _wc(text: str) -> int:
    return len(text.replace("\n", "").replace(" ", ""))


#: 提示词（唯一真源在 ``prompts/quality/``）——patch=局部定向修补；full=整章重写；expand=整章扩写
_PROMPT_PATCH = "quality.rewrite"
_PROMPT_FULL = "quality.rewrite_full"
_PROMPT_EXPAND = "quality.rewrite_expand"

#: 整章重写的字数补足轮数上限（每轮为**整章扩写**，非追加拼段；详见 _ensure_full_length）
_FULL_EXPAND_ROUNDS = 2

#: 空转重试的追加硬指令（模型原样回吐原文时追加，详见 rewrite() 的「2.5 空转判定」）
_NOOP_RETRY_HINT = (
    "\n\n【上一次整章重写你原样返回了原文，这是不合格的】\n"
    "本次必须逐段重写：更换句式、描写角度与细节，替换比喻；"
    "不得出现与上方原文相同的长句或段落。"
)

#: 全书指纹库路径（与 autowrite / compose 体检同源，决策③：存 .state/ 下）
_FINGERPRINT_REL_PATH = Path(".state") / "chapter_fingerprints.json"


# ============================================================
# 核心组件
# ============================================================
class FeedbackRewriter:
    """反馈驱动的章节定向重写器。

    Args:
        project_dir: 小说项目目录。
        llm_client: LLM 客户端（创作模型）；None 则内部惰性构造 create_gateway()。
        guardrails: 护栏实例；None 则用默认 Guardrails()（仅创作残留级校验）。
        console: rich 控制台。
        learning_store: 长期偏好记忆；None 则内部惰性构造。
    """

    def __init__(
        self,
        project_dir: str | Path,
        llm_client: Gateway | None = None,
        guardrails: Guardrails | None = None,
        console: Console | None = None,
        learning_store: Any | None = None,
    ) -> None:
        self.project_dir = Path(project_dir)
        self._llm = llm_client
        self.guardrails = guardrails or build_guardrails()
        self.console = console or Console()
        self._learning_store = learning_store
        self.chapters_dir = self.project_dir / "chapters"
        self.backup_dir = self.project_dir / ".state" / "rewrite_backups"
        self._sm = SettingManager(self.project_dir)

    # ---------------------------------------------------------- LLM 惰性
    @property
    def llm(self) -> Gateway:
        if self._llm is None:
            from agent.client.gateway_adapter import create_gateway, chat_creative
            self._llm = create_gateway()
        return self._llm

    @property
    def learning_store(self):
        if self._learning_store is None:
            from agent.core.story.learning_store import LearningStore

            self._learning_store = LearningStore(self.project_dir)
        return self._learning_store

    # ---------------------------------------------------------- 入口
    def rewrite(
        self,
        chapter_num: int,
        feedback: str,
        *,
        backup: bool = True,
        gate_mode: str = "advisory",
        record_learning: bool = True,
        confirm_fn: Any = None,
        mode: str = "patch",
    ) -> RewriteResult:
        """对指定章节做反馈驱动的重写。

        Args:
            chapter_num: 章节号（1-based）。
            feedback: 用户反馈文本（自由语言）。
            backup: 是否先备份原章。
            gate_mode: ``advisory``（默认，违规告警不阻断）/ ``block``（违规拒绝落盘）。
            record_learning: 改写成功后是否把反馈沉淀为长期偏好。
            confirm_fn: P1-6 确认闸口——``Callable[[RewriteInstruction], bool]``；
                非None 时反馈先结构化，确认通过才发起 LLM 改写（"原始评审文本不得
                直接成为模型指令"）。低自主度场景由调用方注入确认逻辑。
            mode: ``patch``（默认，局部定向修补，最小改动）/ ``full``（整章重写：
                注入本章逐章契约 + 字数区间，并要求"不复述原文、不复述邻章"）。
                合法性以外的一律回落 ``patch``。

        Returns:
            RewriteResult；LLM 不可用时 ``llm_used=False``、``new_text`` 回退原章正文。
        """
        mode = "full" if str(mode).lower() == "full" else "patch"
        gate = GateMode(gate_mode) if gate_mode in ("advisory", "block") else GateMode.ADVISORY
        chapter_file = self.chapters_dir / f"ch{chapter_num:03d}.md"
        if not chapter_file.exists():
            raise FileNotFoundError(f"章节文件不存在：{chapter_file}")

        post = frontmatter.load(chapter_file)
        old_text = self._strip_frontmatter(chapter_file)
        old_wc = _wc(old_text)

        # 1. 上下文锚点
        ctx = self._build_context(chapter_num, post)

        # 1.5 P1-6：反馈先结构化为"修改指令"，确认后才进 prompt
        #（原始评审/反馈文本不直接成为模型指令）
        from agent.core.quality.rewrite.instruction import (
            render_instruction,
            structure_feedback,
        )

        instruction = structure_feedback(feedback, chapter_num)
        if confirm_fn is not None and not confirm_fn(instruction):
            return RewriteResult(
                chapter_file=chapter_file,
                chapter_num=chapter_num,
                old_word_count=old_wc,
                new_word_count=old_wc,
                new_text=old_text,
                changed_summary="（用户未确认修改指令，未改写）",
                guardrail_passed=True,
                backup_file=None,
                blocked=False,
                llm_used=False,
                error="confirm_rejected",
            )

        # 1.6 注入全书指纹库（排除本章自身），使跨章去重在改写路径真正生效
        self._inject_fingerprint_db(chapter_num)

        # 1.7 整章重写：注入本章逐章契约 + 字数区间（无契约则显性降级留痕）
        if mode == "full":
            ctx["chapter_contract"] = self._chapter_contract(chapter_num, post)
            ctx["word_range"] = self._word_range(ctx["chapter_length"])

        # 2. 调 LLM 重写（失败优雅降级）
        try:
            prompt_feedback = (
                render_instruction(instruction, keep_others=(mode != "full")) or feedback
            )
            new_text = self._call_rewrite(old_text, prompt_feedback, ctx, mode=mode)
            if mode == "full":
                # 整章重写产出的篇幅补足（不足下限时整章扩写，详见 _ensure_full_length）
                new_text = self._ensure_full_length(new_text, ctx, chapter_num=chapter_num)
                if self._is_noop(new_text, old_text):
                    # 模型原样回吐原文（2026-09-24 实证：2290 字原样返回、却报「✓ 已落盘」）
                    # ——追加硬指令重试一次；仍空转则由下方「2.5 空转判定」显性失败，绝不落盘。
                    new_text = self._call_rewrite(
                        old_text, prompt_feedback + _NOOP_RETRY_HINT, ctx, mode="full"
                    )
                    new_text = self._ensure_full_length(new_text, ctx, chapter_num=chapter_num)
            llm_used = True
            error = ""
        except Exception as e:  # noqa: BLE001 - LLM 不可达/异常：降级保留原章
            self.console.print(f"[yellow]⚠ 定向重写 LLM 调用失败，保留原章：{e}[/yellow]")
            return RewriteResult(
                chapter_file=chapter_file,
                chapter_num=chapter_num,
                old_word_count=old_wc,
                new_word_count=old_wc,
                new_text=old_text,
                changed_summary="（LLM 不可用，未改写）",
                guardrail_passed=True,
                backup_file=None,
                blocked=False,
                llm_used=False,
                error=str(e),
            )

        # ---- 2.5 空转判定（仅整章重写）----
        # 模型原样回吐原文时链路此前照常落盘并报「✓ 已落盘，字数 X→X」——用户以为
        # 改过了、其实一字未动（比短稿更糟：短稿至少有痕迹）。此处显性失败：
        # 不落盘、不改备份、error=noop_output（CLI 据此报「未改写，保留原章」）。
        if mode == "full" and self._is_noop(new_text, old_text):
            self.console.print(
                f"[yellow]⚠ 第 {chapter_num} 章整章重写两次输出均与原文一致"
                f"（模型原样回吐），未发生改写，不落盘。[/yellow]"
            )
            degrade(
                "rewrite.noop",
                f"第 {chapter_num} 章整章重写两次输出均与原文一致（模型原样回吐），"
                f"本章未改写（{old_wc} 字），请更换模型或调整反馈后重试",
            )
            return RewriteResult(
                chapter_file=chapter_file,
                chapter_num=chapter_num,
                old_word_count=old_wc,
                new_word_count=old_wc,
                new_text=old_text,
                changed_summary="（模型原样回吐原文，未改写，未落盘）",
                guardrail_passed=True,
                backup_file=None,
                blocked=False,
                llm_used=True,
                error="noop_output",
            )

        # 3. 护栏校验
        gr = self.guardrails.check(new_text)
        passed = gr.passed
        # ---- 3.1 字数硬检查（仅整章重写，2026-09-24）----
        # 写章路径的字数门禁（下限 BLOCK / 上限 WARN）在 rewrite 路径**从未生效**：
        # 重写产物无论多短都直接落盘（灵荒工坊 16 章未通过稿 CJK 2041–2918，
        # 重写首稿实测仅 1120–1255 字）。此处复用写时门禁的**同一条规则**
        # ``_check_word_count``（阈值与 severity 同一个真源，不另写比例常量）。
        # 仅整章重写适用：patch 是用户指定的「最小改动」，不因篇幅被拦。
        length_issues: list[Any] = []
        if mode == "full":
            from agent.core.quality.scoring.quality_checker import Severity

            length_issues = self._length_issues(new_text, ctx)
            short_issue = next(
                (i for i in length_issues if i.severity is Severity.BLOCK), None
            )
            if short_issue is not None and gate == GateMode.BLOCK:
                self.console.print(
                    f"[red]✗ BLOCK 门禁：第 {chapter_num} 章改写产物{short_issue.description}，"
                    f"拒绝落盘（原章保留）。[/red]"
                )
                return RewriteResult(
                    chapter_file=chapter_file,
                    chapter_num=chapter_num,
                    old_word_count=old_wc,
                    new_word_count=old_wc,
                    new_text=old_text,
                    changed_summary="（BLOCK 门禁拦截：字数不足，未落盘）",
                    guardrail_passed=passed,
                    guardrail_report=gr.to_dict(),
                    backup_file=None,
                    blocked=True,
                    llm_used=llm_used,
                    error="word_count_short",
                )
            for _li in length_issues:
                self.console.print(
                    f"[yellow]⚠ 字数告警：第 {chapter_num} 章改写产物{_li.description}[/yellow]"
                )
        # ---- L2：生成残留硬污染确定性扫描（2026-09-14，rewrite 路径补缺）----
        # 写章路径（agentic/m5）已接入 scan_hard_pollutions 硬关卡，rewrite 此前
        # 只靠 LLM 护栏（G14）→「你别说」「【下一章预告：…】」标题重复等低级硬伤
        # 在重写产物里漏网（灵荒薪传 ch001 双标题 + 预告泄漏实证）。
        from agent.core.story.text_hygiene import scan_hard_pollutions

        hard_poll = scan_hard_pollutions(new_text)
        if hard_poll:
            _hp_txt = "；".join(hard_poll)
            if gate == GateMode.BLOCK:
                self.console.print(
                    f"[red]✗ BLOCK 门禁：第 {chapter_num} 章改写产物含生成残留硬污染"
                    f"（{_hp_txt}），拒绝落盘（原章保留）。[/red]"
                )
                return RewriteResult(
                    chapter_file=chapter_file,
                    chapter_num=chapter_num,
                    old_word_count=old_wc,
                    new_word_count=old_wc,
                    new_text=old_text,
                    changed_summary="（BLOCK 门禁拦截：生成残留硬污染，未落盘）",
                    guardrail_passed=passed,
                    guardrail_report=gr.to_dict(),
                    backup_file=None,
                    blocked=True,
                    llm_used=llm_used,
                    error="hard_pollution_blocked",
                )
            self.console.print(
                f"[yellow]⚠ 生成残留硬污染告警（advisory）：{_hp_txt}；"
                f"落盘前将自动清理可安全删除项[/yellow]"
            )
        if gate == GateMode.BLOCK and not passed:
            self.console.print(
                f"[red]✗ BLOCK 门禁：第 {chapter_num} 章改写产物未通过合规校验，"
                f"拒绝落盘（原章保留）。[/red]"
            )
            return RewriteResult(
                chapter_file=chapter_file,
                chapter_num=chapter_num,
                old_word_count=old_wc,
                new_word_count=old_wc,
                new_text=old_text,
                changed_summary="（BLOCK 门禁拦截，未落盘）",
                guardrail_passed=passed,
                guardrail_report=gr.to_dict(),
                backup_file=None,
                blocked=True,
                llm_used=llm_used,
                error="",
            )

        # 4. 备份原章
        backup_file = None
        if backup:
            backup_file = self._backup(chapter_file)

        # 5. 落盘（更新 frontmatter 改写痕迹）
        self._save_rewritten(chapter_file, post, new_text, feedback)

        # 5.5 刷新本章指纹（否则指纹库与成书脱节：后续写章按**已不存在的旧文**
        # 判重 ⇒ 误报；新正文不入库 ⇒ 漏报。与写章路径 register_fingerprints 同口径）
        self._refresh_fingerprint(chapter_num, new_text)

        new_wc = _wc(new_text)
        changed_summary = (
            f"{'整章重写' if mode == 'full' else '按反馈重写'}：字数 {old_wc}→{new_wc}"
            + ("（含合规告警）" if (not passed or length_issues) else "")
        )

        # 6. 沉淀用户偏好（闭环：让枪手越来越听话）
        if record_learning:
            self._record_learning(chapter_num, feedback, changed_summary)

        if not passed:
            self.console.print(
                f"[yellow]△ 第 {chapter_num} 章改写已落盘，但护栏有告警："
                f"{', '.join(v.rule_id for v in gr.warnings)}[/yellow]"
            )

        return RewriteResult(
            chapter_file=chapter_file,
            chapter_num=chapter_num,
            old_word_count=old_wc,
            new_word_count=new_wc,
            new_text=new_text,
            changed_summary=changed_summary,
            guardrail_passed=passed,
            guardrail_report=gr.to_dict(),
            backup_file=backup_file,
            blocked=False,
            llm_used=llm_used,
            error=error,
        )

    # ---------------------------------------------------------- 指纹库同步
    def _inject_fingerprint_db(self, chapter_num: int) -> None:
        """把全书指纹库注入护栏（**排除本章自身**）。

        rewrite 此前用裸 ``Guardrails()`` ⇒ ``fingerprint_db`` 恒空，
        ``_check_dup``（跨章段落去重）在本路径形同虚设：带跨章重复的章改不出来，
        还可能改出新的重复（灵荒工坊 ch015/ch016 相似度 0.91 实证）。

        ``_check_dup`` 的契约要求调用方排除自身章——否则本章旧段落与旧指纹自比，
        相似度恒为 1.0 而误报。注意：这里就地更新本实例持有的护栏对象。

        2026-09-24 改用 ``load_book_fingerprints``（按章文件重建）而非直接读缓存：
        缓存只在「写章 / 改写 / 回滚」增量更新，带外改动会让它与成书脱节，而门禁
        盲信缓存 ⇒ ch021 与 ch036 相似度 0.99 的重复段落照常落盘（灵荒工坊实证）。
        """
        from agent.core.quality.guardrails import load_book_fingerprints

        db = load_book_fingerprints(self.project_dir, exclude=chapter_num)
        self.guardrails.fingerprint_db = db

    def _refresh_fingerprint(self, chapter_num: int, text: str) -> None:
        """改写落盘后刷新本章指纹（load/save 内部已各自降级，失败不阻断改写交付）。"""
        from agent.core.quality.guardrails import load_fingerprints, save_fingerprints

        path = self.project_dir / _FINGERPRINT_REL_PATH
        db = load_fingerprints(path)
        self.guardrails.register_fingerprints(str(chapter_num), text)
        db[str(chapter_num)] = self.guardrails.fingerprint_db.get(str(chapter_num), [])
        save_fingerprints(db, path)

    # ---------------------------------------------------------- 内部：LLM 调用
    def _call_rewrite(
        self, old_text: str, feedback: str, ctx: dict[str, Any], *, mode: str = "patch"
    ) -> str:
        full = mode == "full"
        _p = pm.get(_PROMPT_FULL if full else _PROMPT_PATCH)
        user_prompt = _p.render_user(
            chapter_text=old_text[:12000],
            feedback=feedback,
            # 整章重写专用槽位（patch 提示词不引用，渲染为空、零影响）
            chapter_contract=ctx.get("chapter_contract", ""),
            word_range=ctx.get("word_range", ""),
            genre=ctx["genre"],
            tone=ctx["tone"],
            rhythm=ctx["rhythm"],
            chapter_length=ctx["chapter_length"],
            synopsis=ctx["synopsis"],
            characters=ctx["characters"],
            prev_tail=ctx["prev_tail"],
            next_head=ctx["next_head"],
            learnings=ctx["learnings"],
        )
        resp = chat_creative(
            self.llm,
            messages=[
                {"role": "system", "content": _p.system},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.7,
            # 整章重写要输出完整一章（局部修补的输出远小于此），沿用 M3 大细纲同口径
            max_tokens=8192 if full else 6000,
            enable_thinking=False,
        )
        return resp.strip()

    # ---------------------------------------------------------- 内部：整章重写契约
    def _chapter_contract(self, chapter_num: int, post: Any) -> str:
        """取本章逐章契约（钩子 / 情节点 / 强度档位）——唯一真源 ``chapter_contract``。

        full 模式要求"按本章契约整章重写"，契约就是重写的靶子（与写手/评委共用同一份）。
        取不到时必须**显性降级留痕**（``rewrite.contract``）而不是静默按原文重写——
        否则整章重写会退化成"无靶子的自由发挥"，且失败不可见（纪律：降级必须可见）。

        定位口径与 ``design_brief`` 同源：章 frontmatter 的 ``subline`` → ``sublines/<id>/subline.md``。
        """
        subline_id = str((post.metadata or {}).get("subline") or "").strip()
        md = ""
        if subline_id:
            path = self.project_dir / "sublines" / subline_id / "subline.md"
            try:
                md = path.read_text(encoding="utf-8")
            except OSError as e:
                degrade(
                    "rewrite.contract",
                    f"支线 {subline_id} 细纲读取失败，第 {chapter_num} 章契约缺失，"
                    f"整章重写退化为无契约",
                    e,
                )
        if not md:
            if not subline_id:
                degrade(
                    "rewrite.contract",
                    f"第 {chapter_num} 章 frontmatter 无 subline 字段，本章契约缺失，"
                    f"整章重写退化为无契约",
                )
            elif not (self.project_dir / "sublines" / subline_id / "subline.md").exists():
                degrade(
                    "rewrite.contract",
                    f"sublines/{subline_id}/subline.md 不存在，第 {chapter_num} 章契约缺失，"
                    f"整章重写退化为无契约",
                )
            return "（本章逐章契约缺失：请仅按原文事实 + 反馈重写，并保持情节位次不变）"

        from agent.core.story.chapter_contract import PACE_TIER_BY_NAME, chapter_contract, pace_tier_of

        hooks, points = chapter_contract(md, chapter_num=chapter_num)
        tier_name = pace_tier_of(md, chapter_num)
        tier = PACE_TIER_BY_NAME.get(tier_name)
        tier_line = (
            f"{tier.name}（{tier.label}；{tier.chapter_req}）" if tier else "（本章未标强度档位）"
        )
        lines = [
            f"- 强度档位：{tier_line}",
            f"- 章节钩子设计：{hooks or '（无）'}",
            f"- 情节点序列：{points or '（无）'}",
        ]
        return "\n".join(lines)

    @staticmethod
    def _word_range(chapter_length: Any) -> str:
        """本章字数硬区间（复用写时门禁的唯一真源，不另写一份比例常量）。"""
        from agent.core.quality.scoring.quality_checker import (
            resolve_max_cjk_words,
            resolve_min_cjk_words,
        )

        try:
            target = int(chapter_length or 0)
        except (TypeError, ValueError):  # noqa: SILENT_DEGRADE reason=expected-skip
            target = 0
        if target <= 0:
            return "（目标字数未知：以 3000 字为基准，正文中文字数不得少于 2400）"
        return (
            f"中文字数须在 {resolve_min_cjk_words(target)}–{resolve_max_cjk_words(target)} 字之间"
            f"（目标 {target} 字；低于下限视为截断，硬性不合格）"
        )

    # ---------------------------------------------------------- 内部：整章重写的篇幅补足
    @staticmethod
    def _is_noop(new_text: str, old_text: str) -> bool:
        """整章重写产物是否与原文实质相同（仅空白差异不算改写）。

        判据用「去空白后全等」而非相似度：只放宽排版差异，任何真实改写都会改到字面。
        """
        strip_ws = lambda t: "".join(t.split())  # noqa: E731 - 单行纯函数，就近可读
        return strip_ws(new_text) == strip_ws(old_text)

    @staticmethod
    def _length_issues(text: str, ctx: dict[str, Any]) -> list[Any]:
        """整章重写的字数区间判定（复用写时门禁的**同一条规则**）。

        ``_check_word_count`` 是写章路径的判定真源：低于动态下限 → BLOCK 级
        （视为截断），超过合理上限 → WARN 级（仅告警）。此处**直接调用它**，
        阈值与 severity 都不在本路径另写一份（纪律 #19：跨模块共享常量必须派生
        而非重写）。``llm`` 形参对该规则无用，传 None。
        """
        from agent.core.quality.scoring.quality_checker import _check_word_count

        return _check_word_count(text, {"chapter_length": ctx.get("chapter_length")}, None)

    def _error_rules(self, text: str) -> set[str]:
        """本书护栏在 ``text`` 上命中的 error 级规则集合（扩写轮「不恶化」判据）。"""
        return {v.rule_id for v in self.guardrails.check(text).errors}

    def _ensure_full_length(self, text: str, ctx: dict[str, Any], *, chapter_num: int) -> str:
        """整章重写产物的篇幅补足：不足下限时做**整章扩写**重试（非追加拼段）。

        背景（2026-09-24 灵荒工坊 ch015 pilot 实证）：本套模型对「整章重写」提示词
        **系统性偏短**——2918 字原章重写后仅 1120–1255 中文字，远低于下限 2400；
        而追加式补字（写章路径 ``_top_up_length``）既留拼接缝，又实测**重新引入
        跨章重复**（补到 2178 字时命中第 14 章相似度 1.00 的 ``paragraph_dup``）。
        ⇒ 改为「整章扩写 + 每轮护栏复检」：扩写稿若相对当前稿**新增 error 级违规**
        一律弃用，宁短不脏（违规稿落盘比短稿更贵）。

        轮数用尽仍未达标时返回当前最佳稿，由 ``rewrite()`` 的字数硬检查决定是否 BLOCK。
        """
        from agent.core.quality.scoring.quality_checker import (
            _count_cjk,
            resolve_min_cjk_words,
        )

        min_words = resolve_min_cjk_words(ctx.get("chapter_length"))
        best = text
        if _count_cjk(best) >= min_words:
            return best
        best_errors = self._error_rules(best)
        for i in range(_FULL_EXPAND_ROUNDS):
            cur = _count_cjk(best)
            if cur >= min_words:
                break
            try:
                candidate = self._expand_once(best, ctx)
            except Exception as e:  # noqa: BLE001 - 扩写失败不阻断改写交付
                degrade(
                    "rewrite.expand",
                    f"第 {chapter_num} 章整章扩写第 {i + 1} 轮调用失败，保留当前稿"
                    f"（{cur} 字 < 下限 {min_words} 字）",
                    e,
                )
                continue
            new_len = _count_cjk(candidate)
            if new_len <= cur:
                degrade(
                    "rewrite.expand",
                    f"第 {chapter_num} 章整章扩写第 {i + 1} 轮输出未变长"
                    f"（{cur}→{new_len} 字），弃用本轮",
                )
                continue
            new_errors = self._error_rules(candidate)
            introduced = sorted(new_errors - best_errors)
            if introduced:
                degrade(
                    "rewrite.expand",
                    f"第 {chapter_num} 章整章扩写第 {i + 1} 轮引入新的 error 级违规"
                    f"{introduced}，弃用本轮（宁短不脏）",
                )
                continue
            self.console.print(
                f"[dim]      · 整章扩写第 {i + 1} 轮：{cur} → {new_len} 字[/dim]"
            )
            best, best_errors = candidate, new_errors
        return best

    def _expand_once(self, text: str, ctx: dict[str, Any]) -> str:
        """单轮「整章扩写」调用（提示词唯一真源 ``quality.rewrite_expand``）。"""
        _p = pm.get(_PROMPT_EXPAND)
        user_prompt = _p.render_user(
            chapter_text=text,
            word_range=self._word_range(ctx.get("chapter_length")),
            genre=ctx["genre"],
            tone=ctx["tone"],
            rhythm=ctx["rhythm"],
            chapter_length=ctx["chapter_length"],
            characters=ctx["characters"],
            prev_tail=ctx["prev_tail"],
            next_head=ctx["next_head"],
        )
        resp = chat_creative(
            self.llm,
            messages=[
                {"role": "system", "content": _p.system},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.6,
            max_tokens=8192,
            enable_thinking=False,
        )
        return (resp or "").strip()

    # ---------------------------------------------------------- 内部：上下文
    def _build_context(self, chapter_num: int, post: Any) -> dict[str, Any]:
        """收集改写必须保留的上下文锚点（来自设定集 + 邻接章节 + 历史偏好）。"""
        world = self._sm.load_world()
        meta = (world.get("metadata") or {}) if world.get("exists") else {}
        style = meta.get("style", {}) or {}
        synopsis = ""
        if world.get("exists"):
            synopsis = self._extract_section(world.get("content", ""), "故事简介")

        # 本章涉及角色：从 frontmatter evidence_chain 提取
        chain = post.metadata.get("evidence_chain", {}) or {}
        char_names = [c.get("name", "") for c in chain.get("characters", []) if c.get("name")]
        characters = "、".join(char_names) if char_names else "（未登记，按正文推断）"

        prev_tail = self._neighbor_anchor(chapter_num - 1, head=False)
        next_head = self._neighbor_anchor(chapter_num + 1, head=True)

        learnings = self._load_learnings()
        genre_val = meta.get("genre_label") or (meta.get("genres") or ["通用"])[0]

        return {
            "genre": genre_val,
            "tone": style.get("tone", "通用"),
            "rhythm": style.get("rhythm", "通用"),
            "chapter_length": style.get("chapter_length", 3000),
            "synopsis": synopsis or "（无简介）",
            "characters": characters,
            "prev_tail": prev_tail,
            "next_head": next_head,
            "learnings": learnings,
        }

    def _neighbor_anchor(self, neighbor_num: int, *, head: bool) -> str:
        """取邻接章节的尾/头若干字作为衔接锚点（不存在则降级空）。"""
        if neighbor_num < 1:
            return "（本书开头，无上文）"
        f = self.chapters_dir / f"ch{neighbor_num:03d}.md"
        if not f.exists():
            return "（邻接章节未写，无衔接约束）"
        text = self._strip_frontmatter(f)
        text = text.strip()
        if not text:
            return "（邻接章节为空）"
        if head:
            return text[:200] + ("…" if len(text) > 200 else "")
        return "…" + text[-200:] if len(text) > 200 else text

    def _load_learnings(self) -> str:
        try:
            items = self.learning_store.load()
        except Exception:  # noqa: BLE001
            return "（暂无历史偏好）"
        fb = [x for x in items if x.category == "feedback_rewrite"]
        if not fb:
            return "（暂无历史偏好）"
        recent = fb[-8:]
        return "\n".join(f"- {x.text}" for x in recent)

    def _record_learning(self, chapter_num: int, feedback: str, summary: str) -> None:
        try:
            self.learning_store.add(
                category="feedback_rewrite",
                text=f"第{chapter_num}章反馈「{feedback}」→ {summary}",
                source_chapters=[chapter_num],
            )
        except Exception as e:  # noqa: BLE001 - 偏好沉淀失败不影响改写交付
            degrade(
                "rewrite.pref.accumulate",
                "偏好沉淀失败，不影响本次改写交付（偏好学习本轮落空）",
                e,
            )

    # ---------------------------------------------------------- 内部：文件
    @staticmethod
    def _strip_frontmatter(file: Path) -> str:
        text = file.read_text(encoding="utf-8")
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                return parts[2].strip()
        return text.strip()

    @staticmethod
    def _extract_section(content: str, section_name: str) -> str:
        """从 markdown 内容提取 ## 段落。"""
        import re as _re

        pattern = rf"## {_re.escape(section_name)}\s*\n(.*?)(?=\n## |\Z)"
        m = _re.search(pattern, content, _re.DOTALL)
        return m.group(1).strip() if m else ""

    def _backup(self, chapter_file: Path) -> Path:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = self.backup_dir / f"{chapter_file.stem}_bak_{ts}.md"
        dest.write_text(chapter_file.read_text(encoding="utf-8"), encoding="utf-8")
        return dest

    def _save_rewritten(self, chapter_file: Path, post: Any, new_text: str, feedback: str) -> None:
        meta = dict(post.metadata)
        meta["last_rewrite_feedback"] = feedback
        meta["last_rewrite_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        meta["revision_count"] = int(meta.get("revision_count", 0) or 0) + 1
        # ---- L2 落盘兜底（2026-09-14）：rewrite 与写章同口径——删【…】指令/去重标题/清占位符 ----
        from agent.core.story.text_hygiene import (
            clean_hard_pollutions,
            strip_leading_headings,
        )

        body_text, _poll = clean_hard_pollutions(new_text)
        # 去掉 LLM 自报标题行（防与落盘统一标题重复——ch001 双标题根因：
        # 旧逻辑把带标题的 new_text 直接拼进 body，标题重复两遍）
        body_text = strip_leading_headings(body_text)
        # 标题保持一致（从首行推断；去标题行后的首行才是真实正文首行）
        # 仅当首行**形如标题**（短且无句读）时才采纳：旧逻辑只判 `len<=30`，
        # 把 27 字的叙事首句写成了章节标题（灵荒工坊 ch015 实证：
        # title 被改成「林凡把账册摊在膝上，棚顶缝隙透进来的月光恰好落在那一页。」）。
        first = body_text.strip().split("\n", 1)[0].strip() if body_text.strip() else ""
        if (
            first
            and not first.startswith("第")
            and len(first) <= 20
            and not re.search(r"[。！？；，]", first)
        ):
            meta["title"] = first
        meta["quality_passed"] = self._gate_passed(body_text)
        # word_count 同口径刷新（与写章路径``m5_persist._save_chapter`` 同一公式：
        # 去换行/去空格，且**不含** H1 标题行——``body_text`` 已 strip_leading_headings）。
        # 此前只刷 quality_passed 不刷 word_count ⇒ 改写后该字段永远停在写章当时的旧值
        # （灵荒工坊 5 章实证：ch002/ch015/ch021/ch022/ch025 与正文实际不符）。
        meta["word_count"] = _wc(body_text)
        body = f"# 第 {meta.get('chapter', '?')} 章 · {meta.get('title', '')}\n\n{body_text}"
        new_post = frontmatter.Post(body, **meta)
        chapter_file.write_text(frontmatter.dumps(new_post), encoding="utf-8")

    def _gate_passed(self, body_text: str) -> bool:
        """按**写时规则层门禁**（``quality_check`` 工具的同一条链路）重算通行标记。

        ``quality_passed`` 此前只在写章当时写入，改写路径从不刷新 ⇒ 改好了章节、
        标记仍是 ``false``（灵荒工坊 16 章实证：内容已离线可达标，仪表盘照旧显示
        「未通过」）。此处复用同一个 ``QualityChecker``，不另立一套口径。
        """
        from agent.core.quality.scoring.quality_checker import QualityChecker

        return bool(QualityChecker(self.project_dir, None).check(body_text).passed)
