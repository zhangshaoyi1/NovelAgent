"""Phase 5 · 规模化与天花板攻坚（起步）

本轮先落地「影响上限与规模化」5 项中的第一项：**真实向量语义记忆**（其余 4 项
异步并发 / 上下文工程 / 多 Agent 协作 / Guardrails 硬门禁 已在任务列表排期）。

测试覆盖 SemanticMemory 的向量检索与优雅降级：
- 注入 embed_fn 后优先向量余弦检索，命中语义最近条目；
- embed_fn 返回空 / 抛异常 → 自动回退离线 char-bigram（不阻断）；
- 向量随条目持久化，重载后可用；
- MemoryLayer 透传 embed_fn。

全部零网络（用内存假 embed_fn），与既有 73 个测试无冲突。
"""

from __future__ import annotations

from agent.memory.layer import MemoryLayer
from agent.memory.semantic import SemanticMemory, build_default_embed_fn


# 确定性假向量：把文本映射到固定 3 维向量（按文本查表），便于断言检索排序。
_TABLE = {
    "alpha": [1.0, 0.0, 0.0],
    "beta": [0.0, 1.0, 0.0],
    "gamma": [0.0, 0.0, 1.0],
    "query_a": [0.9, 0.1, 0.0],
}


def _fake_embed(texts):
    return [_TABLE[t] for t in texts]


def test_vector_retrieval_picks_nearest():
    mem = SemanticMemory(project_dir=None, embed_fn=_fake_embed)
    mem.add("alpha")
    mem.add("beta")
    mem.add("gamma")
    assert mem.vector_enabled is True

    res = mem.retrieve("query_a", top_k=3)
    assert res, "向量检索应返回结果"
    # query_a 与 alpha 余弦最高（0.9/|qa| ≈ 0.994），应排第一
    assert res[0][0].text == "alpha"
    assert res[0][1] > 0.9


def test_embed_empty_falls_back_to_bigram():
    def empty_embed(texts):
        return []

    mem = SemanticMemory(project_dir=None, embed_fn=empty_embed)
    mem.add("林轩拔出长剑，剑光如雪。")
    mem.add("苏沐橙施展魔法，星光点点。")
    assert mem.vector_enabled is False  # 无向量 → 回退
    res = mem.retrieve("长剑", top_k=2)
    assert res, "回退到 bigram 仍应返回结果"
    assert res[0][0].text.startswith("林轩")


def test_embed_exception_falls_back_to_bigram():
    def boom(texts):
        raise RuntimeError("embedding 服务不可达")

    mem = SemanticMemory(project_dir=None, embed_fn=boom)
    mem.add("林轩拔出长剑。")
    mem.add("苏沐橙施展魔法。")
    assert mem.vector_enabled is False
    res = mem.retrieve("魔法", top_k=2)
    assert res and res[0][0].text.startswith("苏沐橙")


def test_vector_persists_and_reloads(tmp_path):
    p = tmp_path / "novel"
    mem1 = SemanticMemory(p, embed_fn=_fake_embed)
    mem1.add("alpha")
    mem1.add("beta")
    mem1.add("gamma")

    # 重载（用同一 embed_fn）：应从落盘 meta 重建向量
    mem2 = SemanticMemory(p, embed_fn=_fake_embed)
    assert mem2.vector_enabled is True
    res = mem2.retrieve("query_a", top_k=1)
    assert res and res[0][0].text == "alpha"


def test_memory_layer_passes_embed_fn():
    layer = MemoryLayer(project_dir=None, embed_fn=_fake_embed)
    # embed_fn 已透传（向量模式在写入首条后激活）
    assert layer.semantic.embed_fn is not None
    layer.remember("alpha", type="fact")
    layer.remember("beta", type="fact")
    assert layer.semantic.vector_enabled is True
    res = layer.recall("query_a", top_k=1)
    assert res and res[0][0].text == "alpha"


def test_build_default_embed_fn_is_callable():
    # 不应在构造时触发网络；仅在调用时懒加载 embeddings 模块
    fn = build_default_embed_fn("openai")
    assert callable(fn)
    # 不实际调用（需真实 endpoint），仅确认工厂可用
