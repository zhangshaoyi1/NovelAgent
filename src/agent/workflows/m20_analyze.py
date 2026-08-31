"""M20 长篇拆文管线（移植 oh-story-claudecod 的 story-long-analyze skill）

6 阶段深度拆解管道：
- Stage 0 概要提取（outline）  → 概要.md + 章节/章节索引.md + 原文备份
- Stage 1 黄金三章（golden）   → 章节/第N章_深度拆解.md ×3 + 快速预览.md（停靠点）
- Stage 2 逐章摘要（summary）  → 章节/第N章_摘要.md（串行，部分失败容忍）
- Stage 3 聚合分析（aggregate）→ 剧情/故事线.md + 剧情/{标题}.md + 剧情/散落情节.md
- Stage 4 设定+角色关系（setting）→ 设定/*.md + 角色/*.md
- Stage 5 汇总报告（report）   → 拆文报告.md

输出目录：{project_dir}/deconstruction/{book}/
断点恢复：读 _progress.md（阶段状态 / 已完成章节 / 失败记录表），
支持 paused_after_stage1 停靠续跑（续跑跳过 Stage 0/1，从 Stage 2 开始）。

所有 LLM 调用统一走 ``agent.client.LLMClient.chat``；提示词统一走
``agent.core.infra.prompt_manager.pm.get("m20.xxx")``。
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console

from agent.client import LLMClient
from agent.core.infra.prompt_manager import pm
from agent.utils import parse_llm_json

# ============================================================
# 常量
# ============================================================
STAGES: dict[int, tuple[str, str]] = {
    0: ("概要提取", "outline"),
    1: ("黄金三章", "golden"),
    2: ("逐章摘要", "summary"),
    3: ("聚合分析", "aggregate"),
    4: ("设定+角色关系", "setting"),
    5: ("汇总报告", "report"),
}
STAGE_NAMES: dict[int, str] = {k: v[0] for k, v in STAGES.items()}
STAGE_NOTES: dict[int, str] = {
    0: "概要提取",
    1: "黄金三章",
    2: "逐章摘要",
    3: "聚合分析",
    4: "设定+关系",
    5: "汇总报告",
}

# ============================================================
# 章节切分（从原文按「第N章/回/卷」标题切分）
# ============================================================
_CN_NUM = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "百": 100, "千": 1000,
}
_CHAPTER_HEADER_RE = re.compile(
    r"^第\s*[0-9一二三四五六七八九十百千零〇两]+\s*[章回卷]"
)
_CHAPTER_NUM_RE = re.compile(
    r"第\s*([0-9一二三四五六七八九十百千零〇两]+)\s*[章回卷]"
)


def _is_chapter_header(line: str) -> bool:
    return bool(_CHAPTER_HEADER_RE.match(line.strip()))


def _cn_to_int(s: str) -> int:
    """中文/阿拉伯混合数字转 int（支持 二十 / 一百二十三 等）。"""
    m = re.search(r"\d+", s)
    if m:
        return int(m.group(0))
    total = 0
    section = 0
    number = 0
    for ch in s:
        if ch == "零":
            number = 0
        elif ch in ("十", "百", "千"):
            if ch == "十" and number == 0:
                number = 1
            section += number * _CN_NUM[ch]
            number = 0
        else:
            number = _CN_NUM.get(ch, 0)
    return total + section + number or 1


def _extract_chapter_number(header: str) -> int:
    m = _CHAPTER_NUM_RE.search(header)
    if not m:
        return 0
    return _cn_to_int(m.group(1))


def split_chapters(text: str) -> list[dict[str, Any]]:
    """把原文按「第N章」标题切分为章节列表。

    Returns:
        [{"number": int, "title": str, "text": str}, ...]
        无章节标题时整体视为单章。
    """
    lines = text.split("\n")
    headers: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        if _is_chapter_header(line):
            headers.append((i, line.strip()))
    if not headers:
        return [{"number": 1, "title": "第1章", "text": text.strip()}]
    chapters: list[dict[str, Any]] = []
    for j, (idx, header) in enumerate(headers):
        end = headers[j + 1][0] if j + 1 < len(headers) else len(lines)
        body = "\n".join(lines[idx + 1 : end]).strip()
        num = _extract_chapter_number(header)
        chapters.append(
            {"number": num if num else (j + 1), "title": header, "text": body}
        )
    return chapters


# ============================================================
# _progress.md 数据模型
# ============================================================
def _table_rows(text: str, section: str) -> list[list[str]]:
    """提取指定 ## 节下的表格数据行（跳过表头/分隔行）。"""
    out: list[list[str]] = []
    in_sec = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("## "):
            in_sec = s[3:].strip() == section
            continue
        if not in_sec or not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if not cells:
            continue
        if all(set(c) <= {"-", " ", ":"} for c in cells):
            continue  # 分隔行
        out.append(cells)
    return out


@dataclass
class _Progress:
    """管道断点进度（_progress.md 的内存形态）。"""

    book: str = ""
    total_chapters: int = 0
    output_dir: str = ""
    started_at: str = ""
    final_status: str = "pending"
    stages: dict[int, str] = field(default_factory=dict)
    completed_chapters: set[int] = field(default_factory=set)
    last_chapter: int = 0
    failures: list[dict[str, str]] = field(default_factory=list)
    quality: dict[str, str] = field(default_factory=dict)

    def next_stage(self) -> int:
        """返回第一个未完成阶段（0-5；全完成返回 6）。"""
        for s in range(6):
            if self.stages.get(s) != "done":
                return s
        return 6

    @classmethod
    def load(cls, path: Path) -> "_Progress | None":
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8")
        p = cls()
        m = re.search(r"- 最终状态：(.+)", text)
        if m:
            p.final_status = m.group(1).strip()
        m = re.search(r"- 小说：(.+?) \| 总章数：(\d+)", text)
        if m:
            p.book = m.group(1).strip()
            p.total_chapters = int(m.group(2))
        m = re.search(r"- 小说：.+?输出目录：(.+?) \| 开始：", text)
        if m:
            p.output_dir = m.group(1).strip()
        m = re.search(r"- 小说：.+?开始：(.+)$", text)
        if m:
            p.started_at = m.group(1).strip()
        for cells in _table_rows(text, "管道进度"):
            sm = re.match(r"Stage\s*(\d+)", cells[0])
            if sm and len(cells) >= 2:
                p.stages[int(sm.group(1))] = cells[1].strip().lower()
        for cells in _table_rows(text, "分块进度"):
            cm = re.match(r"ch(\d+)", cells[0])
            if cm and len(cells) >= 3 and cells[2].strip().lower() == "done":
                p.completed_chapters.add(int(cm.group(1)))
        for cells in _table_rows(text, "失败记录"):
            if not cells or cells[0].strip() in ("", "类型"):
                continue
            p.failures.append(
                {
                    "type": cells[0].strip() if len(cells) > 0 else "",
                    "ref": cells[1].strip() if len(cells) > 1 else "",
                    "error": cells[2].strip() if len(cells) > 2 else "",
                    "retry": cells[3].strip() if len(cells) > 3 else "未重试",
                }
            )
        for cells in _table_rows(text, "质量检查"):
            if not cells or cells[0].strip() in ("", "检查项"):
                continue
            p.quality[cells[0].strip()] = cells[2].strip() if len(cells) > 2 else ""
        m = re.search(r"最后处理：第(\d+)章", text)
        if m:
            p.last_chapter = int(m.group(1))
        return p


# ============================================================
# M20 长篇拆文工作流
# ============================================================
@dataclass
class _StageResult:
    """单阶段执行结果（供内部使用）。"""

    ok: bool = True
    error: str = ""


@dataclass
class M20AnalyzeResult:
    """管道运行结果。"""

    success: bool
    book: str
    total_chapters: int
    output_dir: Path
    start_stage: int
    completed_stages: list[int]
    failures: list[dict[str, str]]
    paused: bool
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "book": self.book,
            "total_chapters": self.total_chapters,
            "output_dir": str(self.output_dir),
            "start_stage": self.start_stage,
            "completed_stages": self.completed_stages,
            "failures": self.failures,
            "paused": self.paused,
            "status": self.status,
        }


@dataclass
class M20AnalyzeWorkflow:  # noqa: F811 - 下方用装饰器包装，无冲突
    """6 阶段长篇拆文管道。"""

    project_dir: Path
    book: str | None = None
    llm: LLMClient | None = None
    console: Console | None = None

    def __post_init__(self) -> None:
        self.project_dir = Path(self.project_dir)
        self.book = self.book or self.project_dir.name
        self.output_dir = self.project_dir / "deconstruction" / self.book
        self.llm = self.llm or LLMClient()
        self.console = self.console or Console()

    # ============================================================
    # 主入口
    # ============================================================
    def run(
        self,
        source: str | Path | None = None,
        stage: int | None = None,
        full: bool = False,
    ) -> M20AnalyzeResult:
        """执行拆文管道。

        Args:
            source: 原文文件路径（可选；不提供则用 原文/ 下已有备份）。
            stage: 起始阶段 0-5；None 时按断点续跑（第一个未完成阶段）。
            full: True 时跳过 Stage 1 停靠询问一次跑完 Stage 2-5。

        Returns:
            M20AnalyzeResult（含 paused/status/completed_stages/failures）。
        """
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if source:
            self._backup_source(Path(source))
        else:
            self._ensure_original_backup()

        chapters = self._parse_chapters()
        if not chapters:
            raise ValueError("未从原文解析出任何章节")

        total = len(chapters)
        progress = self._load_progress()
        if progress is None or progress.total_chapters != total:
            progress = self._new_progress(chapters)
            self._save_progress(progress)

        start = self._resolve_start_stage(stage, progress)
        result = M20AnalyzeResult(
            success=True,
            book=self.book,
            total_chapters=total,
            output_dir=self.output_dir,
            start_stage=start,
            completed_stages=[],
            failures=[],
            paused=False,
            status="pending",
        )

        for s in range(start, 6):
            if progress.stages.get(s) == "done":
                result.completed_stages.append(s)
                continue
            self.console.print(f"[cyan]→ Stage {s} · {STAGE_NAMES[s]}[/cyan]")
            try:
                if s == 0:
                    self._stage0(chapters)
                elif s == 1:
                    self._stage1(chapters)
                elif s == 2:
                    self._stage2(chapters, progress)
                elif s == 3:
                    self._stage3(chapters, progress)
                elif s == 4:
                    self._stage4(chapters, progress)
                elif s == 5:
                    self._stage5(chapters, progress)
                progress.stages[s] = "done"
                result.completed_stages.append(s)
            except Exception as e:  # noqa: BLE001 - 部分失败容忍，记录并继续
                progress.stages[s] = "failed"
                progress.failures.append(
                    {
                        "type": f"stage{s}",
                        "ref": STAGE_NAMES[s],
                        "error": str(e),
                        "retry": "未重试",
                    }
                )
                result.failures.append(
                    {"type": f"stage{s}", "ref": STAGE_NAMES[s], "error": str(e)}
                )
                self.console.print(f"[red]✗ Stage {s} 失败：{e}[/red]")

            # Stage 1 停靠点：非 full 且刚完成 Stage 1 → 停下询问
            if s == 1 and not full:
                progress.final_status = "paused_after_stage1"
                self._save_progress(progress)
                result.paused = True
                result.status = "paused_after_stage1"
                self.console.print(
                    "[dim]已停靠：快速预览.md 已生成，等待确认是否继续 Stage 2-5。[/dim]"
                )
                return result

            self._save_progress(progress)

        progress.final_status = (
            "completed_with_errors" if result.failures else "completed"
        )
        self._save_progress(progress)
        result.status = progress.final_status
        self.console.print(f"[green]✓ 拆文完成：{self.output_dir}[/green]")
        return result

    # ============================================================
    # 原文 / 章节
    # ============================================================
    def _backup_source(self, source: Path) -> Path:
        """把 --source 文件复制到 原文/（保留原文件名）。"""
        if not source.exists():
            raise FileNotFoundError(f"原文文件不存在：{source}")
        orig_dir = self.output_dir / "原文"
        orig_dir.mkdir(parents=True, exist_ok=True)
        dest = orig_dir / source.name
        shutil.copy2(source, dest)
        return dest

    def _ensure_original_backup(self) -> Path:
        """校验 原文/ 已有备份；无则报错。"""
        orig_dir = self.output_dir / "原文"
        files = list(orig_dir.glob("*")) if orig_dir.exists() else []
        if not files:
            raise ValueError("缺少原文：请提供 --source，或输出目录 原文/ 下已有备份")
        return files[0]

    def _source_backup_path(self) -> Path:
        orig_dir = self.output_dir / "原文"
        files = sorted(orig_dir.glob("*")) if orig_dir.exists() else []
        if not files:
            raise ValueError("原文备份不存在（原文/ 目录为空）")
        return files[0]

    def _parse_chapters(self) -> list[dict[str, Any]]:
        src = self._source_backup_path()
        text = src.read_text(encoding="utf-8", errors="ignore")
        return split_chapters(text)

    # ============================================================
    # 进度
    # ============================================================
    def _new_progress(self, chapters: list[dict[str, Any]]) -> _Progress:
        return _Progress(
            book=self.book,
            total_chapters=len(chapters),
            output_dir=str(self.output_dir),
            started_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            final_status="pending",
        )

    def _load_progress(self) -> _Progress | None:
        f = self.output_dir / "_progress.md"
        return _Progress.load(f) if f.exists() else None

    def _save_progress(self, p: _Progress) -> None:
        (self.output_dir / "_progress.md").write_text(
            self._render_progress(p), encoding="utf-8"
        )

    def _render_progress(self, p: _Progress) -> str:
        lines = [
            f"# 深度拆解进度：{p.book}",
            (
                f"- 小说：{p.book} | 总章数：{p.total_chapters} | "
                f"输出目录：{self.output_dir} | 开始：{p.started_at}"
            ),
            f"- 最终状态：{p.final_status}",
            "## 管道进度",
            "| 阶段 | 状态 | 进度 | 备注 |",
            "|------|------|------|------|",
        ]
        for s in range(6):
            status = p.stages.get(s, "pending")
            lines.append(f"| Stage {s} | {status} | - | {STAGE_NOTES[s]} |")
        lines += ["## 分块进度", "| 块 | 章节 | 状态 |", "|------|------|------|"]
        for n in sorted(p.completed_chapters):
            lines.append(f"| ch{n:03d} | 第{n}章 | done |")
        lines += ["## 失败记录", "| 类型 | 章节/阶段 | 错误信息 | 重试状态 |",
                  "|------|----------|---------|---------|"]
        for f in p.failures:
            err = str(f.get("error", "")).replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| {f.get('type', '')} | {f.get('ref', '')} | {err} | "
                f"{f.get('retry', '未重试')} |"
            )
        lines += ["## 质量检查", "| 检查项 | 阶段 | 结果 | 修正 |",
                  "|------|------|------|------|"]
        for k, v in p.quality.items():
            lines.append(f"| {k} | Stage 3 | {v} | - |")
        cur = p.next_stage()
        last = p.last_chapter or (max(p.completed_chapters) if p.completed_chapters else 0)
        next_op = "全部完成" if cur >= 6 else f"Stage {cur} {STAGE_NAMES[cur]}"
        lines += [
            "## 断点",
            (
                f"- 最后处理：第{last}章 | Stage "
                f"{'完成' if cur >= 6 else cur} | 下一操作：{next_op}"
            ),
        ]
        return "\n".join(lines)

    @staticmethod
    def _resolve_start_stage(stage: int | None, progress: _Progress) -> int:
        if stage is not None:
            if not 0 <= int(stage) <= 5:
                raise ValueError(f"非法阶段：{stage}，可选 0-5")
            return int(stage)
        return progress.next_stage()

    # ============================================================
    # Stage 0 概要提取
    # ============================================================
    def _stage0(self, chapters: list[dict[str, Any]]) -> None:
        total_words = sum(len(c["text"]) for c in chapters)
        index_rows = "\n".join(
            f"| 第{c['number']}章 | {c['title']} | {len(c['text'])} |"
            for c in chapters
        )
        index_for_llm = self._truncate_index(index_rows, len(chapters))
        sample = (chapters[0]["text"][:2000]) if chapters else ""
        p = pm.get("m20.outline")
        resp = self.llm.chat(
            messages=[
                {"role": "system", "content": p.render_system()},
                {
                    "role": "user",
                    "content": p.render_user(
                        book=self.book,
                        total_chapters=len(chapters),
                        total_words=total_words,
                        chapter_index=index_for_llm,
                        sample_text=sample,
                        sample_len=len(sample),
                    ),
                },
            ],
            temperature=0.2,
            use="utility",
            validators=p.validation,
            max_tokens=4096,
        )
        data = parse_llm_json(resp.text)
        self._write_outline(chapters, data, total_words)
        self._write("章节/章节索引.md", self._render_chapter_index(chapters))

    @staticmethod
    def _truncate_index(index_rows: str, total: int) -> str:
        lines = index_rows.split("\n")
        if len(lines) <= 400:
            return index_rows
        return "\n".join(lines[:400]) + f"\n……（共 {total} 章，仅节选前 400 章索引）"

    def _write_outline(
        self,
        chapters: list[dict[str, Any]],
        data: dict[str, Any],
        total_words: int,
    ) -> None:
        genre = data.get("genre", "未知")
        platform = data.get("platform", "未知")
        summary = data.get("summary", "")
        protagonist = data.get("protagonist", "")
        gimmick = data.get("core_gimmick", "")
        volumes = data.get("volumes", []) or []
        wan = round(total_words / 10000, 1)
        lines = [
            f"# 概要：{self.book}",
            "",
            (
                f"- 总字数：{total_words} 字（约 {wan} 万字）| 总章数：{len(chapters)} "
                f"| 题材：{genre} | 目标平台：{platform}"
            ),
            "",
            "## 卷/段划分",
            "",
            "| 卷/段 | 章节范围 | 章数 | 预估字数 |",
            "|-------|----------|------|----------|",
        ]
        if volumes:
            for v in volumes:
                lines.append(
                    f"| {v.get('name', '')} | {v.get('chapters', '')} | "
                    f"{v.get('count', '')} | {v.get('words', '')} |"
                )
        else:
            lines.append(f"| 全书 | 第1-{len(chapters)}章 | {len(chapters)} | 约{wan}万字 |")
        lines += ["", "## 全书概要", "", summary, "", "## 核心设定", ""]
        if protagonist:
            lines.append(f"- 主角：{protagonist}")
        if gimmick:
            lines.append(f"- 核心梗：{gimmick}")
        lines += ["", "## 章节索引", "", "| 章节 | 标题 | 字数 |", "|------|------|------|"]
        for c in chapters:
            lines.append(f"| 第{c['number']}章 | {c['title']} | {len(c['text'])} |")
        self._write("概要.md", "\n".join(lines))

    def _render_chapter_index(self, chapters: list[dict[str, Any]]) -> str:
        lines = [f"# 章节索引：{self.book}", "", "| 章节 | 标题 | 字数 |",
                 "|------|------|------|"]
        for c in chapters:
            lines.append(f"| 第{c['number']}章 | {c['title']} | {len(c['text'])} |")
        return "\n".join(lines)

    # ============================================================
    # Stage 1 黄金三章
    # ============================================================
    def _stage1(self, chapters: list[dict[str, Any]]) -> None:
        dives: list[str] = []
        for ch in chapters[:3]:
            dive = self._golden_dive(ch)
            self._write(f"章节/第{ch['number']}章_深度拆解.md", dive)
            dives.append(dive)
        preview = self._generate_preview(chapters, dives)
        self._write("快速预览.md", preview)

    def _golden_dive(self, ch: dict[str, Any]) -> str:
        p = pm.get("m20.golden")
        resp = self.llm.chat(
            messages=[
                {"role": "system", "content": p.render_system()},
                {
                    "role": "user",
                    "content": p.render_user(
                        num=ch["number"],
                        title=ch["title"],
                        word_count=len(ch["text"]),
                        text=ch["text"],
                    ),
                },
            ],
            temperature=0.3,
            use="creative",
            max_tokens=8192,
        )
        return resp.text.strip()

    def _generate_preview(
        self, chapters: list[dict[str, Any]], dives: list[str]
    ) -> str:
        outline = self._read("概要.md")
        p = pm.get("m20.preview")
        resp = self.llm.chat(
            messages=[
                {"role": "system", "content": p.render_system()},
                {
                    "role": "user",
                    "content": p.render_user(
                        book=self.book,
                        summary=outline,
                        golden_dives="\n\n---\n\n".join(dives),
                    ),
                },
            ],
            temperature=0.3,
            use="creative",
            max_tokens=8192,
        )
        return resp.text.strip()

    # ============================================================
    # Stage 2 逐章摘要（串行，部分失败容忍）
    # ============================================================
    def _stage2(
        self, chapters: list[dict[str, Any]], progress: _Progress
    ) -> None:
        done = set(progress.completed_chapters)
        for ch in chapters:
            n = ch["number"]
            if n in done:
                continue
            try:
                summary = self._chapter_summary(ch)
                self._write(f"章节/第{n}章_摘要.md", summary)
                done.add(n)
                progress.completed_chapters = done
                progress.last_chapter = n
            except Exception as e:  # noqa: BLE001 - 单章失败记录不阻断
                progress.failures.append(
                    {
                        "type": "summary",
                        "ref": f"第{n}章",
                        "error": str(e),
                        "retry": "未重试",
                    }
                )
                self.console.print(f"[yellow]⚠ 第{n}章摘要失败：{e}[/yellow]")
            self._save_progress(progress)

    def _chapter_summary(self, ch: dict[str, Any]) -> str:
        p = pm.get("m20.summary")
        resp = self.llm.chat(
            messages=[
                {"role": "system", "content": p.render_system()},
                {
                    "role": "user",
                    "content": p.render_user(
                        num=ch["number"],
                        title=ch["title"],
                        word_count=len(ch["text"]),
                        text=ch["text"],
                    ),
                },
            ],
            temperature=0.2,
            use="utility",
            max_tokens=8192,
        )
        return resp.text.strip()

    # ============================================================
    # Stage 3 聚合分析
    # ============================================================
    def _stage3(
        self, chapters: list[dict[str, Any]], progress: _Progress
    ) -> None:
        summaries = self._read_summaries(chapters)
        p = pm.get("m20.aggregate")
        resp = self.llm.chat(
            messages=[
                {"role": "system", "content": p.render_system()},
                {
                    "role": "user",
                    "content": p.render_user(
                        book=self.book,
                        total_chapters=len(chapters),
                        summaries=summaries,
                    ),
                },
            ],
            temperature=0.2,
            use="utility",
            validators=p.validation,
            max_tokens=8192,
        )
        data = parse_llm_json(resp.text)
        self._write_storylines(data)
        self._write_plots(data)
        progress.quality["覆盖率"] = f"{data.get('coverage', 0)}"
        progress.quality["置信度"] = f"{data.get('confidence', 0)}"
        progress.quality["孤立比例"] = f"{data.get('orphan_ratio', 0)}"

    def _write_storylines(self, data: dict[str, Any]) -> None:
        framework = data.get("framework", {}) or {}
        lines = ["# 故事线", "", "## 故事框架", "", "| 项目 | 内容 |", "|------|------|"]
        fw_map = [
            ("framework_type", "框架类型"),
            ("core_driver", "核心驱动"),
            ("spine_conflict", "主轴矛盾"),
            ("upgrade_mechanism", "升级机制"),
            ("rhythm_pattern", "叙事节奏模式"),
            ("basis", "判断依据"),
        ]
        for key, label in fw_map:
            lines.append(f"| {label} | {framework.get(key, '-')} |")
        lines.append("")
        storylines = data.get("storylines", []) or []
        for sl in storylines:
            lines.append(f"## 故事线：{sl.get('title', '')}")
            lines.append("")
            lines.append(f"- 类型：{sl.get('type', '')}")
            themes = "、".join(sl.get("themes", []) or [])
            if themes:
                lines.append(f"- 主题：{themes}")
            plots = "、".join(sl.get("plot_titles", []) or [])
            if plots:
                lines.append(f"- 包含剧情：{plots}")
            lines.append("")
            lines.append(sl.get("description", ""))
            lines.append("")
        self._write("剧情/故事线.md", "\n".join(lines))

    def _write_plots(self, data: dict[str, Any]) -> None:
        plots = data.get("plots", []) or []
        for pl in plots:
            title = pl.get("title", "未命名剧情")
            lines = [
                f"# 剧情：{title}",
                "",
                "| 项目 | 内容 |",
                "|------|------|",
                f"| 标题 | {title} |",
                f"| 类型 | {pl.get('type', '')} |",
                f"| 章节范围 | {pl.get('chapter_range', '')} |",
                f"| 核心目标 | {pl.get('goal', '')} |",
                f"| 核心冲突 | {pl.get('conflict', '')} |",
                "",
                "## 概要",
                "",
                pl.get("summary", ""),
                "",
            ]
            structure = pl.get("structure", {}) or {}
            if structure:
                lines.append("## 结构分布")
                lines.append("")
                for k, v in structure.items():
                    lines.append(f"- {k}：{v}")
                lines.append("")
            plot_points = pl.get("plot_points", []) or []
            if plot_points:
                lines += ["## 情节点索引", "", "| 序号 | 章节 | 描述 | 归属置信度 |",
                          "|------|------|------|------------|"]
                for i, pp in enumerate(plot_points, 1):
                    lines.append(
                        f"| {i} | {pp.get('chapter', '')} | {pp.get('desc', '')} | - |"
                    )
            self._write(f"剧情/{self._sanitize_filename(title)}.md", "\n".join(lines))
        # 散落情节
        lines = [
            "# 散落情节",
            "",
            f"孤立比例：{data.get('orphan_ratio', 0)}",
            "",
            data.get("orphan_notes", "无"),
        ]
        self._write("剧情/散落情节.md", "\n".join(lines))

    # ============================================================
    # Stage 4 设定 + 角色关系
    # ============================================================
    def _stage4(
        self, chapters: list[dict[str, Any]], progress: _Progress
    ) -> None:
        summaries = self._read_summaries(chapters)
        plots_text = self._read_plots_text()
        p = pm.get("m20.setting")
        resp = self.llm.chat(
            messages=[
                {"role": "system", "content": p.render_system()},
                {
                    "role": "user",
                    "content": p.render_user(
                        book=self.book,
                        summaries=summaries,
                        plots=plots_text,
                    ),
                },
            ],
            temperature=0.2,
            use="utility",
            validators=p.validation,
            max_tokens=8192,
        )
        data = parse_llm_json(resp.text)
        self._write_worldview(data)
        self._write_golden_finger(data)
        self._write_characters(data)
        self._write_relations(data)

    def _write_worldview(self, data: dict[str, Any]) -> None:
        wv = data.get("worldview", {}) or {}
        lines = ["# 世界观", ""]
        if not wv:
            lines.append("本书为现实题材，无特殊世界观设定。")
        else:
            lines.append("## 世界类型")
            lines.append(wv.get("type", "-"))
            lines += ["", "## 力量体系"]
            lines.append(wv.get("power_system", "-"))
            lines += ["", "## 世界结构与主要区域"]
            lines.append(wv.get("geography", "-"))
            factions = wv.get("factions", []) or []
            if factions:
                lines += ["", "## 关键势力", ""]
                for f in factions:
                    lines.append(f"- {f}")
                lines.append("")
            lines += ["", "## 核心规则"]
            lines.append(wv.get("core_rules", "-"))
            lines += ["", "## 特殊设定"]
            lines.append(wv.get("special", "-"))
            refs = wv.get("reference_chapters", []) or []
            if refs:
                lines += ["", f"参考章节：{'、'.join(str(r) for r in refs)}"]
        self._write("设定/世界观.md", "\n".join(lines))

    def _write_golden_finger(self, data: dict[str, Any]) -> None:
        gf = data.get("golden_finger")
        lines = ["# 金手指", ""]
        if not gf:
            lines.append("本书无明显金手指设定。")
        else:
            lines.append(f"- 名称：{gf.get('name', '')}")
            lines.append(f"- 类型：{gf.get('type', '')}")
            lines.append(f"- 核心机制：{gf.get('core_mechanism', '')}")
            lines += ["", "## 描述", ""]
            lines.append(gf.get("description", ""))
            lines += ["", "## 当前能力", ""]
            lines.append(gf.get("current_abilities", ""))
        self._write("设定/金手指.md", "\n".join(lines))

    def _write_characters(self, data: dict[str, Any]) -> None:
        chars = data.get("characters", []) or []
        for ch in chars:
            name = ch.get("name", "").strip()
            if not name:
                continue
            lines = [f"# {name}", ""]
            archetype = ch.get("archetype", "")
            if archetype:
                lines.append(f"- archetype：{archetype}")
            key_plots = ch.get("key_plots", []) or []
            if key_plots:
                lines += ["", "## 关键情节", ""]
                for kp in key_plots:
                    lines.append(f"- {kp}")
            arc = ch.get("arc", "")
            if arc and arc != "-":
                lines += ["", "## 成长弧线", "", arc]
            aliases = ch.get("aliases", []) or []
            if aliases:
                lines += ["", "## 别名", ""]
                for a in aliases:
                    lines.append(
                        f"- {a.get('name', '')}（{a.get('type', '')}，"
                        f"置信度 {a.get('confidence', '')}）"
                    )
            lines += ["", "## 档案", ""]
            lines.append(ch.get("profile", ""))
            self._write(f"角色/{self._sanitize_filename(name)}.md", "\n".join(lines))

    def _write_relations(self, data: dict[str, Any]) -> None:
        relations = data.get("relations", []) or []
        lines = [
            "# 角色关系",
            "",
            "| 角色A | 角色B | 关系类型 | 情感 | 置信度 | 推断 |",
            "|-------|-------|---------|------|--------|------|",
        ]
        for r in relations:
            lines.append(
                f"| {r.get('a', '')} | {r.get('b', '')} | "
                f"{r.get('relation_type', '')} | {r.get('emotion', '')} | "
                f"{r.get('confidence', '')} | {r.get('inferred', '')} |"
            )
        lines.append("")
        for r in relations:
            lines.append(f"## {r.get('a', '')} ⇄ {r.get('b', '')}")
            lines.append("")
            lines.append(r.get("description", ""))
            evo = r.get("evolution", "")
            if evo:
                lines += ["", f"演变：{evo}"]
            lines.append("")
        self._write("角色/角色关系.md", "\n".join(lines))

    # ============================================================
    # Stage 5 汇总报告
    # ============================================================
    def _stage5(
        self, chapters: list[dict[str, Any]], progress: _Progress
    ) -> None:
        summary = self._read("概要.md")
        golden_dives = self._read_golden_dives(chapters)
        plots_text = self._read_plots_text()
        settings_text = self._read_settings_text()
        coverage = progress.quality.get("覆盖率", "-")
        confidence = progress.quality.get("置信度", "-")
        failures = (
            "；".join(
                f"{f.get('ref', '')}: {f.get('error', '')}" for f in progress.failures
            )
            or "无"
        )
        p = pm.get("m20.report")
        resp = self.llm.chat(
            messages=[
                {"role": "system", "content": p.render_system()},
                {
                    "role": "user",
                    "content": p.render_user(
                        book=self.book,
                        total_chapters=len(chapters),
                        total_words=sum(len(c["text"]) for c in chapters),
                        summary=summary,
                        golden_scores=golden_dives,
                        plots=plots_text,
                        settings=settings_text,
                        coverage=coverage,
                        confidence=confidence,
                        failures=failures,
                    ),
                },
            ],
            temperature=0.3,
            use="creative",
            max_tokens=8192,
        )
        self._write("拆文报告.md", resp.text.strip())

    # ============================================================
    # 读取 / 写入辅助
    # ============================================================
    def _write(self, rel: str, content: str) -> Path:
        f = self.output_dir / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")
        return f

    def _read(self, rel: str) -> str:
        f = self.output_dir / rel
        return f.read_text(encoding="utf-8") if f.exists() else ""

    def _read_summaries(self, chapters: list[dict[str, Any]]) -> str:
        parts: list[str] = []
        for ch in chapters:
            f = self.output_dir / "章节" / f"第{ch['number']}章_摘要.md"
            if f.exists():
                parts.append(
                    f"### 第{ch['number']}章 {ch['title']}\n\n"
                    f"{f.read_text(encoding='utf-8')}"
                )
        return "\n\n".join(parts) or "（无章节摘要）"

    def _read_golden_dives(self, chapters: list[dict[str, Any]]) -> str:
        parts: list[str] = []
        for ch in chapters[:3]:
            f = self.output_dir / "章节" / f"第{ch['number']}章_深度拆解.md"
            if f.exists():
                parts.append(f.read_text(encoding="utf-8"))
        return "\n\n---\n\n".join(parts)

    def _read_plots_text(self) -> str:
        plots_dir = self.output_dir / "剧情"
        if not plots_dir.exists():
            return "（无剧情聚合结果）"
        parts: list[str] = []
        for f in sorted(plots_dir.glob("*.md")):
            if f.stem == "散落情节":
                continue
            parts.append(f.read_text(encoding="utf-8"))
        return "\n\n".join(parts) or "（无剧情聚合结果）"

    def _read_settings_text(self) -> str:
        parts: list[str] = []
        for sub in ("设定", "角色"):
            d = self.output_dir / sub
            if d.exists():
                for f in sorted(d.glob("*.md")):
                    parts.append(f"## {sub}/{f.stem}\n\n{f.read_text(encoding='utf-8')}")
        return "\n\n".join(parts) or "（无设定/角色数据）"

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        return re.sub(r'[\\/:*?"<>|]', "_", name).strip()


# 注册到 workflow 注册表（pkgutil 自动发现，装饰器即注册点）
from agent.core.engine.workflow_registry import workflow  # noqa: E402

workflow("m20_analyze")(M20AnalyzeWorkflow)
