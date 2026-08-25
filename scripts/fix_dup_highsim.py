"""
fix_dup_highsim.py — 对 changan 小说中"建议修改"的 ≥0.95 高相似跨章重复段落做定向改写。

设计：
- 仅改写用户确认要改的段落（A 类场景雷同 + 两处明显逐字漏改 + ch146 边界），保留最早出现的章节为基准（canonical）。
- 每处：以整章正文为上下文，让 LLM 重写该段——保留本章角色/情境，但换角度、细节、句式，推进情节，且绝不出现英文。
- 英文守卫：若重写结果含英文字母则重试，最多 5 次指数退避。
- 只做精确整段替换，不触碰其他正文。

用法：
    python scripts/fix_dup_highsim.py [--dry-run]
"""
import os
import re
import sys
import time
import pathlib
import json

PROJECT = pathlib.Path(r"D:/project/NovelAgent/novels/changan-binyiguan")
CHAPTERS = PROJECT / "chapters"


def load_env():
    env = {}
    p = pathlib.Path(r"D:/project/NovelAgent/agent/.env")
    if not p.exists():
        raise SystemExit("NO .env")
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def strip_frontmatter(text: str) -> str:
    return re.sub(r"^---\s*\n[\s\S]*?\n---\s*\n", "", text, count=1)


def split_paragraphs(body: str):
    out = []
    for p in re.split(r"\n\s*\n", body):
        p = p.strip()
        if p and not p.startswith("#"):
            out.append(p)
    return out


def get_para(ch: str, anchor: str):
    txt = (CHAPTERS / f"{ch}.md").read_text(encoding="utf-8")
    body = strip_frontmatter(txt)
    for p in split_paragraphs(body):
        if anchor in p:
            return p
    return None  # anchor 已不存在（可能已改写过），由调用方跳过


def call_llm_with_retry(client, model, prompt, timeout=90, max_retries=5):
    delay = 5
    for attempt in range(1, max_retries + 1):
        try:
            r = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4,
                max_tokens=600,
                timeout=timeout,
            )
            return r.choices[0].message.content
        except Exception as e:  # noqa: BLE001
            print(f"    [retry {attempt}/{max_retries}] LLM 异常: {e}")
            if attempt < max_retries:
                time.sleep(delay)
                delay = min(delay * 2, 60)
    raise RuntimeError("LLM 最终失败")


EN_RE = re.compile(r"[A-Za-z]{2,}")


def rewrite_para(client, model, ch, chapter_full, old_para, canon_para):
    budget = max(40, int(len(old_para) * 1.3))
    prompt = (
        "你是成熟的中文悬疑小说编辑。以下是小说《我在长安开殡仪馆那些年》某一章的完整正文。\n"
        "其中被标记为 [REWRITE]...[/REWRITE] 的段落与前面章节高度雷同（已有版本见 [CANON]），"
        "读起来像复制粘贴，需要重写。\n"
        "要求：\n"
        "1. 只重写 [REWRITE] 那段，其余正文一字不改；\n"
        "2. 保留该章的角色、即时情境与动作走向，但换不同的观察角度、不同细节、不同句式；\n"
        "3. 让这段在本章里真正推进情节或情绪，而不是复述前文；\n"
        "4. 绝对不要出现任何英文字母或拼音词；\n"
        f"5. 长度上限约 {budget} 字，只写单一段落，不要展开成多段、不要写后续情节。\n"
        "只返回重写后的那段纯文本，不要引号包裹、不要解释、不要 markdown。\n\n"
        f"[CHAPTER]\n{chapter_full}\n[/CHAPTER]\n\n"
        f"[REWRITE]\n{old_para}\n[/REWRITE]\n\n"
        f"[CANON]\n{canon_para}\n[/CANON]"
    )
    delay = 4
    best = None
    hard_cap = int(len(old_para) * 2.2)
    for attempt in range(1, 9):
        out = call_llm_with_retry(client, model, prompt)
        if not out:
            time.sleep(delay); delay = min(delay * 2, 30); continue
        out = out.strip().strip("“”\"'")
        out = re.split(r"\n\s*\n", out)[0].strip()
        if EN_RE.search(out):
            print(f"    [retry {attempt}] 重写含英文，丢弃重试")
            time.sleep(delay); delay = min(delay * 2, 30); continue
        if len(out) < max(10, len(old_para) * 0.4):
            print(f"    [retry {attempt}] 重写过短，丢弃重试")
            time.sleep(delay); delay = min(delay * 2, 30); continue
        if len(out) > hard_cap:
            print(f"    [retry {attempt}] 重写 {len(out)} 字超硬上限 {hard_cap}，丢弃")
            time.sleep(delay); delay = min(delay * 2, 30); continue
        # 通过基础校验：无英文、长度合理
        if len(out) <= int(budget * 1.15):
            return out
        if best is None or len(out) < len(best):
            best = out
        print(f"    [retry {attempt}] 重写 {len(out)} 字 > 软上限 {budget}，争取更紧凑")
        time.sleep(delay); delay = min(delay * 2, 30); continue
    if best:
        return best
    raise RuntimeError(f"{ch}: 重写最终失败（英文/过短/超长）")


# (chapter, anchor, canonical_chapter, canonical_anchor)
TARGETS = [
    ("ch019", "红得像浸了血", "ch017", "红得像浸了血"),
    ("ch012", "烙上去的", "ch011", "烙上去的"),
    ("ch067", "前朝巫蛊案的证物", "ch066", "青铜残片收回袖中"),
    ("ch067", "赵铁近来异常活跃", "ch062", "赵铁近来异常活跃"),
    ("ch068", "前朝巫蛊案的证物", "ch066", "青铜残片收回袖中"),
    ("ch069", "前朝巫蛊案的证物", "ch066", "青铜残片收回袖中"),
    ("ch070", "前朝巫蛊案的证物", "ch066", "青铜残片收回袖中"),
    ("ch073", "前朝巫蛊案的证物", "ch066", "青铜残片收回袖中"),
]


def main():
    dry = "--dry-run" in sys.argv
    env = load_env()
    from openai import OpenAI
    client = OpenAI(api_key=env.get("LLM_API_KEY"), base_url=env.get("LLM_BASE_URL"))
    model = env.get("LLM_MODEL_ID")

    applied = []
    for ch, anchor, cch, canc in TARGETS:
        old = get_para(ch, anchor)
        if old is None:
            print(f"[skip] {ch} 锚点={anchor} 已不存在（可能已改写），跳过")
            continue
        canon = get_para(cch, canc)
        if canon is None:
            print(f"[skip] {ch} 基准 {cch}/{canc} 已不存在，跳过")
            continue
        chapter_full = (CHAPTERS / f"{ch}.md").read_text(encoding="utf-8")
        print(f"[>] {ch} 锚点={anchor} (原段 {len(old)} 字, 基准 {cch})")
        if dry:
            applied.append((ch, anchor, old, "<dry>"))
            continue
        new = rewrite_para(client, model, ch, chapter_full, old, canon)
        # 读取完整文件并精确替换
        path = CHAPTERS / f"{ch}.md"
        full = path.read_text(encoding="utf-8")
        if old not in full:
            print(f"    [!] 原段未在 {ch} 中精确匹配，跳过")
            continue
        full = full.replace(old, new, 1)
        path.write_text(full, encoding="utf-8")
        print(f"    [ok] 改写为 {len(new)} 字")
        applied.append((ch, anchor, old, new))
        time.sleep(1.5)

    # 保存 manifest
    man = [{"chapter": c, "anchor": a, "old": o, "new": n} for c, a, o, n in applied]
    (PROJECT / ".state" / "highsim_fix_manifest.json").write_text(
        json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n完成 {len(applied)} 处，manifest -> .state/highsim_fix_manifest.json")


if __name__ == "__main__":
    main()
