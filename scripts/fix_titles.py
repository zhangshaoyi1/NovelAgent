"""changan 占位标题 LLM 改写器。

扫描 chapters/ 下标题为 第N章·第N章 / 空 / 过短(<4字) 的章节，提取首段正文，
调用 LLM 生成一句有信息量、非模板化的场景化标题（保留 "# 第N章 · " 前缀），
精准替换原标题行。不动正文。
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

_TITLE_RE = re.compile(r"^(#\s*第\s*(\d+)\s*章\s*·\s*)(.*?)\s*$", re.MULTILINE)


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
    """返回 (标题前缀如'# 第5章 · ', 原标题正文, 首段正文)。"""
    body = strip_frontmatter(text)
    m = _TITLE_RE.search(body)
    if not m:
        return None
    prefix = m.group(1)          # "# 第5章 · "
    title_body = m.group(3).strip()
    # 首段：标题之后到第一个空行
    after = body[m.end():]
    first_para = ""
    for para in re.split(r"\n\s*\n", after):
        para = para.strip()
        if para and not para.startswith("#"):
            first_para = para
            break
    return prefix, title_body, first_para


def main() -> None:
    env = load_env(ROOT / ".env")
    client = OpenAI(api_key=env.get("LLM_API_KEY"), base_url=env.get("LLM_BASE_URL"))
    model = env.get("LLM_MODEL_ID")

    files = sorted(NOVEL_CH.glob("ch*.md"))
    # 收集需改写的章节（占位/空/过短）
    tasks: list[tuple[str, str, str, str]] = []  # (ch, prefix, old_title, first_para)
    for f in files:
        txt = f.read_text(encoding="utf-8")
        ex = extract_title_and_body(txt)
        if not ex:
            continue
        prefix, old_title, first_para = ex
        ch = f.stem
        n = ch[2:]
        is_placeholder = (old_title == f"第{n}章" or old_title == f"第{n}章·第{n}章"
                           or old_title == "" or len(old_title) < 4)
        if is_placeholder and first_para:
            tasks.append((ch, prefix, old_title, first_para))

    print(f"需改写标题章节：{len(tasks)}")
    limit = int(os.environ.get("LIMIT", "0"))
    if limit > 0:
        tasks = tasks[:limit]

    total_batches = (len(tasks) + BATCH - 1) // BATCH
    done = 0
    for bi in range(total_batches):
        batch = tasks[bi * BATCH:(bi + 1) * BATCH]
        # 构造 LLM 输入：每章给首段，要一句标题
        prompt_lines = []
        meta = []
        for ch, prefix, old_title, first_para in batch:
            prompt_lines.append(f"【{ch}】首段：{first_para[:200]}")
            meta.append((ch, prefix))
        prompt = (
            "你是一名小说编辑。下面是若干章节的首段正文，请为每章生成一句"
            "有信息量、非模板化、能概括本章核心场景或冲突的章节标题（12-25字为宜，"
            "不要出现'第X章'、不要使用'惊变''风云'等烂大街套话、不要剧透结局）。\n"
            "返回严格 JSON，键为章节标识（如'ch005'），值为标题文本（不含 '# 第N章 · ' 前缀）。\n"
            "只返回 JSON，不要 markdown 围栏。\n\n"
            + "\n".join(prompt_lines)
        )
        try:
            out = call_llm_with_retry(client, model, prompt)
            mapping = json.loads(out)
        except Exception as e:  # noqa: BLE001
            print(f"[batch {bi}] LLM 最终失败：{e}，跳过本批（可重跑补完）")
            continue

        for ch, prefix in meta:
            new_title = mapping.get(ch)
            if not new_title:
                continue
            new_title = new_title.strip().strip('"').strip("'")
            if not new_title or new_title == "第" or len(new_title) < 4:
                continue
            f = NOVEL_CH / f"{ch}.md"
            lines = f.read_text(encoding="utf-8").split("\n")
            # 找到标题行（第一个以 # 第 开头的行，在 frontmatter 之后）
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
