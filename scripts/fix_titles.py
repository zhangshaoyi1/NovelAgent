"""changan 占位标题 LLM 改写器（稳健版，与 guardrail title_placeholder 规则对齐）。

扫描 chapters/ 下标题正文以「第N章」开头 / 为空 / 过短(<4字) 的章节（即 guardrail 判定的
占位标题），提取首段正文，调用 LLM 生成一句有信息量、非模板化的场景化标题（保留
"# 第N章 · " 前缀），精准替换标题行。不动正文。

稳健性修复：
1. 检测与 guardrail 对齐——标题正文 startswith "第N章" 即占位（旧版只判 exact 第N章·第N章，
   漏掉 23 章，导致 43 处只修 20 处）。
2. 模型常在回标题里再带一遍「第N章」，统一正则剥离前缀，避免 "# 第 7 章 · 第7章·鬼市初探"
   这类畸形结果；剥离后正文不再以第N章开头，才能通过 guardrail。
"""
from __future__ import annotations
import json
import os
import pathlib
import re
import sys

from openai import OpenAI

import time

ROOT = pathlib.Path("D:/project/NovelAgent/agent")
NOVEL_CH = pathlib.Path("D:/project/NovelAgent/novels/changan-binyiguan/chapters")
BATCH = 8

_TITLE_RE = re.compile(r"^(#\s*第\s*(\d+)\s*章\s*·\s*)(.*?)\s*$", re.MULTILINE)
# 剥离模型可能带回的"第N章"前缀
_CHAPTER_PREFIX_RE = re.compile(r"^第\s*\d+\s*章\s*[·\-－:：]?\s*")
# 模型偶发回的无效标题，需当作占位重生成
_GARBAGE = {"无法生成", "无法", "生成失败", "none", "null", ""}


def call_llm_with_retry(client, model, prompt, max_retry=5):
    """带指数退避的 LLM 调用，应对 Connection error / 限流。"""
    last_err = None
    for attempt in range(max_retry):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4,
                response_format={"type": "json_object"},
                timeout=90,
            )
            return resp.choices[0].message.content
        except Exception as e:  # noqa: BLE001
            last_err = e
            wait = 2 ** attempt * 3
            print(f"    [retry {attempt+1}/{max_retry}] {type(e).__name__}: {e}，{wait}s 后重试")
            time.sleep(wait)
    raise last_err


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


def extract_title_and_body(text: str):
    """返回 (章节号, 原标题正文, 首段正文)。无标题行返回 None。"""
    body = strip_frontmatter(text)
    m = _TITLE_RE.search(body)
    if not m:
        return None
    n = int(m.group(2)) if m.group(2).isdigit() else int(m.group(1))
    prefix = m.group(1)          # "# 第5章 · "
    title_body = m.group(3).strip()
    after = body[m.end():]
    first_para = ""
    for para in re.split(r"\n\s*\n", after):
        para = para.strip()
        if para and not para.startswith("#"):
            first_para = para
            break
    return n, title_body, first_para


def is_placeholder(old_title: str, n: int) -> bool:
    """与 guardrail title_placeholder 判定对齐。"""
    if not old_title:
        return True
    if old_title in _GARBAGE:
        return True
    if len(old_title) < 4:
        return True
    if old_title == f"第{n}章":
        return True
    if old_title == f"第{n}章·第{n}章":
        return True
    if old_title.startswith(f"第{n}章"):
        return True
    return False


def clean_title(model_title: str, n: int) -> str:
    """剥离模型可能带回的『第N章』前缀，只留场景/主题。"""
    t = (model_title or "").strip().strip('"').strip("'")
    t = _CHAPTER_PREFIX_RE.sub("", t)
    return t.strip()


def main() -> None:
    env = load_env(ROOT / ".env")
    client = OpenAI(api_key=env.get("LLM_API_KEY"), base_url=env.get("LLM_BASE_URL"))
    model = env.get("LLM_MODEL_ID")

    files = sorted(NOVEL_CH.glob("ch*.md"))
    tasks: list[tuple[str, int, str, str]] = []  # (ch, n, prefix, first_para)
    for f in files:
        txt = f.read_text(encoding="utf-8")
        ex = extract_title_and_body(txt)
        if not ex:
            continue
        n, old_title, first_para = ex
        ch = f.stem
        if is_placeholder(old_title, n) and first_para:
            prefix = f"# 第 {n} 章 · "
            tasks.append((ch, n, prefix, first_para))

    print(f"需改写标题章节：{len(tasks)}")
    limit = int(os.environ.get("LIMIT", "0"))
    if limit > 0:
        tasks = tasks[:limit]

    total_batches = (len(tasks) + BATCH - 1) // BATCH
    done = 0
    for bi in range(total_batches):
        batch = tasks[bi * BATCH:(bi + 1) * BATCH]
        prompt_lines = []
        meta = []
        for ch, n, prefix, first_para in batch:
            prompt_lines.append(f"【{ch}】首段：{first_para[:200]}")
            meta.append((ch, n, prefix))
        prompt = (
            "你是一名小说编辑。下面若干章节的首段正文，请为每章生成一句"
            "有信息量、非模板化、能概括本章核心场景或冲突的章节标题。\n"
            "要求：\n"
            "1. 用具体的名词短语或动宾短句（如'棺材铺里的不速之客''夜探长生殿''纸人哭丧夜'），"
            "12-25字为宜；\n"
            "2. 严禁空泛句式与元叙述，如'在……中，……等待着主角''故事的这一部分……'"
            "'一场危机悄然降临'等套话；\n"
            "3. 不要出现'第X章'、不要用'惊变''风云'等烂大街套话、不要剧透结局。\n"
            "返回严格 JSON，键为章节标识（如'ch005'），值为标题文本（只写场景/主题，"
            "绝对不要带'第X章'前缀）。\n"
            "只返回 JSON，不要 markdown 围栏。\n\n"
            + "\n".join(prompt_lines)
        )
        try:
            out = call_llm_with_retry(client, model, prompt)
            mapping = json.loads(out)
        except Exception as e:  # noqa: BLE001
            print(f"[batch {bi}] LLM 最终失败：{e}，跳过本批（可重跑补完）")
            continue

        for ch, n, prefix in meta:
            raw = mapping.get(ch)
            if not raw:
                continue
            new_title = clean_title(raw, n)
            if not new_title or len(new_title) < 4 or new_title in _GARBAGE:
                print(f"    [skip] {ch} 模型返回无效标题（{new_title!r}），保留占位待重跑")
                continue
            f = NOVEL_CH / f"{ch}.md"
            lines = f.read_text(encoding="utf-8").split("\n")
            replaced = False
            in_fm = False
            fm_done = False
            for idx, l in enumerate(lines):
                if l.strip() == "---":
                    if not fm_done:
                        in_fm = not in_fm
                        if not in_fm:
                            fm_done = True
                    continue
                if in_fm:
                    continue
                if re.match(r"^#\s*第\s*\d+\s*章", l):
                    lines[idx] = f"{prefix}{new_title}"
                    replaced = True
                    break
            if replaced:
                f.write_text("\n".join(lines), encoding="utf-8")
                print(f"  {ch}: {prefix}{new_title}")
                done += 1
        print(f"[batch {bi+1}/{total_batches}] 累计改 {done} 章")

    print(f"标题改写完成：{done} 章")


if __name__ == "__main__":
    main()
