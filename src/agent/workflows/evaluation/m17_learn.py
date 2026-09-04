"""E 项目学习闭环：技法提炼（增量 E / T05）

``LearningMiner.extract`` 用 LLM 从指定章节提炼可复用的「写法 / 钩子 / 节奏模板」，
返回 ``Learning`` 列表（LLM 不可用 / 调用异常 → 降级为空，绝不阻断）。

信噪比控制（PRD §E.6）：默认仅当章节本身质量达标才值得提炼；本期实现为
「逐章读取 → 单次合并 LLM 提炼 → 去重落盘」，依赖调用方在合适时机触发
（如 ``learn extract --range`` 用户主动沉淀，或后续 M5 写完高质量章后自动提炼）。
"""

from __future__ import annotations

import json

from agent.core.story.technique_store import SLOT_NAMES, TechniqueAsset, TechniqueStore
from agent.core.infra.prompt_manager import pm
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.core.story.learning_store import Learning, LearningStore
from agent.client.gateway_adapter import chat_utility
from agent.utils import parse_llm_json

_SKILL_DIR = Path(__file__).resolve().parents[2] / "skills" / "learning-imitation"


def json_str(obj: Any) -> str:
    """json.dumps 的简写（ensure_ascii=False）。"""
    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return str(obj)


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
        user = pm.get("e.learn_extract").render_user(chapter_text=joined)
        try:
            resp = chat_utility(
                self.llm,
                [
                    {"role": "system", "content": pm.get("e.learn_extract").system},
                    {"role": "user", "content": user},
                ],
                max_tokens=1500,
                enable_thinking=False,
            )
            data = parse_llm_json(resp)
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


# ---------------------------------------------------------------- 三阶段学习仿写（G15 P0-4）
class LearningImitationMiner:
    """三阶段学习仿写：拆素材 → 学剧情 → 学文风，产出六槽位技法资产（先预览后确认）。

    对标 DeepWrite ``learning-imitation``。产出先写预览区（``TechniqueStore``），
    LLM 不可用 / 调异常 → 返回空，绝不阻断。

    Args:
        project_dir: 项目目录。
        llm: LLM 客户端（需具 chat_utility）；None → 降级空。
    """

    STAGES = ("material_split", "plot_learning", "style_learning")

    def __init__(self, project_dir: Path, llm: Any | None = None) -> None:
        self.project_dir = Path(project_dir)
        self.llm = llm
        self.store = TechniqueStore(self.project_dir)

    # ---------------- 三阶段推断 ----------------
    def _run_stage(self, prompt_file: str, user: str, max_tokens: int = 1200,
                   label: str = "") -> dict[str, Any]:
        """运行单个阶段；资源缺失 / LLM 异常 / 解析失败 → 返回空 dict（降级）。"""
        if self.llm is None:
            return {}
        sp = _SKILL_DIR / prompt_file
        if not sp.exists():
            return {}
        try:
            system = sp.read_text(encoding="utf-8")
            resp = chat_utility(
                self.llm,
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_tokens=max_tokens,
                enable_thinking=False,
            )
            data = parse_llm_json(resp)
            return data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001 - 阶段失败降级为空
            return {}

    def _read_sample(self, sample: str) -> str:
        """读取样本：支持路径字符串或文章正文；失败返回空串。"""
        if not sample:
            return ""
        p = Path(sample)
        if p.exists() and p.is_file():
            try:
                return p.read_text(encoding="utf-8")
            except OSError:
                return ""
        return str(sample)

    # ---------------- 六槽位组装 ----------------
    @staticmethod
    def _fill_slots(stage_results: dict[str, Any], sample_count: int) -> TechniqueAsset | None:
        """把三阶段结果收敛为六槽位资产；样本数为 0 或全空 → None。"""
        if not stage_results or sample_count <= 0:
            return None
        slots: dict[str, str] = {}
        # 六槽位兜底空串，保证结构完整
        for s in SLOT_NAMES:
            slots[s] = ""
        # 依赖各阶段是否产生对应槽位（阶段缺失则槽位留空，由写手侧决定可用性）
        material = stage_results.get("material", {})
        plot = stage_results.get("plot", {})
        style = stage_results.get("style", {})

        # gimmick：优先样式 stage 的 gimmick；缺则从素材 common 技法里取
        gimmick = style.get("gimmick")
        if gimmick:
            slots["gimmick"] = str(gimmick)
        elif material.get("common"):
            slots["gimmick"] = str(material["common"][0].get("technique", ""))
        # plot_refine：素材 common 技法 / 剧情 refine 技能
        if plot.get("plot_refine_skill") and plot["plot_refine_skill"].get("apply_steps"):
            slots["plot_refine"] = json_str(plot["plot_refine_skill"])
        elif material.get("common"):
            slots["plot_refine"] = json_str(material["common"])
        # style_rules → pacing/intro/character 无定型映射时，整段兜底到 plot_refine 之外的 "pacing"
        if style.get("style_rules"):
            slots["pacing"] = json_str(style["style_rules"])
        if material.get("per_sample"):
            slots["draft_excerpt"] = json_str(material["per_sample"])

        nonempty = [v for v in slots.values() if v]
        if not nonempty:
            return None
        # 共性判定：由调用方传入 sample_count；≥2 才标共性
        is_common = sample_count >= 2
        cat = style.get("category") or "general"
        return TechniqueAsset(
            id="",
            title=str(material.get("title") or "三阶段学习仿写资产"),
            category=str(cat),
            is_common=is_common,
            occurrences=sample_count,
            slots=slots,
        )

    def learn(self, samples: list[str]) -> list[TechniqueAsset]:
        """对一批样本执行三阶段学习，产出写入预览区（未确认不入库）。

        Args:
            samples: 样本文本/路径列表。

        Returns:
            本次学习写入预览区的资产列表（可能为空）。
        """
        if self.llm is None or not samples:
            return []
        sample_texts = [self._read_sample(s) for s in samples]
        sample_count = len([t for t in sample_texts if t])

        material = self._run_stage("material_split.txt",
                                   "样本数:%d\n\n样本如下:\n%s" % (
                                       sample_count,
                                       "\n\n----\n\n".join(sample_texts)),
                                   label="material")
        plot = self._run_stage("plot_learning.txt",
                               "基于拆素材共性，产出剧情技能:\n%s" % (
                                   json_str(material.get("common") or [
                                       {"technique": m.get("t", ""), "times": 1,
                                        "is_common": False} for m in []])),
                               label="plot")
        style = self._run_stage("style_learning.txt",
                                "基于拆素材语感，产出文风规则:\n%s" % (
                                    json_str(material.get("per_sample") or [])),
                                label="style")
        # 降级：GPT 输出各异，做最宽松映射保证六槽位可消费
        asset = self._fill_slots(
            {"material": material, "plot": plot, "style": style},
            sample_count,
        )
        if asset is None:
            return []
        self.store.write_preview(asset)
        return [asset]

    # ---------------- 常用子命令 ----------------
    def confirm_preview(self, asset_id: str) -> TechniqueAsset | None:
        return self.store.confirm(asset_id)

    def list_preview(self) -> list[TechniqueAsset]:
        return self.store.list_preview()

    def clear_preview(self) -> int:
        return self.store.clear_preview()
