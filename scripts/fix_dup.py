"""changan 跨章逐字重复（相似度 1.00）去重改写器 —— Option A 全量去重。

策略：
- 以段落归一化 hash 为键，扫描 chapters/ 下所有正文段落（仅在 >=40 字的长段落上比对，
  与 guardrail paragraph_dup 阈值一致），找出跨章逐字重复（相同归一化段落出现在 >1 个不同章节）。
- 每个唯一重复段：保留「最早出现章节」为基准（canonical），其余后续章节的副本调用 LLM 改写为
  多个不同措辞的变体（variant），轮询分布，从而打破逐字重复簇。
- 小簇（副本数 <= MAX_VARIANTS）逐份唯一改写；大簇（如 ch024 灵堂段重复 171 次）生成
  MAX_VARIANTS 个变体轮询，将单一巨型重复簇拆散为若干个不同措辞。

稳健性：
- 段落定位用归一化 hash 精确匹配，不依赖 excerpt 前缀。
- LLM 调用带 5 次指数退避重试；最终失败则跳过该段并记录到 manifest。
- 仅替换命中段落，不动其他内容；先剥离 frontmatter 再定位，避免误改元数据。
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import time
from collections import defaultdict

from openai import OpenAI

from agent.core.guardrails import Guardrails

ROOT = pathlib.Path("D:/project/NovelAgent/agent")
NOVEL_CH = pathlib.Path("D:/project/NovelAgent/novels/changan-binyiguan/chapters")
STATE = pathlib.Path("D:/project/NovelAgent/novels/changan-binyiguan/.state")
MANIFEST = STATE / "dedup_fix_manifest.json"

MIN_LEN = 40          # 与 guardrail paragraph_dup 阈值一致
MAX_VARIANTS = 6      # 单段最多生成的变体数（大簇轮询用）


def load_env(p: pathlib.Path) -> dict:
    env = {}
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def strip_frontmatter(text: str) -> str:
    return re.sub(r"^---\s*\n[\s\S]*?\n---\s*\n", "", text, count=1)


def split_paras(body: str):
    out = []
    for para in re.split(r"\n\s*\n", body):
        para = para.strip()
        if not para or para.startswith("#"):
            continue
        out.append(para)
    return out


_gr = Guardrails()


def norm(para: str) -> str:
    return _gr._normalize_paragraph(para)


_EN_RE = re.compile(r"[A-Za-z]{2,}")


def _valid_variant(v: str) -> bool:
    """变体必须非空且为纯中文（不得含英文字母，避免去重时重新引入英文残留）。"""
    return bool(v) and not _EN_RE.search(v)


def ch_num(name: str) -> int:
    m = re.search(r"ch(\d+)", name)
    return int(m.group(1)) if m else 0


def para_in_file_by_norm(ch_file: pathlib.Path, target_norm: str):
    """返回该文件中归一化等于 target_norm 的第一个段落原始文本。"""
    txt = ch_file.read_text(encoding="utf-8")
    body = strip_frontmatter(txt)
    for para in split_paras(body):
        if norm(para) == target_norm:
            return para
    return None


def call_llm_with_retry(client, model, prompt, max_retry=5):
    last_err = None
    for attempt in range(max_retry):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.8,
                response_format={"type": "json_object"},
                timeout=120,
            )
            return resp.choices[0].message.content
        except Exception as e:  # noqa: BLE001
            last_err = e
            wait = 2 ** attempt * 3
            print(f"    [retry {attempt+1}/{max_retry}] {type(e).__name__}: {e}，{wait}s 后重试")
            time.sleep(wait)
    raise last_err


def main() -> None:
    env = load_env(ROOT / ".env")
    client = OpenAI(api_key=env.get("LLM_API_KEY"), base_url=env.get("LLM_BASE_URL"))
    model = env.get("LLM_MODEL_ID")

    files = sorted(NOVEL_CH.glob("ch*.md"))

    # 1) 建立 归一化 -> 出现章节列表（按章节去重计数）
    norm_to_chapters: dict[str, list[int]] = defaultdict(list)
    for f in files:
        txt = f.read_text(encoding="utf-8")
        body = strip_frontmatter(txt)
        seen_in_file = set()
        for para in split_paras(body):
            if len(para) < MIN_LEN:
                continue
            n = norm(para)
            if n in seen_in_file:
                continue
            seen_in_file.add(n)
            norm_to_chapters[n].append(ch_num(f.stem))

    # 2) 只保留跨章（>1 个不同章节）重复段
    dup_groups = {n: chs for n, chs in norm_to_chapters.items() if len(set(chs)) > 1}
    print(f"跨章逐字重复的唯一段落数：{len(dup_groups)}")

    manifest = {}
    total_rewrites = 0

    for n, chs in sorted(dup_groups.items(), key=lambda kv: min(kv[1])):
        chapters_sorted = sorted(set(chs))
        canonical_ch = chapters_sorted[0]
        copies = chapters_sorted[1:]
        if not copies:
            continue

        canonical_file = NOVEL_CH / f"ch{canonical_ch:03d}.md"
        canonical_para = para_in_file_by_norm(canonical_file, n)
        if not canonical_para:
            print(f"  [skip] 未在 canonical ch{canonical_ch} 定位段落")
            continue

        k = min(len(copies), MAX_VARIANTS)
        prompt = (
            "你是一名小说编辑。下面是一段悬疑小说中的场景/动作描写，它在多个章节里被逐字复用，"
            "造成严重的重复感。请在不改变语义、情节、人物动作、信息量的前提下，仅改写措辞与句式，"
            f"生成 {k} 个彼此不同、但意思一致的版本，供不同章节替换使用。\n"
            "要求：\n"
            "1. 每个版本独立成段，保持原长度量级，不要删减关键信息；\n"
            "2. 不要加序号、不要加解释、不要改变人称与叙事视角；\n"
            "3. 各版本之间用词/句式要有明显差异，避免只是换同义词。\n"
            f"返回严格 JSON，键为 v1 到 v{k}，值为改写文本。只返回 JSON。\n\n"
            f"原文：\n{canonical_para}"
        )

        variants = []
        try:
            out = call_llm_with_retry(client, model, prompt)
            parsed = json.loads(out)
            for i in range(1, k + 1):
                v = parsed.get(f"v{i}", "").strip()
                if _valid_variant(v):
                    variants.append(v)
            # 严格重试：若混入英文则重新要求纯中文
            if len(variants) < k:
                strict_prompt = (
                    prompt
                    + "\n【重要】务必只输出纯中文，不得出现任何英文字母、拼音或英文单词；"
                      "上一轮返回混入英文，已作废，请重新生成。"
                )
                out2 = call_llm_with_retry(client, model, strict_prompt)
                parsed2 = json.loads(out2)
                for i in range(1, k + 1):
                    v = parsed2.get(f"v{i}", "").strip()
                    if _valid_variant(v) and v not in variants:
                        variants.append(v)
        except Exception as e:  # noqa: BLE001
            print(f"  [skip] 段落(canonical ch{canonical_ch}) LLM 最终失败：{e}")
            manifest[n[:24]] = {"canonical_ch": canonical_ch, "status": "llm_failed", "copies": copies}
            continue

        if not variants:
            print(f"  [skip] 段落(canonical ch{canonical_ch}) 未解析出有效变体")
            continue

        applied = []
        for i, ch in enumerate(copies):
            variant = variants[i % len(variants)]
            cf = NOVEL_CH / f"ch{ch:03d}.md"
            raw = para_in_file_by_norm(cf, n)
            if not raw:
                applied.append({"ch": ch, "status": "not_found"})
                continue
            txt = cf.read_text(encoding="utf-8")
            new_txt = txt.replace(raw, variant, 1)
            if new_txt == txt:
                applied.append({"ch": ch, "status": "replace_failed"})
                continue
            cf.write_text(new_txt, encoding="utf-8")
            applied.append({"ch": ch, "status": "ok", "variant_idx": i % len(variants)})
            total_rewrites += 1

        manifest[n[:24]] = {
            "canonical_ch": canonical_ch,
            "copies_count": len(copies),
            "variants_generated": len(variants),
            "applied": applied,
        }
        print(f"  段落(canonical ch{canonical_ch}, 副本 {len(copies)} 章) -> 改写 {sum(1 for a in applied if a['status']=='ok')} 处")

    STATE.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"去重写入完成：共改写 {total_rewrites} 处跨章重复副本")
    print(f"清单已写：{MANIFEST}")


if __name__ == "__main__":
    main()
