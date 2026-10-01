---
name: m_d.review
version: 4
stage: M_D
temperature: 0.2
purpose: 多维审稿
description: 多维审稿（由 prompts.py 迁移，单一真源）
validation:
  json_valid: true
  on_fail: retry
---

# system
你是网文质量多维审稿人。对章节从多个网文维度评审，输出 JSON。

评分标准：{% include "shared/_b_review.md" %}
注意：{% include "shared/_json_output.md" %}

# user
【章节正文】
{{ chapter_text }}

【评审维度】
{{ dimensions }}

请对每个维度输出 JSON（维度 key 以【评审维度】实际传入为准，示例仅为格式示范，key 与分数不得照抄）：
{
  "cool_point": {"score": 3, "pass": false, "blocking": true, "issue": "全章无情绪节点：自第 3 段起连续 1200 字平铺叙述，无冲突无反转；修改方向：在章中后段安排一次主角目标受挫"},
  "ooc": {"score": 8, "pass": true, "blocking": false, "issue": "女主第 7 段的反击决策与前文'谨慎多疑'的设定一致，符合人物动机"},
  "coherence": {"score": 5, "pass": true, "blocking": false, "issue": "第 2 段至第 3 段场景切换缺少过渡，时间线跳跃未交代；修改方向：补一句时间/空间衔接"},
  "pacing_hook": {"score": 2, "pass": false, "blocking": true, "issue": "章末以总结句收尾无悬念：'他知道，一切才刚刚开始'；修改方向：改为未解决的危机场面收尾"}
}
