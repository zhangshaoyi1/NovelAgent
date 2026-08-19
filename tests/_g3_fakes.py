"""G3 测试共享桩：离线 fake LLM / stub writer·editor·memory / planner 桩。

所有桩均不触发真实 LLM 网络调用；``_FakeLLM`` 返回可被 M1~M4 解析的通用 JSON，
使自主规划链路完整跑通（成功路径）。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from agent.agents.planner_agent import MasterPlan
from agent.core.llm_client import LLMResponse


# M1~M4 全部期望字段都包含的通用 JSON（一份 dict 通吃所有 workflow 的解析）。
_GENERIC_PLAN: dict = {
    "synopsis": "（测试简介）",
    "worldview": "（测试世界观）",
    "power_system": "（测试境界）",
    "factions": "（测试势力）",
    "golden_finger": "（测试金手指）",
    "story_core": "（测试内核）",
    "protagonist_triple": {"who": "林逸", "want": "变强", "obstacle": "强敌"},
    "main_plot": {"beginning": "b", "development": "d", "twist": "t", "resolution": "r"},
    "sublines_preview": "（支线预判）",
    "conflict_nodes": "（冲突节点）",
    "theme": "（主题）",
    "ending": "（结局）",
    "emotional_tone": "（情感基调）",
    "sublines": [
        {
            "subline_name": "主线",
            "goal": "推进主线",
            "characters": "",
            "conflicts": "",
            "constraints": "",
            "mainline_relation": "",
            "pressure_curve": {
                "setup": "1-10", "conflict": "11-30",
                "climax": "31-50", "relief": "51-60",
            },
        }
    ],
    "protagonist_route": {
        "root_node": "起点",
        "nodes": [{"id": "n1", "chapter_range": "1-10", "milestone": "觉醒"}],
    },
    "characters": [
        {
            "name": "林逸",
            "role": "protagonist",
            "identity": "废柴少年",
            "core_motivation": "逆天改命",
            "arc": {"start": "弱", "end": "强"},
            "language_fingerprint": {
                "catchphrase": "我命由我", "sentence_style": "短促",
                "vocabulary": "热血", "banned_words": [],
            },
            "relations": "师父：老怪",
        }
    ],
    "relation_graph": {"nodes": [{"id": "林逸", "label": "林逸"}], "edges": []},
    "foreshadows": [
        {
            "id": "F-01", "content": "（伏笔）", "planted_at": "ch1",
            "expected_resolve": "ch10", "state": "未埋", "related_characters": "林逸",
        }
    ],
    "golden_finger_registration": {
        "name": "进度条", "type": "系统", "growth_stages": [],
        "cost_rules": "（成本）", "hard_limits": "（上限）", "unlock_conditions": "（解锁）",
    },
}


class _FakeLLM:
    """离线 fake LLM：返回通用 JSON；统计调用次数（幂等断言用）。"""

    def __init__(self) -> None:
        self.calls = 0

    def _resp(self, text: str) -> LLMResponse:
        return LLMResponse(
            text=text,
            usage={"prompt_tokens": 1, "completion_tokens": 1},
            model="fake",
        )

    def chat_creative(self, messages, **kwargs):
        self.calls += 1
        return self._resp(json.dumps(_GENERIC_PLAN, ensure_ascii=False))

    def chat_utility(self, messages, **kwargs):
        self.calls += 1
        return self._resp(json.dumps(_GENERIC_PLAN, ensure_ascii=False))

    def chat(self, messages, **kwargs):
        self.calls += 1
        return self._resp(json.dumps(_GENERIC_PLAN, ensure_ascii=False))

    def chat_structured(self, messages, schema=None, **kwargs):
        self.calls += 1
        return dict(_GENERIC_PLAN)


class _StubPlanner:
    """返回固定 Master Plan 的 planner 桩（不调用 LLM）。"""

    def __init__(self, plan: MasterPlan) -> None:
        self._plan = plan

    def load_plan(self):
        return None

    def run(self, brief: str) -> MasterPlan:
        return self._plan


def _make_plan() -> MasterPlan:
    return MasterPlan(
        brief="贫寒高二生觉醒二次元进度条系统，逆袭人生",
        title="进度条人生",
        genre="modern",
        total_chapters=12,
    )


class _StubWriter:
    """写章桩：返回固定章节但不推进状态机进度（使写章循环 1 轮后安全退出）。"""

    def run(self, *args, **kwargs):
        return SimpleNamespace(chapter_num=1, chapter_text="x", chapter_title="y")


class _StubEditor:
    """编辑桩：直接通过。"""

    def review(self, text):
        return SimpleNamespace(passed=True, block_count=0, frozen_violations=[])


class _StubMemory:
    """记忆桩：空操作。"""

    def record_chapter(self, num, title, facts=None):
        return None
