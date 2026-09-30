"""parse_llm_json 策略 6（字符串内裸引号修复）回归测试。

灵荒工坊 09-26 实证：评分模型在 rationale 里写 "垫片" 这类未转义英文双引号，
剥围栏后 JSON 仍非法，策略 1-5 全部失败 → 评分静默降级（quality_audit.jsonl
「无法解析为 JSON: ```json」共 5 次）。
"""

from agent.base.utils import parse_llm_json


def test_fenced_json_with_bare_quotes_in_rationale():
    # 09-26 02:44 readability 失败原文的还原（垫片周围是裸英文双引号）
    raw = '```json\n{"value": 83, "rationale": "本章为登记档位"垫片"（放松章）", "issues": []}\n```'
    data = parse_llm_json(raw)
    assert data["value"] == 83
    assert "垫片" in data["rationale"]


def test_bare_quotes_no_fence():
    raw = '{"value": 1, "rationale": "他说"好"之后走了", "issues": []}'
    data = parse_llm_json(raw)
    assert data["value"] == 1


def test_structural_quotes_not_touched():
    raw = '{"value": 5, "rationale": "正常无裸引号", "issues": []}'
    assert parse_llm_json(raw) == {"value": 5, "rationale": "正常无裸引号", "issues": []}


def test_escaped_quotes_still_work():
    raw = '{"value": 2, "rationale": "他说\\"好\\"之后", "issues": []}'
    data = parse_llm_json(raw)
    assert data["value"] == 2
    assert "好" in data["rationale"]
