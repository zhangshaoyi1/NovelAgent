"""changan 英文残留精准定位器。

扫描 chapters/ 下每章，定位正文（剥离 frontmatter）中混入的英文单词/特征串，
输出结构化 JSON：每章含 [行号, 上下文段落, 命中的英文片段]，供 LLM 批量改写。
不修改任何文件。
"""
from __future__ import annotations
import json
import re
import pathlib

from agent.core.guardrails import (
    Guardrails, _JUNK_SIGNATURES, _RE_ENGLISH_WORD,
)

NOVEL = pathlib.Path("D:/project/NovelAgent/novels/changan-binyiguan/chapters")
OUT = pathlib.Path("D:/project/NovelAgent/novels/changan-binyiguan/.state/english_scan.json")

# 复用 guardrails 的英文检测正则与特征串
SIG_PATTERNS = [s for s in _JUNK_SIGNATURES]
WORD_RE = _RE_ENGLISH_WORD


def strip_frontmatter(text: str) -> str:
    return re.sub(r"^---\s*\n[\s\S]*?\n---\s*\n", "", text, count=1)


def locate_english(text: str) -> list[dict]:
    """返回每处英文命中的 {line, paragraph, hits:[英文片段]}。"""
    body = strip_frontmatter(text)
    paras = body.split("\n")
    results: list[dict] = []
    for i, line in enumerate(paras, start=1):
        if not line.strip():
            continue
        hits: list[str] = []
        low = line.lower()
        for sig in SIG_PATTERNS:
            if sig.lower() in low:
                hits.append(sig)
        for m in WORD_RE.finditer(line):
            w = m.group(0)
            # 跳过纯数字/已是中文行里的正常情况（此处仅收集英文片段）
            hits.append(w)
        # 去重保序
        seen = set()
        hits = [h for h in hits if not (h in seen or seen.add(h))]
        if hits:
            results.append({
                "line": i,
                "paragraph": line.strip(),
                "hits": hits,
            })
    return results


def main() -> None:
    gr = Guardrails(check_junk=True, check_title=False, check_dup=False)
    files = sorted(NOVEL.glob("ch*.md"))
    report: dict[str, list[dict]] = {}
    summary = {}
    for f in files:
        txt = f.read_text(encoding="utf-8")
        # 用 guardrails 确认是否命中（避免误判边界）
        r = gr.check_text(txt)
        junk_v = [v for v in r.violations if v.rule_id == "non_chinese_junk"]
        if not junk_v:
            continue
        locs = locate_english(txt)
        if locs:
            report[f.stem] = locs
            summary[f.stem] = {
                "violation_msg": junk_v[0].message,
                "hit_lines": [l["line"] for l in locs],
                "hit_count": len(locs),
            }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "summary": summary,
        "detail": report,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"扫描完成：{len(report)} 章含英文残留")
    print(f"总命中行数：{sum(len(v) for v in report.values())}")
    print(f"报告已写：{OUT}")


if __name__ == "__main__":
    main()
