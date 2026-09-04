"""G15 P0-4 · 三阶段学习仿写测试

覆盖：
- TechniqueStore：预览区 / 资产库分离（未确认不入库）、confirm / confirm_all、
  损坏降级为空、clear。
- LearningImitationMiner：llm=None 降级空、三阶段产出六槽位、共性≥2判定、
  未确认不入库（预览区附着）。
- 技能资源文件存在（skills/learning-imitation/*.{txt,tmpl.j2}）。
"""

from __future__ import annotations

import json
from pathlib import Path

from agent.core.story.technique_store import SLOT_NAMES, TechniqueAsset, TechniqueStore
from agent.workflows.evaluation.m17_learn import LearningImitationMiner
from agent.client import LLMResponse
from tests.conftest import make_project

import agent.workflows.evaluation.m17_learn as _m17


_IMITATION_JSON = {
    "material": {
        "title": "数据化绝境",
        "per_sample": [{"sample_id": "S1", "design": "d", "refine": "r"}],
        "common": [{"technique": "先用数据化绝境立住反差", "times": 2, "is_common": True}],
    },
    "plot": {
        "plot_refine_skill": {"name": "铺垫两拍再抖", "apply_steps": ["垫底", "加压"]},
    },
    "style": {"category": "hook", "gimmick": "数据化绝境"},
    "style_rules": [{"trigger": "开篇", "action": "数据化绝境开场"}],
}


class _FakeImitationLLM:
    def __init__(self, *args, **kwargs) -> None:
        self.calls = 0

    def chat(self, req, **kwargs):
        from types import SimpleNamespace
        self.calls += 1
        messages = req.messages if hasattr(req, 'messages') else req
        system = messages[0]["content"] if messages else ""
        # 按系统提示对应的阶段返回各自 schema
        if "拆素材专家" in system:
            payload = _IMITATION_JSON["material"]
        elif "剧情学习专家" in system:
            payload = _IMITATION_JSON["plot"]
        else:  # 文风学习专家
            payload = {"gimmick": "数据化绝境", "category": "hook",
                       "style_rules": _IMITATION_JSON["style_rules"]}
        return SimpleNamespace(text=json.dumps(payload, ensure_ascii=False))


# ============================================================
# TechniqueStore
# ============================================================
class TestTechniqueStore:
    def test_preview_not_in_library_until_confirm(self, tmp_path: Path) -> None:
        store = TechniqueStore(tmp_path)
        asset = TechniqueAsset(id="", title="T", category="hook",
                               slots={"gimmick": "g"}, is_common=True, occurrences=2)
        store.write_preview(asset)
        # 未确认：预览区有，资产库无
        assert len(store.list_preview()) == 1
        assert store.load_library() == []

        confirmed = store.confirm(asset.id)
        assert confirmed is not None
        assert store.load_library() == [confirmed]
        assert store.list_preview() == []  # 已从预览区移除

    def test_confirm_all(self, tmp_path: Path) -> None:
        store = TechniqueStore(tmp_path)
        for i in range(2):
            store.write_preview(TechniqueAsset(
                id=f"a{i}", category="pacing",
                slots={"pacing": f"p{i}"}, occurrences=1))
        confirmed = store.confirm_all()
        assert len(confirmed) == 2
        assert len(store.load_library()) == 2
        assert store.list_preview() == []

    def test_corrupt_library_degrades_empty(self, tmp_path: Path) -> None:
        f = tmp_path / ".state" / "learning" / "library.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("{broken", encoding="utf-8")
        assert TechniqueStore(tmp_path).load_library() == []


# ============================================================
# LearningImitationMiner
# ============================================================
class TestLearningImitationMiner:
    def test_llm_none_degrades_empty(self, tmp_path: Path) -> None:
        d = make_project(tmp_path, n_chapters=1)
        miner = LearningImitationMiner(d, llm=None)
        assert miner.learn(["样本一"]) == []

    def test_three_stage_six_slots(self, tmp_path: Path) -> None:
        d = make_project(tmp_path, n_chapters=1)
        miner = LearningImitationMiner(d, llm=_FakeImitationLLM())
        items = miner.learn(["样本一", "样本二"])
        assert len(items) == 1
        a = items[0]
        # 六槽位结构完整
        assert all(s in a.slots for s in SLOT_NAMES)
        # gimmick 落槽位
        assert a.slots["gimmick"] == "数据化绝境"
        # 共性判定：2 篇样本 → common
        assert a.is_common is True
        assert a.occurrences == 2
        # 未确认：资产库为空，仍在预览区
        assert miner.store.load_library() == []
        assert len(miner.store.list_preview()) == 1

    def test_confirm_preview_enters_library(self, tmp_path: Path) -> None:
        d = make_project(tmp_path, n_chapters=1)
        miner = LearningImitationMiner(d, llm=_FakeImitationLLM())
        items = miner.learn(["样本一", "样本二"])
        confirmed = miner.confirm_preview(items[0].id)
        assert confirmed is not None
        assert len(miner.store.load_library()) == 1

    def test_single_sample_is_variant(self, tmp_path: Path) -> None:
        d = make_project(tmp_path, n_chapters=1)
        miner = LearningImitationMiner(d, llm=_FakeImitationLLM())
        items = miner.learn(["样本一"])
        assert items and items[0].is_common is False
        assert items[0].occurrences == 1


# ============================================================
# 技能资源文件
# ============================================================
def test_skill_resources_exist() -> None:
    skill_dir = Path(_m17.__file__).resolve().parents[2] / "skills" / "learning-imitation"
    required = {
        "SKILL.md", "material_split.txt", "plot_learning.txt",
        "style_learning.txt", "six_slots.tmpl.j2", "quality_rules.tmpl.j2",
    }
    present = {f.name for f in skill_dir.iterdir() if f.is_file()}
    assert required.issubset(present), required - present


def test_json_str_helper() -> None:
    assert '{"a": 1}' == _m17.json_str({"a": 1})