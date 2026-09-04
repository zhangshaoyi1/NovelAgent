"""体量（篇幅）档位与自定义体量的统一事实源。

用法（M1 世界观、M3 大纲、前端、CLI 都读这里，避免各层各写一套档位逻辑）：
- SCOPE_LABELS：给前端下拉 / 展示的档位中文标签
- describe_scope()：把体量规格化为给 LLM 的一小段中文描述
    （含目标总字数 / 单章字数 / 预计总章数），供 M1/M3 prompt 注入
- estimate_chapters()：按总字数 + 单章字数估算总章数
- 自定义体量：scope='custom'，需要同时给出 total_words 与 chapter_length，
    chapter_length 被严格约束在 [MIN_CHAPTER_LENGTH, MAX_CHAPTER_LENGTH]，
    推荐区间见 RECOMMENDED_CHAPTER_LENGTH（2000-2500）。

约定：所有返回值都是 str/int/float，不引入任何框架依赖，保持零外部依赖（base 层原则）。
"""

from __future__ import annotations

# ---------------------------------------------------------------
# 档位常量
# ---------------------------------------------------------------

# 内置档位（不含 custom，custom 单独处理）
SCOPE_LABELS = {
    "short": "短篇（< 5万字）",
    "medium": "中篇（5-30万字）",
    "long": "长篇（30万字+）",
    "mega": "百万字（100万字以上）",
    "custom": "自定义（输入总字数 + 单章字数）",
}

# 单章字数约束（对应交互 / 校验提示）
MIN_CHAPTER_LENGTH = 1500
MAX_CHAPTER_LENGTH = 5000
# 推荐单章字数区间（推荐 2000-2500）
RECOMMENDED_CHAPTER_LENGTH = (2000, 2500)

# 各内置档位的目标总字数区间（万字），用于 describe_scope 的默认估算
SCOPE_WORD_RANGE = {
    "short": (1, 5),
    "medium": (5, 30),
    "long": (30, 80),
    "mega": (100, 300),
}

# 各内置档位兜底单章字数（未指定时；custom 必须显式指定）
SCOPE_DEFAULT_CHAPTER_LENGTH = {
    "short": 2000,
    "medium": 3000,
    "long": 3000,
    "mega": 3000,
}

# 合法档位键（供 CLI choices / 前端校验）
VALID_SCOPES = ("short", "medium", "long", "mega", "custom")


class ScopeValidationError(ValueError):
    """自定义体量参数非法时的异常（携带可读的错误信息）。"""


def _clamp_int(value: int | float | None, default: int) -> int:
    """安全转 int；None / 非法回退 default（>=1）。"""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, n)


def describe_scope(
    scope: str,
    total_words: int | None = None,
    chapter_length: int | None = None,
) -> str:
    """把体量规格化为给 LLM 的中文描述（含目标总字数、单章字数、预计总章数）。

    Args:
        scope: 档位键（short / medium / long / mega / custom）
        total_words: 目标总字数（字）；custom 必需，其余可省略按默认区间取上限
        chapter_length: 单章字数（字）；custom 必需

    Returns:
        一段中文描述，如
        「百万字体量：目标总字数约 150 万字，单章约 3000 字，预计约 500 章。」
    """
    if scope == "custom":
        cl = _clamp_int(chapter_length, RECOMMENDED_CHAPTER_LENGTH[0])
        tw = _clamp_int(total_words, 10000)
        chapters = estimate_chapters("custom", tw, cl)
        return (
            f"自定义体量：目标总字数约 {tw} 字，单章约 {cl} 字，"
            f"预计约 {chapters} 章。"
        )

    label = SCOPE_LABELS.get(scope, SCOPE_LABELS["medium"])
    lo, hi = SCOPE_WORD_RANGE.get(scope, SCOPE_WORD_RANGE["medium"])
    cl = _clamp_int(chapter_length, SCOPE_DEFAULT_CHAPTER_LENGTH.get(scope, 3000))
    # 目标总字数取区间上限（给足供给，避免 LLM 因目标过小刻意压缩章数）
    tw = hi * 10_000
    chapters = estimate_chapters(scope, tw, cl)
    return (
        f"{label}：目标总字数约 {hi} 万字，单章约 {cl} 字，"
        f"预计约 {chapters} 章。"
    )


def estimate_chapters(
    scope: str,
    total_words: int | None = None,
    chapter_length: int | None = None,
) -> int:
    """估算总章数 = round(总字数 / 单章字数)，下限 1。

    custom 用传入的 total_words 与 chapter_length；内置档位用默认区间上限。
    """
    if scope == "custom":
        cl = _clamp_int(chapter_length, RECOMMENDED_CHAPTER_LENGTH[0])
        tw = _clamp_int(total_words, 10000)
        return max(1, round(tw / max(1, cl)))
    _lo, hi = SCOPE_WORD_RANGE.get(scope, SCOPE_WORD_RANGE["medium"])
    cl = _clamp_int(chapter_length, SCOPE_DEFAULT_CHAPTER_LENGTH.get(scope, 3000))
    return max(1, round(hi * 10_000 / max(1, cl)))


def validate_custom(
    total_words: int | None,
    chapter_length: int | None,
) -> str | None:
    """校验自定义体量参数。合法返回 None；非法返回可读错误信息。

    目前只校验单章字数区间；总字数需为正整数。
    """
    tw = _clamp_int(total_words, 0)
    if tw <= 0:
        return "自定义体量必须填写目标总字数（正整数）。"
    cl = _clamp_int(chapter_length, 0)
    if cl < MIN_CHAPTER_LENGTH or cl > MAX_CHAPTER_LENGTH:
        return (
            f"单章字数需在 {MIN_CHAPTER_LENGTH}-{MAX_CHAPTER_LENGTH} 字之间"
            f"（推荐 {RECOMMENDED_CHAPTER_LENGTH[0]}-{RECOMMENDED_CHAPTER_LENGTH[1]} 字）。"
        )
    return None