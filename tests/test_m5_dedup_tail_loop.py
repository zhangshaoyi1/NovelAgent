"""P-DEDUP-2 尾部循环段落去重 单元测试（无网络 / 无真实项目依赖）。

覆盖 agent.workflows.m5_write_chapter.M5WriteChapterWorkflow._dedup_tail_loop：
- 章节结尾把前文连续段落整段复读（无标题锚点）→ 应截断复读尾部；
- 无循环复读的正常正文 → 应原样返回；
- ch238 实证结构：战斗段落在结尾被整段复述。
"""

from __future__ import annotations

from agent.workflows.writing.m5_write_chapter import M5WriteChapterWorkflow as W


DEDUP = W._dedup_tail_loop

_A1 = "夜风从谷底卷上来，吹得木屋门扉晃动。无名迈步跨过门槛。"
_A2 = "林婉仍躺在榻上，呼吸平稳。那枚黑色石子枕在她头侧。"
_A3 = "无名站在门口，目光扫过林婉苍白的面容，手指在袖中收紧。"
_A4 = "山谷里火把光亮开始出现，一条火龙正缓缓向木屋游走过来。"
_A5 = "无名眯起眼睛，看清打头阵的人，那是一个穿红袍提长刀的修士。"
_A6 = "血屠也看见了他，停下脚步，举起长刀指向无名。"


def _join(parts):
    return "\n\n".join(p.strip() for p in parts if p.strip())


def test_no_loop_returns_unchanged():
    body = _join([_A1, _A2, _A3, _A4])
    assert DEDUP(body) == body


def test_tail_repeats_prefix_dedup():
    """结尾把开头整段战斗复读 → 应截断到复读起点之前。"""
    body = _join([_A1, _A2, _A3, _A4, _A5, _A6, _A4, _A5, _A6])
    out = DEDUP(body)
    # 复读的 [火把, 无名眯眼, 血屠] 三段应被删除
    assert out == _join([_A1, _A2, _A3, _A4, _A5, _A6])
    assert out.count("火把") == 1
    assert "血屠也看见了他" in out


def test_tail_short_repeat_ignored():
    """单独一段偶合相似不构成循环；且复读块过短（< min_run）不触发。"""
    body = _join([_A1, _A2, _A4])  # 仅 3 段，无法构成 源+≥2 段循环
    assert DEDUP(body) == body


def test_ch238_style_battle_loop_cut():
    """ch238 实证：正文前部正常推进，结尾整段复述前面的战斗场景。"""
    normal = [_A1, _A2, _A3, _A4, _A5, _A6]
    loop = [_A4, _A5, _A6]  # 盖章复读的战斗段
    body = _join(normal + loop)
    out = DEDUP(body)
    blocks = [b for b in out.split("\n\n") if b.strip()]
    # 复读的三段被去掉，正文回到正常推进的 6 段
    assert len(blocks) == 6
    # 各段只出现一次（复读未虚增）
    assert blocks.count(_A4) == 1
    assert blocks.count(_A5) == 1
    assert blocks.count(_A6) == 1
    # 结尾回到复读前的最后一段（正常剧情收束）
    assert blocks[-1] == _A6