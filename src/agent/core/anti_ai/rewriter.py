"""AI 味 LLM 改写器（6 Gate + 三遍法）

对齐 story-deslop skill 的方法论：
- **轻度**：只走规则后处理器（``PostProcessor``，零 LLM 成本），门禁 A+B。
- **中度**：LLM 改写，三遍法 Pass1（去泛化）+ Pass2（去书面化），门禁 A+B+C+D。
- **重度**：LLM 改写，完整三遍 + 重点段落重写，6 门禁全过。

分层：位于 core/anti_ai，只依赖 base（LLMClient 由调用方注入），
LLM 调用事件由 client 层统一埋点（``wire_llm_event_hook``），本模块不做转发。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.core.anti_ai.detector import (
    AI_FLAVOR_HEAVY,
    AI_FLAVOR_LIGHT,
    AI_FLAVOR_MEDIUM,
    AIFlavorReport,
    AIFlavorScanner,
)
from agent.core.infra.prompt_manager import pm

# 输出标记（prompts/m5/deslop.md 要求模型严格照抄）
_MARKER_LOG = "【修改记录】"
_MARKER_FULL = "【润色后全文】"

_LEVEL_LABELS = {
    AI_FLAVOR_LIGHT: "轻度",
    AI_FLAVOR_MEDIUM: "中度",
    AI_FLAVOR_HEAVY: "重度",
}


@dataclass
class DeslopResult:
    """去AI味改写结果。"""

    text: str = ""  # 改写后的正文（无标记）
    level: str = AI_FLAVOR_LIGHT
    changed: bool = False
    changes: list[str] = field(default_factory=list)  # 修改记录
    metrics: dict = field(default_factory=dict)  # 扫描指标（来自 AIFlavorReport）
    report: AIFlavorReport | None = None
    via_llm: bool = False  # True=走 LLM 改写；False=仅规则后处理/未处理


class DeslopRewriter:
    """去AI味改写器——规则检测分级 + LLM 改写（走统一 LLMClient）。"""

    def __init__(
        self,
        llm_client: Any | None = None,
        project_dir: str | Path | None = None,
        console: Any | None = None,
    ) -> None:
        # 延迟导入，避免模块级循环依赖（LLMClient 仅依赖 base）。
        if llm_client is None:
            from agent.client import LLMClient

            llm_client = LLMClient()
        self.llm = llm_client
        self.project_dir = project_dir
        self.console = console
        self.scanner = AIFlavorScanner(project_dir)

    # ------------------------------------------------------------------
    # 检测
    # ------------------------------------------------------------------
    def classify(self, text: str) -> AIFlavorReport:
        """仅做 6 指标扫描（CLI --dry-run / 报告用）。"""
        return self.scanner.scan(text)

    # ------------------------------------------------------------------
    # 改写
    # ------------------------------------------------------------------
    def rewrite(self, text: str, level: str = "auto") -> DeslopResult:
        """按分级执行去AI味。

        Args:
            text: 待处理正文（不含标题行/元信息）。
            level: "auto"（扫描自动判定）/ "light" / "medium" / "heavy"。

        Returns:
            DeslopResult（text 为改写后正文；失败降级返回原文，绝不抛异常）。
        """
        if not text or len(text.strip()) < 50:
            return DeslopResult(text=text)

        report = self.scanner.scan(text)
        if level == "auto":
            level = report.level
        if level not in _LEVEL_LABELS:
            level = report.level

        if level == AI_FLAVOR_LIGHT:
            return self._rule_based_rewrite(text, report)

        # 中度/重度：LLM 改写（失败降级原文）
        try:
            return self._llm_rewrite(text, level, report)
        except Exception as e:  # noqa: BLE001 - 改写失败降级原文，不阻断写章（G3 哲学）
            if self.console is not None:
                self.console.print(
                    f"[yellow]⚠ 去AI味改写失败，保留原文：{e}[/yellow]"
                )
            return DeslopResult(
                text=text,
                level=level,
                changed=False,
                changes=[f"改写失败（{e}），保留原文"],
                metrics=report.metrics,
                report=report,
                via_llm=True,
            )

    # ------------------------------------------------------------------
    # 轻度：规则后处理（复用 PostProcessor，零 LLM）
    # ------------------------------------------------------------------
    def _rule_based_rewrite(
        self, text: str, report: AIFlavorReport
    ) -> DeslopResult:
        from agent.core.anti_ai.post_processor import PostProcessor

        result = PostProcessor().process(text)
        return DeslopResult(
            text=result.text,
            level=AI_FLAVOR_LIGHT,
            changed=result.modified,
            changes=result.changes,
            metrics=report.metrics,
            report=report,
            via_llm=False,
        )

    # ------------------------------------------------------------------
    # 中/重度：LLM 改写
    # ------------------------------------------------------------------
    def _llm_rewrite(
        self, text: str, level: str, report: AIFlavorReport
    ) -> DeslopResult:
        prompt = pm.get("m5.deslop")
        system = prompt.system or "你是网文润色专家。"
        user = prompt.render_user(
            ai_level=level,
            level_label=_LEVEL_LABELS.get(level, level),
            chapter_text=text,
        )
        resp = self.llm.chat_creative(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.6,
            max_tokens=8192,
        )
        new_text, changes = self._extract_rewritten(resp.text)

        if not new_text:
            # 标记缺失 → 降级原文（宁可不动，也不破坏章节）
            return DeslopResult(
                text=text,
                level=level,
                changed=False,
                changes=["改写输出缺少【润色后全文】标记，保留原文"],
                metrics=report.metrics,
                report=report,
                via_llm=True,
            )

        if self._is_editorial_contaminated(new_text):
            # 模型把编辑思维链（门禁/禁用词/逐句分析等）当成【润色后全文】输出 →
            # 拒绝提取并降级原文，避免把分析笔记落进章节正文（G3：绝不破坏章节）。
            return DeslopResult(
                text=text,
                level=level,
                changed=False,
                changes=["改写输出含编辑思维链残留，拒绝提取，保留原文"],
                metrics=report.metrics,
                report=report,
                via_llm=True,
            )

        changed = new_text != text
        if self.console is not None and changed:
            self.console.print(
                f"[dim]  去AI味（{_LEVEL_LABELS[level]}）完成："
                f"{len(text)} → {len(new_text)} 字[/dim]"
            )
        return DeslopResult(
            text=new_text,
            level=level,
            changed=changed,
            changes=changes,
            metrics=report.metrics,
            report=report,
            via_llm=True,
        )

    # ------------------------------------------------------------------
    # 输出解析
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_rewritten(raw: str) -> tuple[str, list[str]]:
        """从模型输出中提取【修改记录】与【润色后全文】。

        Returns:
            (润色后正文, 修改记录列表)；标记缺失时返回 ("", [])。
        """
        if _MARKER_FULL not in raw:
            return "", []

        head, _sep, body = raw.partition(_MARKER_FULL)
        # 修改记录：取【修改记录】之后、按行剥离序号/符号
        changes: list[str] = []
        log_part = head.partition(_MARKER_LOG)[2]
        for ln in log_part.splitlines():
            s = ln.strip().lstrip("-*·0123456789.、 ")
            if s:
                changes.append(s)

        # 正文：去掉标记后的空行与潜在「完整正文：」等残余前缀
        lines = body.splitlines()
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and re.match(
            r"^(完整(正文|文本)|正文|润色后(正文|文本))[:：]\s*$", lines[0].strip()
        ):
            lines.pop(0)
        return "\n".join(lines), changes

    @staticmethod
    def _is_editorial_contaminated(text: str) -> bool:
        """检测提取到的「润色后正文」是否混入了编辑思维链（分析笔记）。

        V4 Flash 偶尔会把「门禁检查/禁用词表/逐句分析」这类编辑思维链当成
        【润色后全文】输出（见 ch155/167/172/177/183 曾被污染）。此处用强特征
        识别：命中任一即判定为污染。只匹配"编辑分析独有措辞"，避免误伤正常
        剧情中合法的「门禁/禁制」等词（如"青云门禁制"）。
        """
        patterns = (
            r"门禁[A-FＦ]",  # 门禁A/B/C/D/E/F
            r"门禁\s*[：:]",  # 门禁：
            r"禁用词",
            r"分析原文",
            r"逐句(检查|修改)",
            r"逐段(检查|分析|修改)",
            r"（这是禁用词",  # 正文内残留的改写注记
            r"修改策略|修改记录：|改写方案|润色说明",
            r"(^|\n)\s*第一遍|(^|\n)\s*第二遍|(^|\n)\s*第三遍",
        )
        return any(re.search(p, text) for p in patterns)
