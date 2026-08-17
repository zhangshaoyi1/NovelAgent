"""E 项目学习闭环：技法提炼（增量 E / T05）

``LearningMiner.extract`` 用 LLM 从指定章节提炼可复用的「写法 / 钩子 / 节奏模板」，
返回 ``Learning`` 列表（LLM 不可用 / 调用异常 → 降级为空，绝不阻断）。

信噪比控制（PRD §E.6）：默认仅当章节本身质量达标才值得提炼；本期实现为
「逐章读取 → 单次合并 LLM 提炼 → 去重落盘」，依赖调用方在合适时机触发
（如 ``learn extract --range`` 用户主动沉淀，或后续 M5 写完高质量章后自动提炼）。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from agent.core.learning_store import Learning, LearningStore
from agent.prompts import E_LEARN_EXTRACT_SYSTEM_PROMPT, E_LEARN_EXTRACT_USER_TEMPLATE
from agent.utils import parse_llm_json


def _read_chapter_text(project_path: Path, n: int) -> str | None:
    """读取指定章节正文（剥离 frontmatter）"""
    f = project_path / "chapters" / f"ch{n:03d}.md"
    if not f.exists():
        return None
    text = f.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            text = parts[2]
    return text.strip()


class LearningMiner:
    """学习技法提炼器（E）"""

    def __init__(self, project_dir: Path, llm: Any | None = None) -> None:
        self.project_dir = Path(project_dir)
        self.llm = llm
        self.store = LearningStore(self.project_dir)

    def extract(self, chapter_nums: list[int]) -> list[Learning]:
        """用 LLM 从指定章节提炼技法

        LLM 不可用 / 调用异常 / 解析失败 → 返回空列表（降级，不阻断）。

        Args:
            chapter_nums: 待提炼章节号列表

        Returns:
            ``Learning`` 列表（id 暂未定稿，由 ``extract_and_save`` 统一编号）
        """
        if self.llm is None:
            return []
        texts: list[str] = []
        for n in chapter_nums:
            t = _read_chapter_text(self.project_dir, n)
            if t:
                texts.append(t)
        if not texts:
            return []

        joined = "\n\n----\n\n".join(texts)
        user = E_LEARN_EXTRACT_USER_TEMPLATE.format(chapter_text=joined)
        try:
            resp = self.llm.chat_utility(
                [
                    {"role": "system", "content": E_LEARN_EXTRACT_SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                ],
                max_tokens=1500,
                enable_thinking=False,
            )
            data = parse_llm_json(resp.text)
        except Exception:  # noqa: BLE001 - 提炼失败降级为空
            return []

        items: list[Learning] = []
        for item in data.get("learnings") or []:
            if not isinstance(item, dict):
                continue
            items.append(Learning(
                id="",
                category=str(item.get("category", "general")),
                text=str(item.get("text", "")),
                source_chapters=list(chapter_nums),
            ))
        return items

    def extract_and_save(self, chapter_nums: list[int]) -> list[Learning]:
        """提炼并去重落盘，返回本次新提炼的条目（已落盘）"""
        items = self.extract(chapter_nums)
        if not items:
            return []
        cur = self.store.load()
        base = len(cur)
        for idx, it in enumerate(items):
            it.id = f"L-{base + idx + 1:03d}"
            it.created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # 合并后整体去重（同 category+text 不重复）
        merged = cur + items
        seen: set[tuple[str, str]] = set()
        deduped: list[Learning] = []
        for x in merged:
            key = (x.category, x.text)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(x)
        self.store.save(deduped)
        return items
