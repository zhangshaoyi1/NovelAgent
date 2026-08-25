"""changan 跨章重复精准分析器（排除标题行噪声）。

逐章注册全书指纹库，扫描 ≥40 字长段落的跨章相似（>0.85），但**排除 # 标题行**，
输出结构化报告：每处 {章, 相似度, 与哪章重复, 重复段原文}。供后续 LLM 改写或人工判断。
不修改任何文件。
"""
from __future__ import annotations
import json
import pathlib
import re
import difflib

from agent.core.guardrails import (
    Guardrails, _DUP_MIN_CHARS, _DUP_SIMILARITY,
)

NOVEL = pathlib.Path("D:/project/NovelAgent/novels/changan-binyiguan/chapters")
OUT = pathlib.Path("D:/project/NovelAgent/novels/changan-binyiguan/.state/dup_scan_v2.json")


def strip_frontmatter(text: str) -> str:
    return re.sub(r"^---\s*\n[\s\S]*?\n---\s*\n", "", text, count=1)


def long_paragraphs(text: str) -> list[str]:
    """返回正文长段落（排除标题行 # 开头）。"""
    body = strip_frontmatter(text)
    paras = []
    for p in re.split(r"\n\s*\n", body):
        p = p.strip()
        if not p:
            continue
        if p.startswith("#"):   # 排除标题行
            continue
        if len(p) >= _DUP_MIN_CHARS:
            paras.append(p)
    return paras


def norm(p: str) -> str:
    return Guardrails()._normalize_paragraph(p)


def main() -> None:
    files = sorted(NOVEL.glob("ch*.md"))
    # 建立 章 -> [归一化段] 映射（用于回溯原文）
    norm_map: dict[str, list[tuple[str, str]]] = {}
    for f in files:
        txt = f.read_text(encoding="utf-8")
        norm_map[f.stem] = [(norm(p), p) for p in long_paragraphs(txt)]

    results: list[dict] = []
    for f in files:
        ch = f.stem
        txt = f.read_text(encoding="utf-8")
        cur = norm_map[ch]
        # 与之前所有章比对
        for prev_ch, prev_paras in norm_map.items():
            if prev_ch >= ch:   # 只比前面的章
                continue
            for cn, cp in cur:
                for pn, pp in prev_paras:
                    s = difflib.SequenceMatcher(None, cn, pn).ratio()
                    if s >= _DUP_SIMILARITY:
                        results.append({
                            "chapter": ch,
                            "dup_with": prev_ch,
                            "similarity": round(s, 3),
                            "para_excerpt": cp[:120],
                        })
                        break  # 该段只记一次
    # 按章聚合
    by_ch: dict[str, list[dict]] = {}
    for r in results:
        by_ch.setdefault(r["chapter"], []).append(r)
    OUT.write_text(json.dumps({
        "total_hits": len(results),
        "by_chapter": by_ch,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"跨章重复命中：{len(results)} 处，涉及 {len(by_ch)} 章")
    print(f"报告：{OUT}")


if __name__ == "__main__":
    main()
