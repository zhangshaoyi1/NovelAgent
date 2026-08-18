# 通用写作驱动使用指南（generic_writer.py + driver_config.toml）

`drivers/generic_writer.py` 由《极品医仙》正式驱动重构而来，**所有小说相关参数都已抽到配置文件**。你不需要再改代码，复制一份配置、改几个值，就能驱动下一本小说。

---

## 1. 目录里的文件

| 文件 | 作用 |
|---|---|
| `generic_writer.py` | 通用驱动主程序（配置驱动，无需改动） |
| `driver_config.toml` | 配置文件（默认填的是《极品医仙》的值，兼作模板） |
| `README.md` | 本说明 |
| `_write_driver.py` | 旧的「深井回廊」一次性模板（保留参考，不推荐再用） |

---

## 2. 直接复现《极品医仙》行为

```bash
cd D:/project/NovelAgent/agent
D:/env/python/python.exe drivers/generic_writer.py
```
不传 `--config` 时，默认读取同目录 `driver_config.toml`（极品医仙的值），
并能从旧的 `_driver_log_jipin.json` 断点续写。

---

## 3. 换下一本小说（核心步骤）

### 步骤 A：准备新书的项目目录
新书必须先由 NovelAgent 初始化好（即有 `小说/projects/<新书名>/state.json`、
`outline.md`、`subline.md` 等）。`<新书名>` 就是下面要填的 `name`。

### 步骤 B：复制配置并改名
```bash
cd D:/project/NovelAgent/agent/drivers
cp driver_config.toml driver_config.<新书名>.toml
```
例如新书叫 `sword-epic`：`cp driver_config.toml driver_config.sword-epic.toml`

### 步骤 C：改配置（只改标 ★ 的字段即可）

打开 `driver_config.<新书名>.toml`，重点改这几处：

1. **`[project] name`** ★（必改）
   改成新书目录名，例如 `name = "sword-epic"`。
   驱动会自动拼出项目路径 `小说/projects/sword-epic` 和 `state.json`。

2. **`[paths] log`** ★（强烈建议改）
   改成新书专属日志名，避免误读旧书进度，例如：
   `log = "D:/project/NovelAgent/小说/_driver_log_sword-epic.json"`

3. **支线规划 `[arcs]`** ★（按需改 / 或改用简单模式）
   - 有「卷/支线」概念：把 `ids` 换成新书的支线 ID（与 `state.json` 里 `subline` 对齐），
     `per_arc_chapters` 换成每条支线计划写的章数，**两者长度必须相等**。
     写完最后一条支线即完结。
   - 没有支线概念：直接**删掉整个 `[arcs]` 段**，并在 `[run]` 设 `max_chapters = 80`
     （写满 80 章即完结，简单模式）。

4. **节奏与上限 `[run]`**（按需调）
   - `run_target_chars`：单批写多少字就暂停（分批续写抗超时）。新书体量大可调大。
   - `cooldown_sec`：章节间冷却秒数。换用不限流模型可调小（如 3~5）；仍 429 就调大。
   - `hard_cap_chars`：极端兜底字上限，一般不用动。

5. **`[adjust]`**（一般默认即可）
   `every` 每隔几章做一次关系网/路线演化；`tail_from_arc_index` 从第几弧起切收官语气。

其它字段（`base_dir`、`projects_rel`、`python`、完结导出开关）通常不用动。

### 步骤 D：运行
```bash
D:/env/python/python.exe drivers/generic_writer.py --config driver_config.<新书名>.toml
```

---

## 4. 两种完结模式

| 模式 | 触发条件 | 适用 |
|---|---|---|
| **支线模式**（配置了 `[arcs]`） | 写完 `ids` 里**最后一条支线** | 有明确卷/支线结构的小说 |
| **简单模式**（无 `[arcs]` 且 `max_chapters>0`） | 写到 `max_chapters` 章 | 无卷结构、按总章数收尾 |

若两者都满足（配了 arcs 又设了 max_chapters），**优先按支线完结**，max_chapters 被忽略。

---

## 5. 断点续写说明

日志（`[paths] log`）里记录了 `total_chars / chapters / arc_index / records`。
脚本每次启动都会读它：
- 自动从 `arc_index` 恢复当前支线，并写回 `state.json`（**不会**硬重置回第一条支线）；
- 累计字数达到 `run_target_chars` 会**正常退出**，你再次运行即从断点继续；
- 中途崩溃/限流耗尽也只是中止本批，重跑即可，**不会重复写已完成的章节**。

> 因此「换书」时务必给新书一个**独立的 log 文件名**，否则会误读旧书进度。

---

## 6. 常见问题

- **`tomllib` 报错**：需要 Python 3.11+。用配置文件里 `[paths] python` 指定的解释器，
  或系统时确保 ≥3.11。
- **`[arcs].ids` 与 `per_arc_chapters` 长度不一致**：启动即报错退出，对齐长度即可。
- **仍然 429 限流**：调大 `[run] cooldown_sec`，或确认模型的 RPM 额度。
- **草稿卡死 / `draft.wip` 删不掉**：脚本已内置 `os.remove` 直删残留草稿（不依赖回收站），
  沙箱环境也能跑；若仍报错，手动删 `小说/projects/<书名>/.state/draft.wip*` 再重跑。
