from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from agent.core.infra.prompt_manager import pm
from agent.core.registry.genre_pack import GenrePackRegistry

from agent.client.gateway_adapter import chat_creative, chat_utility
from agent.core.quality.scoring import QualityChecker, LLMBackedChecker, Severity
from agent.core.quality.scoring.quality_checker import (
    _chapter_length_from_ctx,
    _count_cjk,
    resolve_min_cjk_words,
    resolve_max_cjk_words,
)
from agent.utils import parse_llm_json
from agent.workflows.writing.m5_text_hygiene import (
    _ENGLISH_REPLACE_GUIDE,
    hard_replace_english,
    scan_english_contamination,
)

logger = logging.getLogger(__name__)

MAX_REVISIONS = 2


class M5QualityGateMixin:
    """质量校验 + 自动修订 + D 多维审查 + 提速辅助（由 m5_write_chapter 拆出，仅由 M5WriteChapterWorkflow 组合使用）"""

    # ============================================================
    # 3. 质量校验 + 自动修订
    # ============================================================
    def _extra_english_revise(
        self, text: str, tokens: list[str], ctx: dict[str, Any], max_extra: int = 2
    ) -> str:
        """落盘前追加的英文专门修订：把残留英文 token 明确告诉 LLM，要求改纯中文。
        最多 max_extra 次，避免无限循环；仍残留则交给 hard_replace_english 兜底。"""
        for _ in range(max_extra):
            toks = scan_english_contamination(text)
            if not toks:
                break
            instr = (
                f"本章正文仍残留英文（必须全部改为纯中文叙事）：{', '.join(toks[:20])}。"
                + _ENGLISH_REPLACE_GUIDE
                + " 仅替换这些英文，保持情节/人物/对话/结构完全不变，直接输出完整正文。"
            )
            try:
                rev_resp = chat_creative(
                    self.llm,
                    messages=[
                        {"role": "system", "content": pm.get("m5.revise").system},
                        {
                            "role": "user",
                            "content": pm.get("m5.revise").render_user(
                                quality_report=instr, chapter_text=text
                            ),
                        },
                    ],
                    temperature=0.4,
                    max_tokens=4096,
                    enable_thinking=False,
                )
                text = rev_resp.strip()
            except Exception:  # noqa: BLE001 - 修订调用异常不阻断，交由兜底清理
                logger.warning("[no_english] 追加英文修订调用异常，交由确定性清理")
                break
        return text
    def _quality_check_and_revise(
        self, ctx: dict[str, Any], chapter_text: str
    ) -> tuple[dict[str, Any], int, str]:
        """质量校验 + ≤MAX_REVISIONS 次自动修订

        Returns:
            (final_quality_report, revision_attempts, final_text)
        """
        wi = ctx["world_info"]
        is_climax = ctx["pressure_stage"] == "高潮"
        report: dict[str, Any] = {}
        attempts = 0
        text = chapter_text
        # D：多维 LLM 审查问题（仅 strict_review 时填充，随最后一次校验落入 report）
        last_d_issues: list[dict[str, Any]] = []
        # 提速：下一轮是否需要 LLM 复检（上轮失败全部来自确定性关卡时可跳过）
        llm_check_needed = True
        # 提速：上轮未通过的规则 id（供复审聚焦）
        last_failed_rules: list[str] = []

        for attempt in range(MAX_REVISIONS + 1):
            # 提速：确定性关卡（英文污染/字数）用纯扫描复核，无需 LLM 参与
            english_tokens = scan_english_contamination(text)
            cjk_count = _count_cjk(text)

            resp = ""
            report = {}
            if llm_check_needed:
                # 校验
                check_prompt = pm.get("m5.quality_check").render_user(
                    tone=wi["tone"],
                    chapter_length=wi["chapter_length"],
                    characters_fingerprint=ctx["characters_fingerprint"],
                    is_climax="是" if is_climax else "否",
                    stage_calibration=self._stage_calibration(ctx, attempt),
                    recheck_focus=self._recheck_focus(last_failed_rules, attempt),
                    chapter_text=text,
                )

                # T-3：追加题材层质量规则（取自题材包 quality-rules.md），强化题材专属校验
                # 多题材：逐个题材包加载并拼接（world.md 元数据为 genres 列表，兼容旧 genre 单值）
                genre_list = wi.get("genres") or (
                    [wi["genre"]] if wi.get("genre") else []
                )
                if genre_list:
                    if self._genre_registry is None:
                        self._genre_registry = GenrePackRegistry()
                    rules_parts: list[str] = []
                    for g in genre_list:
                        try:
                            genre_rules_text = self._genre_registry.load(g).quality_rules
                        except ValueError:
                            genre_rules_text = ""
                        if genre_rules_text:
                            rules_parts.append(
                                f"【题材层质量规则（{g}）】\n{genre_rules_text}"
                            )
                    if rules_parts:
                        check_prompt = check_prompt + "\n\n" + "\n\n".join(rules_parts)

                # 提速：主质检与 D 多维审查并行执行（原先串行，省一次完整往返）
                resp, last_d_issues = self._check_parallel(text, ctx, check_prompt)
                try:
                    report = parse_llm_json(resp)
                except ValueError:
                    report = {"overall_pass": True, "rules": [], "suggestions": "校验解析失败，默认通过"}

                # D：多维 LLM 质量审查（仅当 strict_review 开启；并入同一 revise_loop 预算）
                # 维度 blocking 视为本章未通过、触发既有修订循环。
                if self.strict_review:
                    report["d_issues"] = last_d_issues
                    d_blocking = any(
                        i.get("severity") == Severity.BLOCK.value for i in last_d_issues
                    )
                    report["d_blocking"] = d_blocking
                    if d_blocking:
                        report["overall_pass"] = False
            else:
                # 提速：确定性快速复审——上轮失败全部来自英文污染/字数等纯扫描关卡，
                # 本轮跳过 LLM 复检（省 1~2 次 LLM 往返），仅重跑下方确定性扫描。
                report = {"overall_pass": True, "rules": []}
                last_d_issues = []

            # ---- G-EN：正文纯中文硬关卡（确定性扫描，叠加在 LLM 质检之上，不依赖 LLM 自觉）----
            quality_report_text = resp
            if english_tokens:
                report["overall_pass"] = False
                report.setdefault("rules", []).append(
                    {
                        "rule": "no_english",
                        "pass": False,
                        "issue": "正文含英文污染（必须改为纯中文）："
                        + "、".join(english_tokens[:20]),
                    }
                )
                report["suggestions"] = (
                    report.get("suggestions", "") + "\n" + _ENGLISH_REPLACE_GUIDE
                )
                # 把明确的中文替换指令直接塞进修订提示词，确保 LLM 知道改什么
                quality_report_text = (
                    resp
                    + "\n\n# 硬性修订指令（必须执行，否则本章不通过）\n"
                    + "本章检出英文污染 token："
                    + "、".join(english_tokens[:20])
                    + "\n"
                    + _ENGLISH_REPLACE_GUIDE
                )

            # ---- 字数硬关卡（确定性扫描，不依赖 LLM 自觉）----
            # 中文字数低于动态下限（随目标字数伸缩，有绝对下限兜底）即判不通过，
            # 触发修订扩写，杜绝截断短章落盘
            min_words = resolve_min_cjk_words(_chapter_length_from_ctx(ctx))
            if cjk_count < min_words:
                report["overall_pass"] = False
                report.setdefault("rules", []).append(
                    {
                        "rule": "word_count",
                        "pass": False,
                        "issue": (
                            f"正文中文字数不足（{cjk_count} < {min_words} 字），"
                            "必须扩写补齐到门禁字数以上，禁止截断残缺"
                        ),
                    }
                )
                report["suggestions"] = (
                    report.get("suggestions", "")
                    + f"\n本章正文字数不足（现 {cjk_count} 字），须扩充细节/对白/动作/推进到 ≥ {min_words} 字。"
                )
                # 把明确扩写指令塞进修订提示词，确保 LLM 知道要补
                quality_report_text = (
                    quality_report_text
                    + "\n\n# 硬性扩写指令（必须执行，否则本章不通过）\n"
                    + f"本章正文字数不足（现 {cjk_count} 字 < {min_words} 字），"
                    f"请在不偏离大纲与人物设定的前提下扩写至 ≥ {min_words} 字，"
                    "禁止用重复段/空行/废话充数。"
                )
            # 超合理上限仅告警，不阻断落盘（区间口径：目标×1.2 视为合理上限）
            else:
                max_words = resolve_max_cjk_words(_chapter_length_from_ctx(ctx))
                if max_words and cjk_count > max_words:
                    report["suggestions"] = (
                        report.get("suggestions", "")
                        + f"\n本章正文偏长（约 {cjk_count} 字，合理上限 {max_words} 字），"
                        "可适当精简冗余描写，使其更紧凑。"
                    )

            if report.get("overall_pass", False):
                break

            # 提速：记录本轮未通过项，供下轮复审聚焦；判定下轮是否还需要 LLM 复检
            # （仅当存在 LLM 评审类失败时才需要；纯确定性关卡失败由扫描兜底）
            last_failed_rules = [
                str(r.get("rule", "?"))
                for r in (report.get("rules") or [])
                if not r.get("pass", True)
            ]
            llm_check_needed = (
                bool(last_failed_rules)
                or bool(report.get("d_blocking"))
                or not self.fast_deterministic_recheck
            )

            # 未通过 → 修订
            if attempt < MAX_REVISIONS:
                # ---- G9：章内子阶段事件（修订）----
                self._emit_substage("revise", ctx["chapter_num"])
                self.console.print(
                    f"  [yellow]质量校验未通过（第 {attempt + 1} 次修订）...[/yellow]"
                )
                # 提速：修订要点压缩——只给失败项 + 硬性指令，不塞全量 9 项 JSON
                revise_prompt = pm.get("m5.revise").render_user(
                    quality_report=self._compact_revise_report(report, quality_report_text),
                    chapter_text=text,
                )
                rev_resp = chat_creative(
                    self.llm,
                    messages=[
                        {"role": "system", "content": pm.get("m5.revise").system},
                        {"role": "user", "content": revise_prompt},
                    ],
                    temperature=0.6,
                    max_tokens=4096,
                    enable_thinking=False,
                )
                text = rev_resp.strip()
                attempts = attempt + 1

        # ---- G-EN：落盘前最终英文兜底（主循环修订后若仍有英文，追加专门修订 + 确定性清理）----
        residual = scan_english_contamination(text)
        if residual:
            text = self._extra_english_revise(text, residual, ctx, max_extra=6)
            residual = scan_english_contamination(text)
            if residual:
                text, still = hard_replace_english(text)
                if still:
                    logger.warning(
                        "[no_english] 落盘前仍存在英文残留，已做确定性清理: %s",
                        still[:20],
                    )
                else:
                    logger.info("[no_english] 落盘前确定性清理完成，无英文残留")
            # 英文已清干净 → 把 no_english 规则移出，避免影响整体通过判定
            if not scan_english_contamination(text):
                report.setdefault("rules", [])
                report["rules"] = [
                    r for r in report["rules"] if r.get("rule") != "no_english"
                ]
                other_fail = any(
                    (not r.get("pass", True)) for r in report.get("rules", [])
                )
                if not other_fail:
                    report["overall_pass"] = True

        # T-5：可选启用结构化质量校验（仅补充，不阻断主路径 LLM 校验）
        if getattr(self, "enable_structured_qc", False):
            try:
                checker = QualityChecker(self.project_dir, self.llm)
                structured = checker.check(text, ctx)
                report["structured_issues"] = [
                    {
                        "rule_id": issue.rule_id,
                        "severity": issue.severity.value,
                        "description": issue.description,
                    }
                    for issue in structured.issues
                ]
            except Exception:  # noqa: BLE001 - 结构化校验失败不影响主路径
                report.setdefault("structured_issues", [])

        return report, attempts, text
    # ============================================================
    # 3.5 提速：并行质检 / 阶段校准 / 复审聚焦 / 修订要点压缩
    # ============================================================
    def _check_parallel(
        self, text: str, ctx: dict[str, Any], check_prompt: str
    ) -> tuple[str, list[dict[str, Any]]]:
        """主质检（9 项规则）与 D 多维审查并行执行，取二者较大耗时为墙钟时间。

        strict_review 关闭时 D 审查不启动；线程池异常时降级为串行主质检
        （D 审查返回空，与既有降级行为一致，不阻断写章）。

        Returns:
            (质检原始 JSON 文本, D 多维审查 issue 列表)
        """
        messages = [
            {"role": "system", "content": pm.get("m5.quality_check").system},
            {"role": "user", "content": check_prompt},
        ]
        try:
            with ThreadPoolExecutor(max_workers=2) as ex:
                f_check = ex.submit(
                    chat_utility, self.llm, messages=messages,
                    max_tokens=1500, enable_thinking=False,
                )
                f_d = (
                    ex.submit(self._run_d_review, text, ctx)
                    if self.strict_review
                    else None
                )
                resp = f_check.result()
                d_issues = f_d.result() if f_d is not None else []
            return resp, d_issues
        except Exception as e:  # noqa: BLE001 - 并行异常降级串行，不影响正确性
            logger.warning("[m5] 并行质检异常，降级串行执行: %s", e)
            resp = chat_utility(
                self.llm, messages=messages, max_tokens=1500, enable_thinking=False
            )
            return resp, []
    @staticmethod
    def _stage_calibration(ctx: dict[str, Any], attempt: int) -> str:
        """提速·评审校准：开篇/铺垫章不以中后期节奏苛求，复审聚焦上轮失败项，
        减少开篇章被反复打回的无效修订轮（不影响 no_english/字数等硬关卡）。"""
        notes: list[str] = []
        ch = ctx.get("chapter_num", 0)
        stage = str(ctx.get("pressure_stage") or "")
        if ch and ch <= 3:
            notes.append(
                f"本章为开篇章节（第{ch}章）：允许世界观/人物铺垫占比略高，"
                "节奏类规则以「开篇钩子是否成立、关键信息是否清晰」为准，"
                "不以中后期高强度节奏苛求。"
            )
        elif stage and "铺垫" in stage:
            notes.append(
                f"本章为铺垫章节（压力阶段：{stage}）：允许节奏放缓，"
                "重点审查开篇钩子、角色一致性与章末悬念。"
            )
        if attempt > 0:
            notes.append(
                f"本次为第 {attempt} 次修订后的复审：确认上轮未通过项已解决即可，"
                "不要为锦上添花引入新的否决项。"
            )
        return "\n".join("- " + n for n in notes) if notes else "（无特殊校准，按常规标准评审）"
    def _recheck_focus(self, last_failed_rules: list[str], attempt: int) -> str:
        """提速·复审聚焦：修订后的复检只重点复核上轮未通过规则，其余确认未回归即可。"""
        if attempt > 0 and last_failed_rules:
            return (
                "上轮未通过规则：" + "、".join(last_failed_rules)
                + "（其余规则上轮已通过，只需确认修订未引入新问题）"
            )
        return ""
    def _compact_revise_report(self, report: dict[str, Any], full_text: str) -> str:
        """提速·修订要点压缩：修订提示只注入失败项 + 硬性指令，不塞全量 9 项 JSON。

        全量 JSON 中大量 pass=true 规则对修订毫无信息量，压缩后可显著降低
        修订调用的输入 token 与注意力分散。任何解析缺口都回退到 full_text。
        """
        try:
            lines: list[str] = []
            for r in report.get("rules") or []:
                if not r.get("pass", True):
                    lines.append(
                        f"- [{r.get('rule', '?')}] {r.get('issue', '')}".rstrip()
                    )
            for d in report.get("d_issues") or []:
                lines.append(
                    f"- [{d.get('rule_id', '?')}]（{d.get('severity', '')}）"
                    f"{d.get('description', '')}".rstrip()
                )
            sugg = str(report.get("suggestions") or "").strip()
            if sugg:
                lines.append("修改建议：" + sugg[:600])
            # 保留硬性指令段（英文污染替换指令 / 扩写指令），这些必须原样传达
            if full_text:
                for part in full_text.split("\n\n# ")[1:]:
                    head = part.split("\n", 1)[0]
                    if "硬性" in head:
                        lines.append("# " + part)
            if not lines:
                return full_text or "审稿未给出具体问题，请自查常见规则后输出修订稿。"
            return "\n".join(lines)
        except Exception:  # noqa: BLE001 - 压缩失败回退原始全文，保证行为不回退
            return full_text
    # ============================================================
    # 3.6 D：多维 LLM 质量审查（并入 revise_loop）
    # ============================================================
    def _run_d_review(self, text: str, ctx: dict[str, Any]) -> list[dict[str, Any]]:
        """D：用 LLMBackedChecker 合并评审 4 个网文维度（爽点/OOC/连贯性/追读力）

        合并为单次 ``chat_utility`` 调用；LLM 不可用 / 调用异常 / 超时均降级为空
        （放行 + 记录，绝不阻断写章）。返回可序列化的 issue 字典列表。

        Args:
            text: 当前章节正文
            ctx: 上下文

        Returns:
            ``[{"rule_id", "severity", "description"}, ...]``；降级时为空列表。
        """
        try:
            checker = LLMBackedChecker(self.llm)
            issues = checker.run_rules(self._qc.llm_rules, text, ctx)
        except Exception:  # noqa: BLE001 - D 审查失败降级为空，不阻断主路径
            return []
        return [
            {
                "rule_id": i.rule_id,
                "severity": i.severity.value,
                "description": i.description,
            }
            for i in issues
        ]
