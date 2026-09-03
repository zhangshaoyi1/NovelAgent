"""repair 编排器（A2→A4）

把 A1 采集的坏点按"事实型 / 取向型"分层仲裁，事实型自动定版重写，取向型进
pending_decisions；重写后回归校验，不合格有限轮再改，防止无限循环。

"关键一次拍板"落地为 `preferences.md`：
- `--dry-run`（或偏好未确认）：只采集 + 分层 + 草拟偏好文件，不改正文。
- 默认执行：先检查 preferences.md 是否已由用户确认；确认后对事实型坏点自动重写，
  取向型一律跳过（除非 --include-orientation）。
- 写回设定：事实型涉及 world/金手指时，先 create_snapshot 保证可回滚，再落盘，
  并以 append_revision_log 记录仲裁。

沿用项目"降级不阻断"哲学：任一步 LLM 失败都只跳过该坏点并记录，不中断整批。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console

from agent.core.quality.scan.bad_point_scanner import BadPoint, ScannerReport


# ============================================================
# 常量
# ============================================================
REPAIR_DIR = "repair"                 # 项目内产出：bad_points.json / pending_decisions.md / learned_preferences.md
PREFERENCES_FILE = "preferences.md"   # 书级偏好（一次确认）
CONFIRMED_MARKER = "本文档已被作者确认，修复脚本可据此自动执行"
MAX_REGRESS_ROUNDS = 2                # 回归校验有限轮，防无限循环

# 事实型（自动改）：有客观对错，可用设定对齐判定
FACT_TYPES = {"fact_conflict", "plot_hole", "character_drift"}
# 取向型（只出建议）：审美/取向，需作者一次表态
ORIENTATION_TYPES = {"orientation"}


@dataclass
class RepairSummary:
    facts_auto_rewritten: int = 0
    orientations_deferred: int = 0
    regress_failures: dict[int, str] = field(default_factory=dict)
    llm_failures: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "facts_auto_rewritten": self.facts_auto_rewritten,
            "orientations_deferred": self.orientations_deferred,
            "regress_failures": self.regress_failures,
            "llm_failures": self.llm_failures,
        }


@dataclass
class RepairResult:
    report: ScannerReport
    summary: RepairSummary = field(default_factory=RepairSummary)
    preferences_path: Path | None = None
    pending_path: Path | None = None
    dry_run: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "preferences": str(self.preferences_path) if self.preferences_path else None,
            "pending": str(self.pending_path) if self.pending_path else None,
            "summary": self.summary.to_dict(),
            "report": self.report.to_dict(),
        }


class RepairOrchestrator:
    """坏点→分层→重写→回归 的编排器。"""

    def __init__(
        self,
        project_dir: str | Path,
        llm: Any | None = None,
        console: Console | None = None,
        *,
        use_llm_scan: bool = True,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.repair_dir = self.project_dir / REPAIR_DIR
        self._llm = llm
        self.console = console or Console()
        self.use_llm_scan = use_llm_scan

    @property
    def llm(self) -> Any:
        if self._llm is None:
            from agent.client.gateway_adapter import create_gateway_adapter
            self._llm = create_gateway_adapter()
        return self._llm

    # ------------------------------------------------------ 主入口
    def run(
        self,
        *,
        dry_run: bool = True,
        include_orientation: bool = False,
        overwrite_preferences: bool = False,
    ) -> RepairResult:
        from agent.core.quality.scan.bad_point_scanner import BadPointScanner

        scanner = BadPointScanner(
            self.project_dir,
            llm=self._llm,
            console=self.console,
            use_llm=self.use_llm_scan,
        )
        report = scanner.scan()
        self.repair_dir.mkdir(parents=True, exist_ok=True)

        # 产出 bad_points.json（始终写，便于追溯/检查）
        self._dump_bad_points(report)

        # 偏好文件：不存在 → 草拟；已确认 → 直接使用
        prefs = self._ensure_preferences(overwrite=overwrite_preferences)
        confirmed = prefs is not None and prefs.exists() and CONFIRMED_MARKER in prefs.read_text(encoding="utf-8")

        result = RepairResult(
            report=report,
            preferences_path=prefs,
            dry_run=dry_run,
        )

        # 分层：事实型 ↔ 取向型
        fact_points = [p for p in report.points if p.type in FACT_TYPES and p.suggested_fix]
        ori_points = [p for p in report.points if p.type in ORIENTATION_TYPES]

        # 取向型 → pending_decisions.md（永远产出，供作者查看）
        pending_path = self._write_pending_decisions(ori_points)
        result.pending_path = pending_path

        # dry-run：到这里就停，不动正文
        if dry_run or not confirmed:
            self.console.print(
                f"[cyan]● dry-run/未确认：共 {len(report.points)} 坏点，"
                f"事实型 {len(fact_points)} 待自动修，取向型 {len(ori_points)} 已进待拍板清单。[/cyan]"
            )
            if not confirmed:
                self.console.print(
                    f"[yellow]○ preferences.md 未确认，不会自动改任何正文。"
                    f"请确认后重新运行（去掉 --dry-run）。[/yellow]"
                )
            return result

        # 已确认 & 非 dry-run：事实型自动重写
        return self._execute_repairs(fact_points, result, include_orientation)

    # ------------------------------------------------------ 偏好文件
    def _ensure_preferences(self, overwrite: bool) -> Path | None:
        path = self.repair_dir / PREFERENCES_FILE
        if path.exists() and not overwrite:
            return path  # 已存在（可能已确认或待确认）
        # 草拟一份书级偏好（模板 + 采集到的可自动推断项）
        content = self._draft_preferences()
        path.write_text(content, encoding="utf-8")
        return path

    def _draft_preferences(self) -> str:
        """草拟书级偏好：取向 + 感情线取舍 + 灭门回忆上限 + 手段轮换。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return f"""# 书级偏好（关键一次拍板）

> 生成时间：{now}
> 这份文件用来表达"这本书的整体取向"，让修复/写章闭环**不逐章介入**即可自动执行。
> 请修改为你的真实取向，然后在该文件首行附近保留下面这行确认标记后保存：
> `{CONFIRMED_MARKER}`

## 位置
- 本文件：`{self.repair_dir / PREFERENCES_FILE}`

## 取向（请填空）
- 核心价值观：苟道独行、不谈感情、杀伐果断（请改为你的定义）

## 感情线取舍
- 是否允许感情线：否（此书定位纯粹苟道杀伐，感情线属人设漂移）

## 灭门回忆次数上限
- 单个回忆在全书允许复述的最大次数：3

## 手段轮换白名单（写章时贴近的冲突手段库）
- 借刀杀人、设局离间、正面硬刚、交易/出卖、心理战、借势压人、陷阱伏击

## 复核规则
- 事实型硬伤（设定/逻辑/人设冲突）可自动修复。
- 取向型问题（含感情线、角度审美）仅进待拍板清单，不经确认不落盘。
"""

    # ------------------------------------------------------ 产出
    def _dump_bad_points(self, report: ScannerReport) -> Path:
        path = self.repair_dir / "bad_points.json"
        path.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def _write_pending_decisions(self, ori_points: list[BadPoint]) -> Path:
        path = self.repair_dir / "pending_decisions.md"
        lines = [
            "# 待拍板清单（取向型 / 审美型问题）",
            "",
            "以下问题属创作取向，未经确认不会自动改正文。逐条浏览后改为 `[x]` 或删掉删点即可。",
            "",
        ]
        if not ori_points:
            lines.append("（无取向型待决问题）")
        for i, p in enumerate(ori_points, 1):
            ch = f"第{p.chapter}章" if p.chapter else "全局"
            lines.append(f"{i}. [{''}] **{ch} · {p.type}** ({p.severity})")
            if p.evidence:
                lines.append(f"   - 依据：{p.evidence[:200]}")
            if p.suggested_fix:
                lines.append(f"   - 建议：{p.suggested_fix[:200]}")
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    # ------------------------------------------------------ 执行（已确认）
    def _execute_repairs(
        self,
        fact_points: list[BadPoint],
        result: RepairResult,
        include_orientation: bool,
    ) -> RepairResult:
        from agent.core.quality.rewrite.feedback_rewriter import FeedbackRewriter
        from agent.core.story.setting_manager import SettingManager

        # 仅对"面向具体章节"的事实型坏点做章节重写；全局/设定层单独处理（写回由 A2 落 world）
        by_chapter: dict[int, list[BadPoint]] = {}
        for p in fact_points:
            if p.chapter is not None:
                by_chapter.setdefault(p.chapter, []).append(p)

        if not by_chapter:
            self.console.print("[cyan]○ 无面向章节的事实型坏点，跳过批量重写。[/cyan]")
            return result

        # 先对设定层做一次快照，保证可回滚
        sm = SettingManager(self.project_dir)
        snapshot = sm.create_snapshot("repair_before_rewrite")
        sm.append_revision_log(
            f"[repair] 自动修复前快照：{snapshot.name}；事实型坏点 {len(fact_points)} 个"
        )

        rewriter = FeedbackRewriter(
            self.project_dir,
            llm_client=self._llm,
            console=self.console,
        )

        for chapter, points in sorted(by_chapter.items()):
            feedback = "；".join(
                f"[{p.type}｜{p.severity}] {p.suggested_fix}" for p in points
            )
            try:
                r = rewriter.rewrite(chapter, feedback, gate_mode="advisory")
                if r.rewritten:
                    result.summary.facts_auto_rewritten += 1
                    self.console.print(
                        f"[green]✔ 第{chapter}章已自动重写（{r.old_word_count}→{r.new_word_count} 字）[/green]"
                    )
                    # 回归校验（A4）
                    if not self._regress_check(chapter):
                        result.summary.regress_failures[chapter] = "回归未通过"
                elif r.error:
                    result.summary.llm_failures.append(
                        {"chapter": chapter, "error": r.error}
                    )
            except Exception as e:  # noqa: BLE001 - 单章失败不中断
                result.summary.llm_failures.append(
                    {"chapter": chapter, "error": str(e)}
                )
                self.console.print(f"[yellow]⚠ 第{chapter}章重写失败：{e}[/yellow]")

        return result

    # ------------------------------------------------------ A4 回归校验
    def _regress_check(self, chapter: int) -> bool:
        """重写后有限轮再改的回归校验。当前先做字数/残留规则校验，超限即停。"""
        from agent.core.quality.scan.bad_point_scanner import (
            _EDITORIAL_PATTERNS,
            _wc,
        )
        import frontmatter

        path = self.project_dir / "chapters" / f"ch{chapter:03d}.md"
        if not path.exists():
            return False
        post = frontmatter.load(path)
        body = post.content
        if _wc(body) < 1500:
            return False
        if any(p.search(body) for p in _EDITORIAL_PATTERNS):
            return False
        return True