"""共享测试夹具（集中管理，避免各测试文件重复定义与互相 import）

提供：
- ARCH_JSON / CHAPTER_TEXT / QUALITY_PASS / QUALITY_FAIL 样例数据
- _build_mock_llm / _build_minimal_project 构造辅助
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import frontmatter
import pytest

from agent.client import LLMResponse
from agent.core.engine.state_machine import State, StateMachine
from agent.workflows.writing.m5_write_chapter import M5WriteChapterWorkflow

# 假数据
# ============================================================
ARCH_JSON = {
    "story_core": "凡人以痛感证道。",
    "protagonist_triple": {
        "who": "林寻",
        "want": "复仇",
        "obstacle": "宗门垄断",
    },
    "main_plot": {
        "beginning": "觉醒",
        "development": "逃亡",
        "twist": "真相",
        "resolution": "证道",
    },
    "theme": "效率vs人性",
    "ending": "殉道",
    "emotional_tone": "悲壮",
    "synopsis": "林寻唤醒太虚镜。",
}

CHAPTER_TEXT = """寒风割过绝灵崖的枯木，林寻伏在血泊里，丹田破碎。

「计算完毕，存活率 0.13%。建议放弃抵抗。」

太虚镜的声音冰冷如机械，没有一丝波动。林寻咳出一口血，指尖抓住镜缘——他不想死，不是因为怕，而是师父的仇还没报。

「我选笨路。」

他咬破舌尖，精血渗入镜面。推演启动的刹那，剧痛撕裂神识，他看见了——入门功法里那道隐秘的奴役暗门，如同一根钉子嵌在气脉深处。

原来如此。宗门从未打算让寒门弟子真正修成。

镜面映出他扭曲的脸，和一串跳动的数据。远处，执法堂的铃声响了。"""

QUALITY_PASS = {
    "overall_pass": True,
    "rules": [
        {"rule": "open_hook", "pass": True, "issue": ""},
        {"rule": "emotion_anchor", "pass": True, "issue": ""},
    ],
    "banned_word_count": {"突然": 0, "忽然": 0, "就在这时": 0, "微微一笑": 0},
    "suggestions": "",
}

QUALITY_FAIL = {
    "overall_pass": False,
    "rules": [
        {"rule": "open_hook", "pass": True, "issue": ""},
        {"rule": "emotion_anchor", "pass": False, "issue": "缺少明确情绪锚点"},
    ],
    "banned_word_count": {"突然": 0, "忽然": 0, "就在这时": 0, "微微一笑": 0},
    "suggestions": "增加一个爽/虐/燃锚点",
}


# ============================================================
# 夹具
# ============================================================
def _build_mock_llm(
    chapter_text: str = CHAPTER_TEXT,
    quality_report: dict | None = None,
    revised_text: str | None = None,
) -> MagicMock:
    """构建 mock Gateway，支持多轮调用"""
    from unittest.mock import MagicMock as _MagicMock
    from types import SimpleNamespace

    llm = _MagicMock()
    if quality_report is None:
        quality_report = QUALITY_PASS

    import json as _json

    # chat_creative → 生成正文；chat_utility → 质量校验
    # 如果有修订，第二次 chat_creative 返回修订后的文本
    creative_responses = iter([chapter_text] + ([revised_text] if revised_text else []))
    utility_responses = iter([_json.dumps(quality_report, ensure_ascii=False)])

    def chat_side_effect(req, **kwargs):
        """mock Gateway.chat() → 返回含 .text 的对象"""
        hint = getattr(req, 'hint', None)
        is_creative = hint is None or getattr(hint, 'complexity', None) != 'simple'
        try:
            if is_creative:
                text = next(creative_responses)
            else:
                text = next(utility_responses)
        except StopIteration:
            text = chapter_text if is_creative else _json.dumps(QUALITY_PASS, ensure_ascii=False)
        return SimpleNamespace(text=text)

    llm.chat.side_effect = chat_side_effect
    return llm


def _build_minimal_project(tmp_path: Path, state: State = State.CHARACTER_DESIGN) -> Path:
    """搭建最小可用项目（含 world/architecture/outline/characters/sublines/route/foreshadows）"""
    d = tmp_path / "p"
    d.mkdir(parents=True)

    # world.md
    world = """---
title: "太虚镜"
scope: long
genre: xiuxian
style:
  tone: 热血
  pov: 第三人称限制
  rhythm: 快
  chapter_length: 3000
  info_density: 中
  banned_elements: []
frozen_fields:
  - realm_system
---

# 总设定集

## 故事简介

灵根残缺少年林寻唤醒太虚镜，与冷酷器灵博弈。

## 境界体系（冻结）

炼气→筑基→金丹→元婴→化神

## 金手指登记

名称：太虚镜
代价：精血寿元
"""
    (d / "world.md").write_text(world, encoding="utf-8")

    # architecture.md（confirmed）
    arch_post = frontmatter.Post(
        "# 故事架构\n",
        title="太虚镜",
        confirmed=True,
        confirmed_at="2026-01-01",
        version=1,
        architecture=ARCH_JSON,
    )
    (d / "architecture.md").write_bytes(frontmatter.dumps(arch_post).encode("utf-8"))

    # outline.md
    outline_post = frontmatter.Post(
        "# 大纲\n## 故事简介\n林寻唤醒太虚镜。\n",
        title="太虚镜",
        sublines=[
            {"subline_name": "器灵人性觉醒", "goal": "器灵觉醒"},
        ],
    )
    (d / "outline.md").write_bytes(frontmatter.dumps(outline_post).encode("utf-8"))

    # protagonist_route.md
    route = """# 主角成长路线 · 起点

## N01 · 太虚镜初启

- **章节范围**：1-15

### 主分支 · 逃亡推演

- **结果**：逃入凡城
- **成长**：炼气一层

## N02 · 组建互助盟

- **章节范围**：16-45

### 主分支 · 布道凡城

- **结果**：公开推演
- **成长**：筑基初期
"""
    (d / "protagonist_route.md").write_text(route, encoding="utf-8")

    # subline
    sub_dir = d / "sublines" / "S01_器灵人性觉醒"
    sub_dir.mkdir(parents=True)
    subline = """---
subline_id: "S01_器灵人性觉醒"
subline_name: "器灵人性觉醒"
status: "planned"
characters: ["林寻", "太虚镜"]
---

# 支线设定

## 支线目标

太虚镜从工具进化为独立生命体

## 出场角色

林寻, 太虚镜

## 剧集压力曲线

| 阶段 | 章节 | 张力等级 |
|---|---|---|
| 铺垫 | 1-50 | 低 |
| 冲突 | 51-200 | 中 |
| 高潮 | 450-500 | 高 |
"""
    (sub_dir / "subline.md").write_text(subline, encoding="utf-8")

    # characters
    chars_dir = d / "characters"
    chars_dir.mkdir()
    char = """---
name: "林寻"
role: "protagonist"
faction: "寒门"
realm: "炼气"
---

# 角色档案

## 内核

- **核心动机**：让人人可修仙
- **表层目标**：复仇
- **深层目标**：证道

## 语言指纹

- **口头禅**：我选笨路。
- **句式偏好**：短句斩钉截铁
- **用词习惯**：反问
- **禁用词**：["突然"]
"""
    (chars_dir / "林寻.md").write_text(char, encoding="utf-8")

    # relations
    rel_dir = d / "relations"
    rel_dir.mkdir()
    (rel_dir / "graph.md").write_text("# 关系网\n\nA→B 对立", encoding="utf-8")

    # foreshadows.md
    (d / "foreshadows.md").write_text(
        "# 伏笔登记表\n\n| ID | 伏笔内容 | 埋设位置 | 预期回收点 | 状态 | 关联角色 |\n|---|---|---|---|---|---|\n| F-01 | 镜面乱码 | ch003 | ch095 | 未埋 | 林寻 |\n",
        encoding="utf-8",
    )

    # state
    sm = StateMachine(d)
    sm.load()
    sm.state = state
    sm.mode = "auto"  # M8: 测试用 auto 模式，避免触发交互询问
    sm.save()

    return d


# ============================================================
# 增量 A/C/D/E/F 共享夹具
# ============================================================
def sample_chapter_text(chapter_num: int = 1) -> str:
    """返回一段多段落样例章节文本（RAG / pacing / learn 测试用）

    不同章节号返回略有不同的内容，便于验证语义召回的区分度。
    """
    themes = ["逃亡", "推演", "觉醒", "对峙", "反杀", "隐忍"]
    theme = themes[(chapter_num - 1) % len(themes)]
    return (
        f"第{chapter_num}章的夜色里，林寻借着太虚镜的微光潜行。"
        f"{theme}是他此刻唯一的选择。\n\n"
        f"镜中数据流闪烁，推演出三条生路，却都被执法堂的巡逻封锁。"
        f"林寻咬破舌尖，精血沁入镜面。\n\n"
        f"「计算完毕，存活率仅 0.13%。」太虚镜的声音依旧冰冷。"
        f"但林寻已习惯了这种绝境。\n\n"
        f"他选择笨路——以伤换机，于{theme}中撕开一道口子。"
        f"远处铃声骤起，追兵已至。\n\n"
        f"这一章的转折在于：看似被动的{theme}，实则是林寻主动布下的饵。"
        f"镜光映出他唇角一丝几不可察的弧度。"
    )


def make_project(
    tmp_path: Path,
    n_chapters: int = 3,
    state: State = State.WRITING,
) -> Path:
    """搭建含 N 章已写章节的样例项目（RAG / pacing / learn 测试用）

    在 ``_build_minimal_project`` 基础上直接写入 ``ch001..chNNN.md``
    （不依赖真实 LLM，使用 ``sample_chapter_text`` 生成区分度内容），
    并把状态置为指定值（默认 WRITING）。
    """
    d = _build_minimal_project(tmp_path, state=State.CHARACTER_DESIGN)
    chapters_dir = d / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)
    for n in range(1, n_chapters + 1):
        text = sample_chapter_text(n)
        post = frontmatter.Post(
            f"# 第 {n} 章 · 样例\n\n{text}",
            chapter=n,
            subline="S01_器灵人性觉醒",
            route_node="N01",
            pressure_stage="铺垫",
            title=f"第{n}章样例",
            word_count=len(text),
            quality_passed=True,
            revision_attempts=0,
            evidence_chain={"characters": [], "foreshadows": [], "settings": []},
        )
        (chapters_dir / f"ch{n:03d}.md").write_bytes(
            frontmatter.dumps(post).encode("utf-8")
        )
    sm = StateMachine(d)
    sm.load()
    sm.state = state
    sm.progress = {
        "current_subline": "S01_器灵人性觉醒",
        "current_chapter": n_chapters,
        "total_written": n_chapters,
        "last_written_at": "2026-01-01 00:00:00",
    }
    sm.save()
    return d


class FakeEmbedder:
    """可注入的假 EmbeddingProvider（绕开真实网络，供 RAG 单元测试）

    基于字符 bigram 哈希生成确定性向量：相同文本得相同向量、相似文本得相近向量，
    足以驱动向量召回与 BM25 融合的单元测试。
    """

    def __init__(self, dim: int = 16) -> None:
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def _vec(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for i in range(len(text) - 1):
            ngram = text[i : i + 2]
            h = (hash(ngram) % self.dim + self.dim) % self.dim
            vec[h] += 1.0
        norm = sum(v * v for v in vec) ** 0.5
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


@pytest.fixture
def fake_embedder() -> FakeEmbedder:
    """返回可注入的假嵌入器（RAG 测试用，避免真实网络调用）"""
    return FakeEmbedder()
