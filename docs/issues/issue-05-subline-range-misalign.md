# 问题 #05：支线章节范围与大纲 / 驱动计划错位

| 项 | 内容 |
|---|---|
| 分类 | 小说生成 / 规划 |
| 严重度 | 中（影响章节分配与压力曲线展示，不阻断生成，但造成元数据失真） |
| 状态 | 已解决（`scripts/_fix_subline_ranges.py` 一次性修复） |
| 关联文件 | `scripts/_fix_subline_ranges.py`、`projects/jipin-yixian/sublines/*/subline.md`、`projects/jipin-yixian/outline.md`、`drivers/_write_driver_jipin.py`（`PER_ARC_LIST`） |
| 证据 | `_fix_subline_ranges.py` 的 docstring 与代码：重写 5 条支线的「剧集压力曲线」表并同步 `outline.md` 章节分配列 |

## 1. 问题描述
小说的 5 条支线（S01–S05）在**三个地方**各自记录了「本章线覆盖哪些章节」：
1. 每条支线的 `subline.md` 里的「剧集压力曲线」表（起止章）；
2. `outline.md` 的总表「章节分配」列；
3. 驱动脚本 `_write_driver_jipin.py` 的 `PER_ARC_LIST = [25, 17, 2, 2, 3]`（实际驱动用的章节数）。

最初大纲按 **5 支线 × 25 章 = 125 章** 设计，但执行时压缩为约 50 章。结果 `subline.md` / `outline.md` 仍写着旧的 125 章分配，与驱动实际推进（S01:1–25、S02:26–42…）**错位**，导致 Dashboard / 大纲展示的章节范围失真。

## 2. 现象 / 证据
`_fix_subline_ranges.py` 注释明确：
> 修复 jipin 各支线章节范围：对齐驱动脚本的 5支线×25章(共125章)方案。重写每个 subline.md 的「剧集压力曲线」表格，并同步 outline.md 表格的章节分配列。

代码中 `MAPPING` 把每条支线重映射为连续 25 章（1 / 26 / 51 / 76 / 101），并改写 `outline.md` 中 `| S0X | ... |` 行的「章节分配」单元格。

## 3. 根因
- **同一事实（支线章节范围）存在多份拷贝**（subline.md、outline.md、驱动常量），修改规划时只改了一处，其余沦为过期真相。
- 驱动参数 `PER_ARC_LIST` 与大纲文本没有联动，纯靠人脑保持一致。

## 4. 解决方案（当前）
写一次性修复脚本 `scripts/_fix_subline_ranges.py`：用 `MAPPING` 重算每条支线起点，重写 `subline.md` 的「剧集压力曲线」段，并用正则替换 `outline.md` 表格的「章节分配」列，使三者重新对齐。

## 5. 影响
- 修复前：Dashboard 与大纲显示的章节区间与实际成书不符（元数据失真，但不影响正文）。
- 修复后：三者一致。该脚本**只适用于本次 125→压缩方案的特定映射**，通用性低（见 SCRIPTS_USAGE.md）。

## 6. 改进建议
- **单一来源（SSOT）**：支线章节范围应由状态机（`state.json` 的 `progress`）自动生成到 `subline.md` / `outline.md`，不要再手写双份。
- **驱动读大纲而非硬编码**：`PER_ARC_LIST` 这类规划应从 `outline.md` / 状态机读取，删除代码里的第二份真相。
- **CI 校验**：加一个检查，生成前后对比「驱动计划章节数」与「大纲文本章节数」，不一致就报错。

## 7. 复现 / 验证
```bash
# 在小说项目根目录运行（projects/jipin-yixian 相对路径）
cd D:/project/NovelAgent/小说
PYTHONPATH=D:/project/NovelAgent/agent/src python D:/project/NovelAgent/agent/scripts/_fix_subline_ranges.py
# 验证：检查 outline.md 的章节分配列与 subline.md 的压力曲线表是否已更新
```
