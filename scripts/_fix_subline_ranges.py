"""修复 jipin 各支线章节范围：对齐驱动脚本的 5支线×25章(共125章)方案。
重写每个 subline.md 的「剧集压力曲线」表格，并同步 outline.md 表格的章节分配列。
"""
import re, pathlib

BASE = pathlib.Path("projects/jipin-yixian")
SUB = BASE / "sublines"

# 每条支线起始章（连续 25 章）
MAPPING = {
    "S01_情感清算_前缘断绝": 1,
    "S02_医馆崛起_积累功德": 26,
    "S03_身世揭秘_医典之谜": 51,
    "S04_正邪博弈_宗门围剿": 76,
    "S05_终极清算_仙尊登临": 101,
}

def curve_table(start: int) -> str:
    return (
        "## 剧集压力曲线\n\n"
        "| 阶段 | 章节 | 张力等级 |\n"
        "|---|---|---|\n"
        f"| 铺垫 | {start}-{start+7} | 低 |\n"
        f"| 冲突 | {start+8}-{start+17} | 中 |\n"
        f"| 高潮 | {start+18}-{start+23} | 高 |\n"
        f"| 舒缓 | {start+24} | 低 |\n"
    )

# 1) 重写 subline.md 的剧集压力曲线段（该段到文件末尾）
for name, start in MAPPING.items():
    p = SUB / name / "subline.md"
    text = p.read_text(encoding="utf-8")
    idx = text.index("## 剧集压力曲线")
    head = text[:idx]  # 包含段前所有内容
    p.write_text(head + curve_table(start), encoding="utf-8")
    print(f"[subline] updated {name} -> chapters {start}-{start+24}")

# 2) 同步 outline.md 表格的章节分配列（行首 | S0X | 锚定，仅改表格行）
alloc = {
    "S01": "铺垫 1-8 · 冲突 9-18 · 高潮 19-24 · 舒缓 25",
    "S02": "铺垫 26-33 · 冲突 34-43 · 高潮 44-49 · 舒缓 50",
    "S03": "铺垫 51-58 · 冲突 59-68 · 高潮 69-74 · 舒缓 75",
    "S04": "铺垫 76-83 · 冲突 84-93 · 高潮 94-99 · 舒缓 100",
    "S05": "铺垫 101-108 · 冲突 109-118 · 高潮 119-124 · 舒缓 125",
}
op = BASE / "outline.md"
ol = op.read_text(encoding="utf-8")
lines = ol.splitlines()
new_lines = []
for ln in lines:
    m = re.match(r"(\|\s*(S0[1-5])\s*\|)", ln)
    if m:
        sid = m.group(2)
        # 替换第四个「| ... |」单元格（章节分配列）：即行内第 4 段
        # 格式: | S0X | 名称 | 目标 | 分配 | 关系 | 角色 |
        parts = ln.split("|")
        # parts[0] 空, 1=S0X, 2=名称, 3=目标, 4=分配, 5=关系, 6=角色, 7=空
        if len(parts) >= 7:
            parts[4] = " " + alloc[sid] + " "
            ln = "|".join(parts)
    new_lines.append(ln)
op.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
print("[outline] updated chapter allocation columns")
print("DONE")
