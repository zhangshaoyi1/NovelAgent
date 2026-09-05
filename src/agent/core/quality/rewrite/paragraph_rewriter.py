"""段落级局部重写（竞品差距改进计划 P1-7，对标 MuMuAINovel partial-regenerate）。

问题：``rewrite`` 是章级定向改写——只想改某一段时，整章重写既浪费 token 又有
"改动扩散"风险（枪手顺手改了别处）。

方案：定位单个段落 → 仅携带该段 + 受限前后文窗口（前后各 1 段 + 全局约束）调 LLM
→ 输出前后 diff → 确认后仅替换该段落落盘。支持 ``plan``（离线 dry-run，不调 LLM，
只出定位方案与上下文窗口供用户预览）。

纯定位/拆分逻辑离线可测；LLM 调用失败优雅降级（保留原段，降级不阻断）。
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import frontmatter

_SYSTEM_PROMPT = """你是资深网文代笔枪手，正在对小说中的**单个段落**做定向重写。

铁律：
1. 只重写指定的目标段落，输出且仅输出这一个段落的替换文本（无标题、无解释）。
2. 不得改变段落承担的情节功能（该段推进的事件/信息/钩子必须保留）。
3. 与给定的上文/下文自然衔接：人称、时态、场景、语言指纹保持一致。
4. 不引入新的人物、设定、伏笔；不出现任何英文与写作元指令。
"""

_USER_TEMPLATE = """# 上文（不可改动，仅供衔接）
{context_before}

# 目标段落（要重写的段落）
{old_paragraph}

# 下文（不可改动，仅供衔接）
{context_after}

# 修改指令
{instruction}

# 任务
请重写目标段落，仅输出替换后的段落文本。"""


@dataclass
class ParagraphRewriteResult:
    """单段重写结果。"""

    chapter_num: int
    paragraph_index: int          # 1-based 段落序号
    old_paragraph: str
    new_paragraph: str
    diff_text: str                # unified diff（旧段 → 新段）
    applied: bool                 # 是否已落盘
    llm_used: bool = True
    backup_file: Path | None = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "chapter": self.chapter_num,
            "paragraph_index": self.paragraph_index,
            "old_paragraph": self.old_paragraph,
            "new_paragraph": self.new_paragraph,
            "diff": self.diff_text,
            "applied": self.applied,
            "llm_used": self.llm_used,
            "backup_file": str(self.backup_file) if self.backup_file else None,
            "error": self.error,
        }


def split_paragraphs(body: str) -> list[str]:
    """按空行把正文拆为段落列表（与 m5 P-FMT 的段落约定一致，去掉首尾空白）。"""
    return [p.strip() for p in re.split(r"\n\s*\n", body or "") if p.strip()]


def make_diff(old: str, new: str) -> str:
    """两段文本的 unified diff（行级）。"""
    if old == new:
        return ""
    lines = difflib.unified_diff(
        old.splitlines(), new.splitlines(),
        fromfile="旧段落", tofile="新段落", lineterm="",
    )
    return "\n".join(lines)


class ParagraphRewriter:
    """段落级局部重写器。

    Args:
        project_dir: 小说项目目录。
        llm_client: LLM 客户端（创作模型）；``plan`` 不需要。
        console: rich 控制台（可注入 quiet console）。
    """

    def __init__(self, project_dir: Path | str, llm_client: Any = None, console: Any = None) -> None:
        self.project_dir = Path(project_dir)
        self.chapters_dir = self.project_dir / "chapters"
        self.llm_client = llm_client
        self.console = console

    # ---------------------------------------------------------- 定位
    def _load_body(self, chapter_num: int) -> tuple[Path, Any, str, list[str]]:
        """读章：返回 (文件, post, 章标题行, 段落列表)。标题行不计入段落序号。"""
        chapter_file = self.chapters_dir / f"ch{chapter_num:03d}.md"
        if not chapter_file.exists():
            raise FileNotFoundError(f"章节文件不存在：{chapter_file}")
        post = frontmatter.load(chapter_file)
        body = self._strip_frontmatter(chapter_file)
        heading, rest = "", body
        if body.startswith("# "):
            heading, _, rest = body.partition("\n")
        return chapter_file, post, heading, split_paragraphs(rest)

    @staticmethod
    def _strip_frontmatter(file: Path) -> str:
        """读取正文（剥离 frontmatter，与 FeedbackRewriter 同口径）。"""
        raw = file.read_text(encoding="utf-8")
        if raw.startswith("---"):
            end = raw.find("\n---", 3)
            if end != -1:
                raw = raw[raw.find("\n", end + 1) + 1:]
        return raw.strip()

    def locate(
        self,
        chapter_num: int,
        paragraph: str,
        paragraphs: list[str] | None = None,
    ) -> int:
        """定位段落：``"3"`` 按 1-based 序号；否则按原文片段（必须唯一命中）。

        Returns:
            1-based 段落序号。

        Raises:
            ValueError: 序号越界 / 片段未命中 / 片段命中多处（歧义）。
        """
        paras = paragraphs if paragraphs is not None else self._load_body(chapter_num)[3]
        token = (paragraph or "").strip()
        if token.isdigit():
            idx = int(token)
            if not 1 <= idx <= len(paras):
                raise ValueError(
                    f"段落序号 {idx} 越界（本章共 {len(paras)} 段）"
                )
            return idx
        hits = [i + 1 for i, p in enumerate(paras) if token and token in p]
        if not hits:
            raise ValueError("未在正文中命中该段落片段，请复制更长的原文片段")
        if len(hits) > 1:
            raise ValueError(
                f"片段命中 {len(hits)} 处（段落 {'、'.join(map(str, hits))}），"
                f"请提供更长片段或改用段落序号"
            )
        return hits[0]

    # ---------------------------------------------------------- 方案（离线 dry-run）
    def plan(
        self, chapter_num: int, paragraph: str, instruction: str
    ) -> dict[str, Any]:
        """离线出方案：定位段落 + 受限上下文窗口 + 将执行的指令。不调 LLM。"""
        chapter_file, _post, _heading, paras = self._load_body(chapter_num)
        idx = self.locate(chapter_num, paragraph, paras)
        i = idx - 1
        return {
            "chapter": chapter_num,
            "chapter_file": str(chapter_file),
            "paragraph_index": idx,
            "total_paragraphs": len(paras),
            "old_paragraph": paras[i],
            "context_before": paras[i - 1] if i >= 1 else "（本书/章首，无上文）",
            "context_after": paras[i + 1] if i + 1 < len(paras) else "（章末，无下文）",
            "instruction": instruction,
        }

    # ---------------------------------------------------------- 重写
    def rewrite(
        self,
        chapter_num: int,
        paragraph: str,
        instruction: str,
        *,
        backup: bool = True,
        confirm_fn: Any = None,
    ) -> ParagraphRewriteResult:
        """定位 → 受限窗口调 LLM → diff → 确认 → 仅替换该段落落盘。

        Args:
            paragraph: 1-based 序号（``"3"``）或唯一原文片段。
            instruction: 该段的修改指令。
            confirm_fn: 确认闸口 ``Callable[[dict 方案], bool]``；返回 False 则不调 LLM 不落盘。
        """
        scheme = self.plan(chapter_num, paragraph, instruction)
        idx = scheme["paragraph_index"]
        _file, post, heading, paras = self._load_body(chapter_num)
        old_paragraph = scheme["old_paragraph"]

        if confirm_fn is not None and not confirm_fn(scheme):
            return ParagraphRewriteResult(
                chapter_num=chapter_num,
                paragraph_index=idx,
                old_paragraph=old_paragraph,
                new_paragraph=old_paragraph,
                diff_text="",
                applied=False,
                llm_used=False,
                error="confirm_rejected",
            )

        new_paragraph = self._call_rewrite(scheme)
        if new_paragraph is None:
            return ParagraphRewriteResult(
                chapter_num=chapter_num,
                paragraph_index=idx,
                old_paragraph=old_paragraph,
                new_paragraph=old_paragraph,
                diff_text="",
                applied=False,
                llm_used=False,
                error="llm_unavailable",
            )
        new_paragraph = new_paragraph.strip()
        if not new_paragraph:
            return ParagraphRewriteResult(
                chapter_num=chapter_num,
                paragraph_index=idx,
                old_paragraph=old_paragraph,
                new_paragraph=old_paragraph,
                diff_text="",
                applied=False,
                llm_used=True,
                error="llm_empty_output",
            )

        diff = make_diff(old_paragraph, new_paragraph)
        backup_file: Path | None = None
        if backup:
            backup_file = self._backup(self.chapters_dir / f"ch{chapter_num:03d}.md")

        # 仅替换目标段落，其余段落原样；标题行保持最前
        paras[idx - 1] = new_paragraph
        chapter_file = self.chapters_dir / f"ch{chapter_num:03d}.md"
        body = f"{heading}\n\n" + "\n\n".join(paras) if heading else "\n\n".join(paras)
        self._save(chapter_file, post, body)

        return ParagraphRewriteResult(
            chapter_num=chapter_num,
            paragraph_index=idx,
            old_paragraph=old_paragraph,
            new_paragraph=new_paragraph,
            diff_text=diff,
            applied=True,
            llm_used=True,
            backup_file=backup_file,
        )

    # ---------------------------------------------------------- 内部
    def _call_rewrite(self, scheme: dict[str, Any]) -> str | None:
        if self.llm_client is None:
            return None
        try:
            from agent.client.gateway_adapter import chat_creative

            resp = chat_creative(
                self.llm_client,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": _USER_TEMPLATE.format(
                            context_before=scheme["context_before"],
                            old_paragraph=scheme["old_paragraph"],
                            context_after=scheme["context_after"],
                            instruction=scheme["instruction"],
                        ),
                    },
                ],
                temperature=0.7,
                max_tokens=1200,
                enable_thinking=False,
            )
            return resp.strip()
        except Exception:  # noqa: BLE001 - LLM 不可达降级保留原段
            return None

    def _backup(self, chapter_file: Path) -> Path:
        backup_dir = self.project_dir / ".state" / "rewrite_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        from datetime import datetime

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dst = backup_dir / f"{chapter_file.stem}-{stamp}.md"
        dst.write_text(chapter_file.read_text(encoding="utf-8"), encoding="utf-8")
        return dst

    def _save(self, chapter_file: Path, post: Any, body: str) -> None:
        # P0-3 原子写：与章节主链路同一落盘纪律
        from agent.core.infra.atomic import atomic_write_text

        atomic_write_text(chapter_file, frontmatter.dumps(frontmatter.Post(body, **post.metadata)))


__all__ = [
    "ParagraphRewriteResult",
    "ParagraphRewriter",
    "make_diff",
    "split_paragraphs",
]
