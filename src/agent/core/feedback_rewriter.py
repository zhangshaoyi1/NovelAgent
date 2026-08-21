"""A3 反馈→定向改写闭环（用户好用核心能力）

把用户针对某一章的反馈（"这章太拖" / "主角太蠢" / "感情戏不够"）变成**局部定向重写**，
而不是只能整章回退或整本重跑。这是把"枪手"变成"听话的枪手"的关键黏性闭环。

设计要点（契合项目"降级不阻断"哲学）：
- 复用 LLMClient（chat_creative 重写 + 可选 chat_utility 生成改动摘要）、
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

from agent.core.guardrails import GateMode, Guardrails, build_guardrails
from agent.core.llm_client import LLMClient
from agent.core.setting_manager import SettingManager


# ============================================================
# 提示词（自包含，避免侵入 prompts.py）
# ============================================================
REWRITE_SYSTEM_PROMPT = """你是一位资深网文代笔枪手，正在根据用户（主编）对某一章的反馈做**定向重写**。

铁律（违反任意一条都算改写失败）：
1. **只改用户要求改的地方**，其余情节、设定、已埋伏笔、已设章末钩子、人物关系一律原样保留。
2. **不许改事实**：已出场角色的姓名/身份/动机、已建立的境界/金手指规则、前文已揭示的真相，
   以及本章与上一章/下一章的衔接句，必须无缝衔接，绝不能出现前后矛盾。
3. **保持人物声音**：角色的语言指纹（口头禅、句式）与上文一致，不能换一个人说话。
4. **不许注水也不许砍断爽点**：用户说"太拖"就收紧节奏、删冗余铺垫；用户说"太蠢"就补动机/补逻辑，
   但绝不能删掉已有的钩子与高潮信息。
5. 输出**仅正文**（Markdown，可含小标题），不要任何作者注、不要解释你改了什么。

你的目标：用最少的改动，精准命中用户反馈，同时让这一章读起来像从未被改过一样连贯。"""

REWRITE_USER_TEMPLATE = """# 本章原文
{chapter_text}

# 用户（主编）反馈
{feedback}

# 必须保留的上下文锚点（不可改动）
- 题材：{genre}｜基调：{tone}｜节奏预期：{rhythm}｜目标字数：{chapter_length}
- 世界观简介：{synopsis}
- 本章涉及角色：{characters}
- 衔接上文（上一章尾，不可破坏）：{prev_tail}
- 衔接下文（下一章头，不可破坏）：{next_head}
- 历史偏好（此前用户反馈沉淀，尽量顺手吸收）：{learnings}

# 任务
请针对上面的反馈对本章做定向重写。严格遵循系统提示的铁律，输出重写后的完整正文。"""


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


# ============================================================
# 核心组件
# ============================================================
class FeedbackRewriter:
    """反馈驱动的章节定向重写器。

    Args:
        project_dir: 小说项目目录。
        llm_client: LLM 客户端（创作模型）；None 则内部惰性构造 LLMClient()。
        guardrails: 护栏实例；None 则用默认 Guardrails()（仅创作残留级校验）。
        console: rich 控制台。
        learning_store: 长期偏好记忆；None 则内部惰性构造。
    """

    def __init__(
        self,
        project_dir: str | Path,
        llm_client: LLMClient | None = None,
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
    def llm(self) -> LLMClient:
        if self._llm is None:
            self._llm = LLMClient()
        return self._llm

    @property
    def learning_store(self):
        if self._learning_store is None:
            from agent.core.learning_store import LearningStore

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
    ) -> RewriteResult:
        """对指定章节做反馈驱动的定向重写。

        Args:
            chapter_num: 章节号（1-based）。
            feedback: 用户反馈文本（自由语言）。
            backup: 是否先备份原章。
            gate_mode: ``advisory``（默认，违规告警不阻断）/ ``block``（违规拒绝落盘）。
            record_learning: 改写成功后是否把反馈沉淀为长期偏好。

        Returns:
            RewriteResult；LLM 不可用时 ``llm_used=False``、``new_text`` 回退原章正文。
        """
        gate = GateMode(gate_mode) if gate_mode in ("advisory", "block") else GateMode.ADVISORY
        chapter_file = self.chapters_dir / f"ch{chapter_num:03d}.md"
        if not chapter_file.exists():
            raise FileNotFoundError(f"章节文件不存在：{chapter_file}")

        post = frontmatter.load(chapter_file)
        old_text = self._strip_frontmatter(chapter_file)
        old_wc = _wc(old_text)

        # 1. 上下文锚点
        ctx = self._build_context(chapter_num, post)

        # 2. 调 LLM 定向重写（失败优雅降级）
        try:
            new_text = self._call_rewrite(old_text, feedback, ctx)
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

        # 3. 护栏校验
        gr = self.guardrails.check(new_text)
        passed = gr.passed
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

        new_wc = _wc(new_text)
        changed_summary = (
            f"按反馈重写：字数 {old_wc}→{new_wc}"
            + ("（含合规告警）" if not passed else "")
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

    # ---------------------------------------------------------- 内部：LLM 调用
    def _call_rewrite(self, old_text: str, feedback: str, ctx: dict[str, Any]) -> str:
        user_prompt = REWRITE_USER_TEMPLATE.format(
            chapter_text=old_text[:12000],
            feedback=feedback,
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
        resp = self.llm.chat_creative(
            messages=[
                {"role": "system", "content": REWRITE_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.7,
            max_tokens=6000,
            enable_thinking=False,
        )
        return resp.text.strip()

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
        except Exception:  # noqa: BLE001 - 偏好沉淀失败不影响改写交付
            pass

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

    @staticmethod
    def _save_rewritten(chapter_file: Path, post: Any, new_text: str, feedback: str) -> None:
        meta = dict(post.metadata)
        meta["last_rewrite_feedback"] = feedback
        meta["last_rewrite_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        meta["revision_count"] = int(meta.get("revision_count", 0) or 0) + 1
        # 标题保持一致（从首行推断）
        first = new_text.strip().split("\n", 1)[0].strip()
        first = re.sub(r"^#+\s*", "", first)
        if first and not first.startswith("第") and len(first) <= 30:
            meta["title"] = first
        body = f"# 第 {meta.get('chapter', '?')} 章 · {meta.get('title', '')}\n\n{new_text}"
        new_post = frontmatter.Post(body, **meta)
        chapter_file.write_text(frontmatter.dumps(new_post), encoding="utf-8")
