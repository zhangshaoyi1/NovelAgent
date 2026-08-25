"""changan 英文残留 LLM 最小改动改写器（稳健版）。

读 .state/english_scan.json，分批调用真实 LLM，对每处含英文的行返回"仅把英文片段
翻译/改写为自然中文"的完整行（不动其他内容）。脚本用精确字符串替换原文件对应行。

稳健性修复：给每行分配稳定行号标识（L001/L002…）作为 JSON 键，避免模型在回写键里
改写原文字符导致精确匹配失败（旧版用"原行全文"做键，模型常微调一两个字使 lookup 落空）。
"""
from __future__ import annotations
import json
import os
import pathlib
import re
import sys

from openai import OpenAI

ROOT = pathlib.Path("D:/project/NovelAgent/agent")
NOVEL_CH = pathlib.Path("D:/project/NovelAgent/novels/changan-binyiguan/chapters")
SCAN = pathlib.Path("D:/project/NovelAgent/novels/changan-binyiguan/.state/english_scan.json")
BATCH = 8


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


import time


def call_llm_with_retry(client, model, prompt, max_retry=5):
    """带指数退避的 LLM 调用，应对 Connection error / 限流。"""
    last_err = None
    for attempt in range(max_retry):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                response_format={"type": "json_object"},
                timeout=90,
            )
            return resp.choices[0].message.content
        except Exception as e:  # noqa: BLE001
            last_err = e
            wait = 2 ** attempt * 3  # 3,6,12,24,48s
            print(f"    [retry {attempt+1}/{max_retry}] {type(e).__name__}: {e}，{wait}s 后重试")
            time.sleep(wait)
    raise last_err


PROMPT = """你是一名中文小说编辑。下面是一批带行号标识的小说正文行，其中混入了英文单词或短语（如 said/voice/last/otts 等模型生成残留），破坏了中文成书的纯净度。
你的任务：对每一行，仅把其中的英文片段翻译/改写为自然、符合上下文的中文，保持原句其他内容、标点、语气完全不变。若一行有多处英文，全部改写。
绝对不要重写整句、不要添加解释、不要改变任何非英文部分。

返回严格 JSON 对象，键为每行左侧的"行号标识"（如 "L001"、"L002"，务必原样照抄、不要改动），值为"仅替换英文后的中文整行"。只返回 JSON，不要 markdown 围栏。

待处理行（每行一个，左侧为行号标识）：
"""


def main() -> None:
    env = load_env(ROOT / ".env")
    client = OpenAI(api_key=env.get("LLM_API_KEY"), base_url=env.get("LLM_BASE_URL"))
    model = env.get("LLM_MODEL_ID")
    scan = json.loads(SCAN.read_text(encoding="utf-8"))
    detail = scan["detail"]

    chapters = list(detail.keys())
    limit = int(os.environ.get("LIMIT", "0"))
    if limit > 0:
        chapters = chapters[:limit]
    total_batches = (len(chapters) + BATCH - 1) // BATCH
    done = 0

    for bi in range(total_batches):
        batch_chs = chapters[bi * BATCH:(bi + 1) * BATCH]
        # 收集本批所有待改写行（去重，保留章节映射）
        lines_to_fix: list[str] = []
        line_owner: dict[str, str] = {}
        for ch in batch_chs:
            for loc in detail[ch]:
                para = loc["paragraph"]
                if para not in line_owner:
                    line_owner[para] = ch
                    lines_to_fix.append(para)

        if not lines_to_fix:
            continue

        # 分配稳定行号标识，避免模型改动能回写键
        id_map = {f"L{i+1:03d}": para for i, para in enumerate(lines_to_fix)}
        prompt_lines = [f"【{lid}】{para}" for lid, para in id_map.items()]
        prompt = PROMPT + "\n".join(prompt_lines)
        try:
            out = call_llm_with_retry(client, model, prompt)
            mapping = json.loads(out)
        except Exception as e:  # noqa: BLE001
            print(f"[batch {bi}] LLM 调用最终失败：{e}，跳过本批（可重跑补完）")
            continue

        # 逐章替换
        for ch in batch_chs:
            f = NOVEL_CH / f"{ch}.md"
            if not f.exists():
                continue
            lines = f.read_text(encoding="utf-8").split("\n")
            changed = 0
            for loc in detail[ch]:
                para = loc["paragraph"]
                lid = next((k for k, v in id_map.items() if v == para), None)
                if lid is None:
                    continue
                new_para = mapping.get(lid)
                if not new_para or new_para == para:
                    continue
                # 精确替换：在文件中找到该行并替换（保留行号上下文）
                for idx, l in enumerate(lines):
                    if l.strip() == para:
                        lines[idx] = new_para
                        changed += 1
                        break
            if changed:
                f.write_text("\n".join(lines), encoding="utf-8")
                print(f"  {ch}: 改写 {changed} 行")
                done += 1
        print(f"[batch {bi+1}/{total_batches}] 处理 {len(batch_chs)} 章，累计改 {done} 章")

    print(f"全部完成：改写 {done} 章")


if __name__ == "__main__":
    main()
