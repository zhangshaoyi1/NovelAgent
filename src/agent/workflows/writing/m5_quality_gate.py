from __future__ import annotations

import logging
from typing import Any

from agent.core.infra.prompt_manager import pm
from agent.core.registry.genre_pack import GenrePackRegistry

from agent.client.gateway_adapter import chat_creative, chat_utility
from agent.core.quality.scoring import QualityChecker, Severity
from agent.core.quality.scoring.quality_checker import (
    _chapter_length_from_ctx,
    _count_cjk,
    _rhythm_from_ctx,
    resolve_min_cjk_words,
    resolve_max_cjk_words,
)
from agent.utils import parse_llm_json
from agent.workflows.writing.m5_text_hygiene import (
    _ENGLISH_REPLACE_GUIDE,
    hard_replace_english,
    scan_english_contamination,
    scan_hard_pollutions,
)

logger = logging.getLogger(__name__)

MAX_REVISIONS = 2

# 金三写时门禁（2026-09-12）：前三章落盘前必须过读者吸引力六维门禁。
# 此前金三只在批末评估（evaluator golden gate），写时 9 项规则质检不含吸引力维度，
# 导致低质量开局照样落盘、批末必然熔断且修不到开头（五灵破 19+7 次实证）。
GOLDEN_WRITE_GATE_FIRST_N = 3
GOLDEN_WRITE_GATE_TOTAL = 60  # 综合合格线（与 B4 golden_three_threshold 默认一致）
GOLDEN_WRITE_GATE_FLOOR = 40  # 单维触底线（与 golden_three_floor 默认一致）


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
                    # P-4/P-8（提示词改进）：质检校验角色硬约束 + 细纲情节点覆盖（缺省为空）
                    hard_constraints=ctx.get("character_constraints", ""),
                    plot_points=ctx.get("plot_points", ""),
                    # P-9（提示词改进）：事实对照卡 = 连续性账本投影（≤800 字，规则 6 逐条对照）
                    fact_card=str(ctx.get("continuity_projection", "") or "")[:800],
                    # T2（2026-09-13）：规则 13 跨章复述对照依据（与 agentic_write 同步透传）
                    prev_chapter_excerpt=(str(ctx.get("prev_chapter_summary") or "")[-1500:]),
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
                            genre_rules_text = ""  # noqa: SILENT_DEGRADE
                        if genre_rules_text:
                            rules_parts.append(
                                f"【题材层质量规则（{g}）】\n{genre_rules_text}"
                            )
                    if rules_parts:
                        check_prompt = check_prompt + "\n\n" + "\n\n".join(rules_parts)

                # 提速：主质检与 D 多维审查并行执行（原先串行，省一次完整往返）
                # 合并同质检查（2026-09-13）：主质检 + D 审查单次调用（省 1 次/轮，
                # 正文只传一份）；d_issues 从同一 JSON 提取，缺失显性降级为空。
                check_prompt_full = check_prompt + self._d_supplement()
                last_d_issues: list[dict[str, Any]] = []
                resp = self._check_combined(check_prompt_full)
                try:
                    report = parse_llm_json(resp)
                except ValueError as e:
                    # 解析失败多为截断（H4），可修复：附错误详情重试一次（对齐 G4 约定），
                    # 重试仍失败才按 G3 降级放行（显性 degrade，不静默）
                    try:
                        retry = chat_utility(
                            self.llm,
                            messages=[
                                {"role": "system", "content": pm.get("m5.quality_check").system},
                                {"role": "user", "content": check_prompt_full
                                 + f"\n\n【上次质检输出解析失败原因，务必修正】"
                                   f"请只输出一个合法的 JSON 对象，不要包含 ```json 标记：\n{e}"},
                            ],
                            max_tokens=6144,
                            enable_thinking=False,
                        )
                        report = parse_llm_json(retry)
                        logger.info("[m5] 质检 JSON 解析失败后带错误重试成功: %s", e)
                    except ValueError as e2:
                        from agent.core.infra.degrade import degrade
                        degrade(
                            "m5.quality_gate.parse",
                            f"质检 JSON 解析失败且带错重试仍失败，默认通过（不阻断出章）：{e2}",
                        )
                        report = {"overall_pass": True, "rules": [], "suggestions": "校验解析失败，重试后降级通过"}

                # D：从合并输出提取 d_issues（仅 strict_review 开启）。
                # 缺失/非列表 = 模型未完成附加任务 → 显性降级为空（与原 D 审查
                # 调用失败的放行语义一致，但必须留痕，不许静默）。
                if self.strict_review:
                    raw_d = report.get("d_issues")
                    if isinstance(raw_d, list):
                        last_d_issues = [i for i in raw_d if isinstance(i, dict)]
                    else:
                        from agent.core.infra.degrade import degrade
                        degrade(
                            "m5.quality_gate.d_missing",
                            "合并质检输出缺 d_issues 字段，D 多维审查降级为空（本轮未覆盖）",
                        )
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
            min_words = resolve_min_cjk_words(
                _chapter_length_from_ctx(ctx), _rhythm_from_ctx(ctx)
            )
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

            # ---- L2：生成残留硬污染硬关卡（2026-09-13，标题重复/AI指令泄漏/占位符/AI承接词）----
            # 灵荒薪传 ch001 实证：标题重复两遍 + 「【下一章预告：…】」指令混入正文，
            # LLM 质检（钩子/爽点等情感类规则）无感，必须确定性拦截。
            hard_poll = scan_hard_pollutions(text)
            if hard_poll:
                report["overall_pass"] = False
                report.setdefault("rules", []).append(
                    {
                        "rule": "hard_pollution",
                        "pass": False,
                        "issue": "生成残留硬污染：" + "；".join(hard_poll),
                    }
                )
                report["suggestions"] = (
                    report.get("suggestions", "")
                    + "\n生成残留硬污染："
                    + "；".join(hard_poll)
                    + "。删除上述残留（标题重复只保留落盘统一标题；【…】指令文本必须删除；"
                    "占位符/AI 承接词替换为自然叙事），保持情节/人物/设定不变，直接输出完整正文。"
                )
                # 把明确清理指令塞进修订提示词，确保 LLM 知道删什么
                quality_report_text = (
                    quality_report_text
                    + "\n\n# 硬性清理指令（必须执行，否则本章不通过）\n"
                    + "；".join(hard_poll)
                    + "。删除这些生成残留（标题重复只保留一个；【…】预告/大纲指令整段删除；"
                    "占位符与 AI 承接词替换为自然叙事），保持情节/人物/对话完全不变，直接输出完整正文。"
                )

            # ---- 金三写时门禁（第 1~N 章专属）：读者吸引力六维不达标不得落盘 ----
            if (
                int(ctx.get("chapter_num", 0) or 0) <= GOLDEN_WRITE_GATE_FIRST_N
            ):
                report = self._apply_golden_write_gate(
                    report, text, ctx
                )
                quality_report_text = (
                    quality_report_text + report.get("golden_gate_instruction", "")
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
                report.setdefault("structured_issues", [])  # noqa: SILENT_DEGRADE

        return report, attempts, text
    # ============================================================
    # 3.5 提速：并行质检 / 阶段校准 / 复审聚焦 / 修订要点压缩
    # ============================================================
    def _d_supplement(self) -> str:
        """合并同质检查（2026-09-13）：D 多维审查的维度块（并入主质检 prompt）。

        原 `_check_parallel` 用两个并发调用分别取质检 JSON 与 d_issues——二者
        输入同为章节正文、输出同构（issue 列表），合并后省 1 次调用且正文只传
        一份（输入 token 减半）。strict_review 关闭 / 规则渲染失败 → 空串
        （退化为仅主质检，与既有降级语义一致）。
        """
        if not getattr(self, "strict_review", False):
            return ""
        try:
            llm_rules = list(self._qc.llm_rules)
        except Exception as e:  # noqa: BLE001 - 显性降级为不合并
            from agent.core.infra.degrade import degrade

            degrade("m5.quality_gate.d_merge", "D 维度规则读取失败，本次质检不合并 D 审查", e)
            return ""
        if not llm_rules:
            return ""
        dims = "\n".join(
            f"- {r.dimension}（{r.name}，id={r.id}）：{r.prompt_template}" for r in llm_rules
        )
        return (
            "\n\n【附加任务：D 多维审查（与上述质检同一次完成，勿分开两次作答）】\n"
            "在输出 JSON 顶层追加字段 d_issues：数组，逐维度审查同一章正文，\n"
            '每项格式 {"rule_id": "<维度id>", "severity": "block|warn", "description": "<问题>"}；\n'
            'severity 取 "block"（该维度不达标且必须修订）或 "warn"（提示）；'
            "全部达标时输出空数组 []。\n"
            f"审查维度：\n{dims}"
        )

    def _check_combined(self, check_prompt: str) -> str:
        """主质检 + D 多维审查合并为单次调用（输出契约见 _d_supplement）。

        Returns:
            质检原始 JSON 文本（含 d_issues 字段，若有）。
        """
        messages = [
            {"role": "system", "content": pm.get("m5.quality_check").system},
            {"role": "user", "content": check_prompt},
        ]
        return chat_utility(
            self.llm, messages=messages,
            max_tokens=6144, enable_thinking=False,  # 合并输出更大：6144 防 H4 截断 fail-open
        )
    def _apply_golden_write_gate(
        self, report: dict[str, Any], text: str, ctx: dict[str, Any]
    ) -> dict[str, Any]:
        """金三写时门禁：对本章草稿做读者吸引力六维评分。

        不达标 → overall_pass=False + 失败规则（含逐维分数与差距），
        修订指令注入 quality_report_text（由调用方拼接），走既有修订闭环。
        评分器离线/异常 → 显性 degrade 后放行（G3：基础设施失败不阻断，
        但批末 evaluator 金三门禁仍在，不会被静默放过）。
        结果写入 report["golden_write_gate"] 供落盘前硬判定。
        """
        report.setdefault("golden_write_gate", {"applied": False})
        report.pop("golden_gate_instruction", None)  # 防止上一轮指令残留到本轮
        try:
            from agent.core.quality.scoring.reader_appeal import (
                ReaderAppealScorer,
                build_score_chapter_kwargs_from_ctx,
                recheck_borderline,
            )

            if getattr(self, "_golden_scorer", None) is None:
                self._golden_scorer = ReaderAppealScorer(self.llm, self.console)
            # 信息校准（2026-09-13）：注入设定真源/前情/本章意图，杜绝裸评——
            # 此前只传正文，character_arc/world_novelty 等上下文依赖型维度被系统性低估。
            _g_kwargs = build_score_chapter_kwargs_from_ctx(ctx)
            gr = self._golden_scorer.score_chapter(text, **_g_kwargs)
            # 贴线带二次采样复核（方差抑制）：单样本在 60 线附近 1 分之差即可误熔断。
            _re = recheck_borderline(
                self._golden_scorer, text, gr,
                threshold=GOLDEN_WRITE_GATE_TOTAL, band=5, kwargs=_g_kwargs,
            )
            if _re is not None:
                gr = _re
        except Exception as e:  # noqa: BLE001 - 评分器异常降级放行（G3）
            from agent.core.infra.degrade import degrade

            degrade(
                "m5.golden_write_gate",
                "金三写时评分失败，本轮放行（批末金三门禁仍会把关）",
                e,
            )
            report["golden_write_gate"] = {"applied": False, "error": str(e)}
            return report
        if not gr.llm_used:
            report["golden_write_gate"] = {"applied": False, "error": "scorer offline"}
            return report

        failing = [
            f"{k}（{v}/100，触底线 {GOLDEN_WRITE_GATE_FLOOR}，差 {GOLDEN_WRITE_GATE_FLOOR - v}）"
            for k, v in gr.dimensions.items() if v < GOLDEN_WRITE_GATE_FLOOR
        ]
        total_fail = gr.total_score < GOLDEN_WRITE_GATE_TOTAL
        result = {
            "applied": True,
            "dimensions": dict(gr.dimensions),
            "total": gr.total_score,
            "passed": not (total_fail or failing),
        }
        report["golden_write_gate"] = result
        if result["passed"]:
            return report

        report["overall_pass"] = False
        issue = "金三门禁未达标：综合分 {}/{}；{}".format(
            gr.total_score,
            GOLDEN_WRITE_GATE_TOTAL,
            "；".join(failing) if failing else "单维均触底线之上",
        )
        report.setdefault("rules", []).append(
            {"rule": "golden_gate", "pass": False, "issue": issue}
        )
        report["suggestions"] = (
            report.get("suggestions", "")
            + "\n金三门禁："
            + ("；".join(gr.suggestions[:3]) if gr.suggestions else issue)
        )
        report["golden_gate_instruction"] = (
            "\n\n# 金三门禁硬性修订指令（本章为开篇前 "
            f"{GOLDEN_WRITE_GATE_FIRST_N} 章，不达标不得落盘）\n"
            f"读者吸引力综合分 {gr.total_score}/{GOLDEN_WRITE_GATE_TOTAL}"
            + (f"；触底维度：{'、'.join(failing)}" if failing else "")
            + "。\n针对不足维度定向强化：世界观新颖度低→给设定一个独特的记忆点/代价/异象；"
            "人物弧光弱→给主角一次主动选择或小胜利，而非纯被动挨打；"
            "爽点密度低→压缩压抑段、提前兑现一个具体爽点节拍；"
            "情绪曲线平→制造一次明显起伏；代入感弱→收紧视角、增加可感细节；"
            "钩子弱→章末悬念更具体。保持情节/人物/设定不变，只提升写法。"
            + ("评分建议：" + "；".join(gr.suggestions[:3]) if gr.suggestions else "")
        )
        self.console.print(
            f"  [yellow]金三写时门禁未通过（综合 {gr.total_score}），纳入修订...[/yellow]"
        )
        return report

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
