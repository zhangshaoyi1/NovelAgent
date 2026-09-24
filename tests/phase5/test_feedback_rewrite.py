"""A3 反馈→定向改写 —— 离线测试

用 conftest.make_project 搭建含章节的项目，注入假 LLM（MagicMock spec=LLMClient），
覆盖：成功重写 / 上下文锚点透传 / BLOCK 门禁拦截 / advisory 放行带告警 /
LLM 失败优雅降级 / 缺章报错 / 偏好沉淀 / AgentService 接线。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import frontmatter
import pytest

from agent.core.quality.rewrite.feedback_rewriter import FeedbackRewriter, RewriteResult
from agent.core.quality.guardrails import Guardrails
from agent.service.agent_service import AgentService


# ============================================================
# 假 LLM
# ============================================================
def _fake_llm(rewritten_text: str, *, raise_error: bool = False) -> MagicMock:
    llm = MagicMock()
    if raise_error:
        llm.chat.side_effect = RuntimeError("network down")
    else:
        llm.chat.return_value = SimpleNamespace(text=rewritten_text)
    return llm


def _seq_llm(*texts: str) -> MagicMock:
    """按调用顺序依次返回正文的假 LLM（多轮：整章重写 → 整章扩写…）。

    调用次数超出给定文本数时抛 ``StopIteration``——被 ``_ensure_full_length``
    按「扩写轮调用失败」降级捕获，正好用于验证"轮数用尽则保留当前稿"。
    """
    llm = MagicMock()
    llm.chat.side_effect = [SimpleNamespace(text=t) for t in texts]
    return llm


def _cjk(text: str) -> int:
    from agent.core.quality.scoring.quality_checker import _count_cjk

    return _count_cjk(text)


def _long_body(min_cjk: int = 2600) -> str:
    """构造一篇 ≥ min_cjk 中文字的正文（段落互不相同、无残留标记，纯测试用）。"""
    templates = (
        "林寻把第{i}块矿石翻过来，就着灯看断口，第{i}道纹理里有一道颜色发暗。",
        "石莽在{i}丈外的巷口守着，听见檐上落下一滴水，握刀的手紧了紧。",
        "账册翻到第{i}页，边角被虫蛀出个洞，他用指甲把洞沿的纸屑一点点刮掉。",
        "炉温升到第{i}档时药汁开始翻泡，苦气顺风飘出去，{i}步外的狗叫了两声。",
    )
    parts: list[str] = []
    i = 0
    while _cjk("\n\n".join(parts)) < min_cjk:
        i += 1
        parts.append(templates[i % len(templates)].format(i=i))
    return "\n\n".join(parts)


REWRITTEN = "（重写版）林寻咬破舌尖，精血沁入镜面，这一章被精准收紧了节奏。"


# ============================================================
# 测试
# ============================================================
def test_rewrite_success(tmp_path):
    d = __import__("tests.conftest", fromlist=["make_project"]).make_project(tmp_path, n_chapters=3)
    rewriter = FeedbackRewriter(
        d,
        llm_client=_fake_llm(REWRITTEN),
        guardrails=Guardrails(check_title=False, check_meta_leak=False),
    )
    res = rewriter.rewrite(2, "节奏太慢，删水")

    assert isinstance(res, RewriteResult)
    assert res.llm_used is True
    assert res.blocked is False
    assert res.guardrail_passed is True
    assert res.new_text == REWRITTEN
    assert res.new_word_count == len(REWRITTEN.replace("\n", "").replace(" ", ""))
    # 落盘
    ch_file = d / "chapters" / "ch002.md"
    assert REWRITTEN in ch_file.read_text(encoding="utf-8")
    # 备份
    assert res.backup_file is not None and res.backup_file.exists()
    # frontmatter 改写痕迹
    post = frontmatter.load(ch_file)
    assert post.metadata.get("revision_count") == 1
    assert post.metadata.get("last_rewrite_feedback") == "节奏太慢，删水"


def test_rewrite_context_anchors_passed(tmp_path):
    d = __import__("tests.conftest", fromlist=["make_project"]).make_project(tmp_path, n_chapters=3)
    llm = _fake_llm(REWRITTEN)
    rewriter = FeedbackRewriter(d, llm_client=llm)
    rewriter.rewrite(2, "主角太蠢，补动机")

    # chat 被调用，且 prompt 含上下文锚点（上一章尾 / 下一章头 / 题材）
    assert llm.chat.call_count == 1
    req = llm.chat.call_args[0][0]
    user = req.messages[1]["content"]
    assert "上一章" in user or "衔接上文" in user
    assert "衔接下文" in user
    assert "题材" in user


def test_rewrite_block_gate_rejects(tmp_path):
    d = __import__("tests.conftest", fromlist=["make_project"]).make_project(tmp_path, n_chapters=3)
    # 重写产物含默认合规词 {{ → BLOCK 应拦截
    bad = "{{leak}}" + REWRITTEN
    rewriter = FeedbackRewriter(d, llm_client=_fake_llm(bad))
    res = rewriter.rewrite(2, "测试", gate_mode="block")

    assert res.blocked is True
    assert res.llm_used is True
    assert res.guardrail_passed is False
    assert res.backup_file is None
    # 原章未被改写
    ch_file = d / "chapters" / "ch002.md"
    assert "{{leak}}" not in ch_file.read_text(encoding="utf-8")


def test_rewrite_advisory_allows_with_warning(tmp_path):
    d = __import__("tests.conftest", fromlist=["make_project"]).make_project(tmp_path, n_chapters=3)
    bad = "{{leak}}" + REWRITTEN
    rewriter = FeedbackRewriter(d, llm_client=_fake_llm(bad))
    res = rewriter.rewrite(2, "测试", gate_mode="advisory")

    assert res.blocked is False
    assert res.guardrail_passed is False  # advisory：带告警仍落盘
    ch_file = d / "chapters" / "ch002.md"
    # 2026-09-14：L2 落盘兜底把占位符/模板泄漏清除（{{leak}} 属可安全删除项），
    # 与写章路径 clean_hard_pollutions 同口径——告警仍透出，但垃圾不再落盘。
    assert "{{leak}}" not in ch_file.read_text(encoding="utf-8")


def test_rewrite_llm_failure_degrade(tmp_path):
    d = __import__("tests.conftest", fromlist=["make_project"]).make_project(tmp_path, n_chapters=3)
    ch_file = d / "chapters" / "ch002.md"
    original = frontmatter.load(ch_file).content.strip()
    rewriter = FeedbackRewriter(d, llm_client=_fake_llm("", raise_error=True))
    res = rewriter.rewrite(2, "反馈")

    assert res.llm_used is False
    assert res.error != ""
    assert res.new_text == original  # 回退原章
    # 原章未被改动
    post = frontmatter.load(ch_file)
    assert post.metadata.get("revision_count", 0) == 0


def test_rewrite_missing_chapter(tmp_path):
    d = __import__("tests.conftest", fromlist=["make_project"]).make_project(tmp_path, n_chapters=3)
    rewriter = FeedbackRewriter(d, llm_client=_fake_llm(REWRITTEN))
    with pytest.raises(FileNotFoundError):
        rewriter.rewrite(99, "反馈")


def test_rewrite_records_learning(tmp_path):
    d = __import__("tests.conftest", fromlist=["make_project"]).make_project(tmp_path, n_chapters=3)
    rewriter = FeedbackRewriter(d, llm_client=_fake_llm(REWRITTEN))
    rewriter.rewrite(2, "感情戏不够")

    from agent.core.story.learning_store import LearningStore

    items = LearningStore(d).load()
    fb = [x for x in items if x.category == "feedback_rewrite"]
    assert len(fb) == 1
    assert "感情戏不够" in fb[0].text


def test_service_rewrite_chapter_wires(tmp_path):
    d = __import__("tests.conftest", fromlist=["make_project"]).make_project(tmp_path, n_chapters=3)
    svc = AgentService(project_dir=d, console=__import__("rich.console", fromlist=["Console"]).Console())
    # 注入假 LLM 到 traced_llm（AgentService 用其构造 FeedbackRewriter）
    svc.traced_llm = _fake_llm(REWRITTEN)

    out = svc.rewrite_chapter(2, "节奏太慢")
    assert out["rewritten"] is True
    assert out["chapter"] == 2
    assert REWRITTEN in (d / "chapters" / "ch002.md").read_text(encoding="utf-8")


def test_rewrite_does_not_hijack_title_from_narrative_first_line(tmp_path):
    """首行是叙事句（含句读）时不得被当成标题——旧逻辑只判 len<=30（ch015 实证）。"""
    d = __import__("tests.conftest", fromlist=["make_project"]).make_project(tmp_path, n_chapters=3)
    sentence = "夜色沉沉，林寻把太虚镜按在胸口，镜面依然冰凉。"
    rewriter = FeedbackRewriter(
        d,
        llm_client=_fake_llm(sentence),
        guardrails=Guardrails(check_title=False, check_meta_leak=False),
    )
    rewriter.rewrite(2, "重写")

    post = frontmatter.load(d / "chapters" / "ch002.md")
    assert post.metadata["title"] == "第2章样例"  # 原标题保留
    assert post.content.startswith("# 第 2 章 · 第2章样例")


# ============================================================
# 指纹库同步（跨章去重在改写路径的生效性）
# ============================================================
#: 两个 ≥40 字的独立段落（`_check_dup` 只比对 ≥40 字长段落）
_PARA_OTHER = "推演完毕，存活率不足一成，他却笑了，因为他早已习惯在绝境里替自己留一条后路，以备不时之需。"
_PARA_ONE = "晨雾未散，他把药锄扛在肩上，顺着田垄一路走过去，脚下的泥还带着昨夜雨水的凉意与腥气。"


def _seed_prose(d: Path, per_chapter: dict[str, str]) -> None:
    """把段落写进对应**章文件**正文。

    跨章去重的真源是章文件，不是 ``.state/chapter_fingerprints.json``
    （2026-09-24：改写路径改用 ``load_book_fingerprints`` 按章文件重建指纹库，
    因为缓存只在「写章/改写/回滚」增量更新，带外改动会让它与成书脱节）。
    """
    for ch, para in per_chapter.items():
        f = d / "chapters" / f"ch{int(ch):03d}.md"
        post = frontmatter.load(f)
        post.content = f"# 第 {int(ch)} 章 · 样例\n\n{para}"
        f.write_bytes(frontmatter.dumps(post).encode("utf-8"))


def test_rewrite_injects_bookwide_fingerprints_for_dup_check(tmp_path):
    """改写产物的跨章段落重复必须被检出（此前 rewrite 用裸 Guardrails ⇒ 指纹库恒空）。"""
    d = __import__("tests.conftest", fromlist=["make_project"]).make_project(tmp_path, n_chapters=3)
    _seed_prose(d, {"3": _PARA_OTHER})

    # 改写产物抄了第 3 章的段落 ⇒ 应命中 paragraph_dup
    rewriter = FeedbackRewriter(
        d,
        llm_client=_fake_llm(_PARA_OTHER),
        guardrails=Guardrails(check_title=False, check_meta_leak=False),
    )
    res = rewriter.rewrite(2, "重写", gate_mode="advisory")

    assert res.guardrail_passed is False
    assert "paragraph_dup" in [v["rule_id"] for v in res.guardrail_report["violations"]]


def test_rewrite_refreshes_own_fingerprint_after_save(tmp_path):
    """落盘后本章指纹须刷新为新正文（否则后续写章按已不存在的旧文判重）。"""
    from agent.core.quality.guardrails import load_fingerprints

    d = __import__("tests.conftest", fromlist=["make_project"]).make_project(tmp_path, n_chapters=3)
    _seed_prose(d, {"1": _PARA_ONE, "3": _PARA_OTHER})
    fp_path = d / ".state" / "chapter_fingerprints.json"

    rewriter = FeedbackRewriter(
        d,
        llm_client=_fake_llm(_PARA_OTHER),
        guardrails=Guardrails(check_title=False, check_meta_leak=False),
    )
    rewriter.rewrite(2, "重写")

    _norm = Guardrails()._normalize_paragraph
    db = load_fingerprints(fp_path)
    assert [e[1] for e in db["2"] if isinstance(e, tuple)] == [_norm(_PARA_OTHER)]  # 已换成新正文
    assert [e[1] for e in db["1"] if isinstance(e, tuple)] == [_norm(_PARA_ONE)]  # 他章不受影响


# ============================================================
# 整章重写模式（--mode full）
# ============================================================
_SUBLINE_MD = """## 章节钩子设计

第1章：以"纸上的五行"收束，暗示矿脉异常
第2章：章末抛出"五行吞噬诀"的反噬隐患

## 章节强度档位

第1章：推进
第2章：推进

## 情节点序列

第2章：测试不同属性催化的吸收率 → 发现土系与木系最佳 → 埋下傀儡线
"""


def _write_subline(d: Path, subline_id: str = "S01_器灵人性觉醒") -> None:
    p = d / "sublines" / subline_id / "subline.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_SUBLINE_MD, encoding="utf-8")


def test_full_mode_uses_full_prompt_and_injects_contract(tmp_path):
    """full 模式：走 quality.rewrite_full，注入本章逐章契约 + 字数区间，且不再要求"原样保留"。"""
    d = __import__("tests.conftest", fromlist=["make_project"]).make_project(tmp_path, n_chapters=3)
    _write_subline(d)
    llm = _fake_llm(REWRITTEN)
    rewriter = FeedbackRewriter(
        d, llm_client=llm, guardrails=Guardrails(check_title=False, check_meta_leak=False)
    )

    res = rewriter.rewrite(2, "与第3章雷同，整章重写", mode="full")

    assert res.llm_used is True and res.changed_summary.startswith("整章重写")
    # 取**第一次**调用（整章重写首稿）：后续若触发字数补足，末次调用是扩写提示词
    req = llm.chat.call_args_list[0][0][0]
    system = req.messages[0]["content"]
    user = req.messages[-1]["content"]
    # system 换成整章重写语义（禁止照搬原文 / 衔接而不复述）
    assert "整章重写" in system
    assert "严禁把原文段落原样搬回" in system
    assert "衔接而不复述" in system
    # user 注入本章契约（唯一真源 chapter_contract 的产物）
    assert "- 强度档位：推进" in user
    assert "情节点序列：第2章：测试不同属性催化" in user
    assert "未提及的内容一律保留原样" not in user  # 与整章重写矛盾的措辞必须消失
    assert "字数须在" in user and "目标 3000 字" in user


def test_patch_mode_keeps_minimal_change_semantics(tmp_path):
    """patch（默认）行为不变：仍走 quality.rewrite，仍要求"未提及的内容一律保留原样"。"""
    d = __import__("tests.conftest", fromlist=["make_project"]).make_project(tmp_path, n_chapters=3)
    _write_subline(d)
    llm = _fake_llm(REWRITTEN)
    rewriter = FeedbackRewriter(
        d, llm_client=llm, guardrails=Guardrails(check_title=False, check_meta_leak=False)
    )

    rewriter.rewrite(2, "节奏太慢")

    req = llm.chat.call_args[0][0]
    assert "用最少的改动" in req.messages[0]["content"]
    assert "未提及的内容一律保留原样" in req.messages[-1]["content"]
    assert "强度档位：" not in req.messages[-1]["content"]  # patch 不注入整章契约


def test_full_mode_degrades_when_contract_missing(tmp_path, caplog):
    """契约缺失（章指向不存在的支线）必须显性降级留痕，且不阻断改写交付。"""
    d = __import__("tests.conftest", fromlist=["make_project"]).make_project(tmp_path, n_chapters=3)
    # conftest 的项目自带 S01 细纲 ⇒ 把本章指向一条不存在的支线，制造契约缺失
    ch_file = d / "chapters" / "ch002.md"
    post = frontmatter.load(ch_file)
    post.metadata["subline"] = "S99_不存在"
    ch_file.write_bytes(frontmatter.dumps(post).encode("utf-8"))

    llm = _fake_llm(REWRITTEN)
    rewriter = FeedbackRewriter(
        d, llm_client=llm, guardrails=Guardrails(check_title=False, check_meta_leak=False)
    )

    with caplog.at_level("WARNING", logger="agent.degrade.rewrite.contract"):
        res = rewriter.rewrite(2, "整章重写", mode="full")

    assert res.llm_used is True and res.error == ""  # 降级不阻断
    assert "本章逐章契约缺失" in llm.chat.call_args_list[0][0][0].messages[-1]["content"]
    assert "rewrite.contract" in caplog.text  # 降级必须可见（不留静默）


def test_illegal_mode_falls_back_to_patch(tmp_path):
    """非法 mode 回落 patch（不抛错、不改写语义）。"""
    d = __import__("tests.conftest", fromlist=["make_project"]).make_project(tmp_path, n_chapters=3)
    llm = _fake_llm(REWRITTEN)
    rewriter = FeedbackRewriter(
        d, llm_client=llm, guardrails=Guardrails(check_title=False, check_meta_leak=False)
    )

    res = rewriter.rewrite(2, "节奏太慢", mode="Full-Rewrite")

    assert res.changed_summary.startswith("按反馈重写")


# ============================================================
# 整章重写的字数硬检查 + 篇幅补足（2026-09-24）
# ============================================================
def test_full_mode_expands_short_draft_to_min(tmp_path):
    """整章重写首稿偏短时，用「整章扩写」补足到下限（本期 pilot 实证：首稿仅 1120–1255 字）。"""
    d = __import__("tests.conftest", fromlist=["make_project"]).make_project(tmp_path, n_chapters=3)
    _write_subline(d)
    long_body = _long_body(2600)
    llm = _seq_llm(REWRITTEN, long_body)
    rewriter = FeedbackRewriter(
        d, llm_client=llm, guardrails=Guardrails(check_title=False, check_meta_leak=False)
    )

    res = rewriter.rewrite(2, "整章重写", mode="full")

    assert res.new_text == long_body            # 采用了扩写稿
    assert _cjk(res.new_text) >= 2400           # 达到目标 3000 的动态下限（×0.8）
    assert llm.chat.call_count == 2             # 首稿 + 一轮扩写（达标即停）
    assert (d / "chapters" / "ch002.md").read_text(encoding="utf-8").count(long_body[:20]) == 1


def test_full_mode_rejects_expansion_that_adds_cross_chapter_dup(tmp_path, caplog):
    """扩写轮若引入新的 error 级违规（跨章重复）必须弃用——宁短不脏。"""
    d = __import__("tests.conftest", fromlist=["make_project"]).make_project(tmp_path, n_chapters=3)
    _write_subline(d)
    _seed_prose(d, {"3": _PARA_OTHER})          # 第 3 章正文：扩写稿抄了它
    dup_long = _long_body(2600) + "\n\n" + _PARA_OTHER
    llm = _seq_llm(REWRITTEN, dup_long)
    rewriter = FeedbackRewriter(
        d, llm_client=llm, guardrails=Guardrails(check_title=False, check_meta_leak=False)
    )

    with caplog.at_level("WARNING", logger="agent.degrade.rewrite.expand"):
        res = rewriter.rewrite(2, "整章重写", mode="full")

    assert res.new_text == REWRITTEN            # 弃用带重复的扩写稿
    assert "rewrite.expand" in caplog.text      # 降级必须可见
    assert "paragraph_dup" in caplog.text       # 引入的违规被点名


def test_full_mode_block_gate_rejects_short_output(tmp_path):
    """整章重写补足后仍低于下限 ⇒ BLOCK 拒绝落盘（写章口径：下限是硬闸）。"""
    d = __import__("tests.conftest", fromlist=["make_project"]).make_project(tmp_path, n_chapters=3)
    _write_subline(d)
    ch_file = d / "chapters" / "ch002.md"
    original = frontmatter.load(ch_file).content.strip()
    llm = _fake_llm(REWRITTEN)                  # 扩写轮不再变长 ⇒ 始终短
    rewriter = FeedbackRewriter(
        d, llm_client=llm, guardrails=Guardrails(check_title=False, check_meta_leak=False)
    )

    res = rewriter.rewrite(2, "整章重写", mode="full", gate_mode="block")

    assert res.blocked is True
    assert res.error == "word_count_short"
    assert res.new_text == original             # 原章保留
    assert res.backup_file is None
    assert frontmatter.load(ch_file).content.strip() == original


def test_patch_mode_is_not_blocked_by_length(tmp_path):
    """patch（最小改动）不适用整章字数硬闸：用户要求的就是局部改，不因篇幅被拦。"""
    d = __import__("tests.conftest", fromlist=["make_project"]).make_project(tmp_path, n_chapters=3)
    _write_subline(d)
    rewriter = FeedbackRewriter(
        d,
        llm_client=_fake_llm(REWRITTEN),
        guardrails=Guardrails(check_title=False, check_meta_leak=False),
    )

    res = rewriter.rewrite(2, "删掉这句", gate_mode="block")

    assert res.blocked is False
    assert res.error == ""
    assert REWRITTEN in (d / "chapters" / "ch002.md").read_text(encoding="utf-8")


# ============================================================
# 整章重写的空转判定 + 通行标记刷新（2026-09-24）
# ============================================================
def _overwrite_body(ch_file: Path, body: str) -> str:
    """把章节正文换成 ``body``，返回 ``_strip_frontmatter`` 口径的原文（即 prompt 里的原文）。"""
    post = frontmatter.load(ch_file)
    post.content = f"# 第 2 章 · 样例\n\n{body}"
    ch_file.write_bytes(frontmatter.dumps(post).encode("utf-8"))
    return frontmatter.load(ch_file).content.strip()


def test_full_mode_noop_output_is_not_saved(tmp_path, caplog):
    """模型原样回吐原文（两次）必须显性失败、不落盘，绝不报「✓ 已落盘，字数 X→X」。

    实证（2026-09-24 灵荒工坊 ch015）：整章重写任务产出「字数 2290→2290」，
    正文与原文逐字相同（模型空转）——旧链路照常落盘并报成功，用户以为改过了。
    """
    d = __import__("tests.conftest", fromlist=["make_project"]).make_project(tmp_path, n_chapters=3)
    _write_subline(d)
    ch_file = d / "chapters" / "ch002.md"
    echoed = _overwrite_body(ch_file, _long_body(2600))   # 篇幅已达标 ⇒ 不触发扩写轮
    llm = _fake_llm(echoed)
    rewriter = FeedbackRewriter(
        d, llm_client=llm, guardrails=Guardrails(check_title=False, check_meta_leak=False)
    )

    with caplog.at_level("WARNING", logger="agent.degrade.rewrite.noop"):
        res = rewriter.rewrite(2, "整章重写", mode="full")

    assert res.error == "noop_output"
    assert res.blocked is False and res.llm_used is True
    assert res.backup_file is None
    assert llm.chat.call_count == 2                        # 首稿 + 追加硬指令重试一次
    assert "rewrite.noop" in caplog.text                   # 空转必须可见（不留静默）
    assert frontmatter.load(ch_file).content.strip() == echoed  # 一字未动


def test_rewrite_refreshes_quality_passed_flag(tmp_path):
    """改写落盘后按写时门禁重算 ``quality_passed``（否则改好了仍永远显示「未通过」）。

    实证（2026-09-24 灵荒工坊）：16 章标记 ``quality_passed: false``，其中 14 章
    离线复核已无缺陷——标记是写章当时的旧值，改写路径从不刷新。
    """
    d = __import__("tests.conftest", fromlist=["make_project"]).make_project(tmp_path, n_chapters=3)
    ch_file = d / "chapters" / "ch002.md"
    _overwrite_body(ch_file, _long_body(2600) + "\n\n他忽然停住，风忽然停了，灯忽然灭了。")
    post = frontmatter.load(ch_file)
    post.metadata["quality_passed"] = False
    ch_file.write_bytes(frontmatter.dumps(post).encode("utf-8"))

    rewriter = FeedbackRewriter(
        d,
        llm_client=_fake_llm(_long_body(2700)),
        guardrails=Guardrails(check_title=False, check_meta_leak=False),
    )

    res = rewriter.rewrite(2, "去掉忽然这类套词")

    assert res.error == "" and res.blocked is False
    assert frontmatter.load(ch_file).metadata["quality_passed"] is True
    # 反向：同类套词超量时标记必须如实置 false（不是恒 true 的摆设）
    assert rewriter._gate_passed(
        _long_body(2600) + "\n\n他忽然停住，风忽然停了，灯忽然灭了。"
    ) is False


def test_rewrite_refreshes_word_count_metadata(tmp_path):
    """改写落盘后 ``word_count`` 必须刷新为正文实际值（口径同写章：去换行/去空格、不含 H1）。

    实证（2026-09-24 灵荒工坊）：ch002/ch015/ch021/ch022/ch025 的 ``word_count``
    停在写章当时的旧值——改写路径只刷 ``quality_passed``，不刷 ``word_count``，
    于是仪表盘/写章读取的都是陈旧字数。
    """
    d = __import__("tests.conftest", fromlist=["make_project"]).make_project(tmp_path, n_chapters=3)
    ch_file = d / "chapters" / "ch002.md"
    _overwrite_body(ch_file, _long_body(2600))
    post = frontmatter.load(ch_file)
    post.metadata["word_count"] = 1  # 陈旧值（模拟带外改动后的旧字数）
    ch_file.write_bytes(frontmatter.dumps(post).encode("utf-8"))

    rewriter = FeedbackRewriter(
        d,
        llm_client=_fake_llm(_long_body(2700)),
        guardrails=Guardrails(check_title=False, check_meta_leak=False),
    )
    res = rewriter.rewrite(2, "把这章扩写得更细一些")

    assert res.error == "" and res.blocked is False
    saved = frontmatter.load(ch_file)
    body = saved.content.split("\n", 1)[1]  # 去掉 H1 标题行
    assert saved.metadata["word_count"] == len(body.replace("\n", "").replace(" ", ""))
    assert saved.metadata["word_count"] != 1
