# 通用写作驱动 · 使用说明书（小白版）

> 看不懂代码也没关系。这份说明只要求你会：打开文件夹、复制粘贴几行命令、用记事本改几个字。
> 跟着一步一步做就行。

---

## 〇、这东西到底是干什么的？

一句话：**它是一个"自动写小说"的遥控器。**

你只要告诉它"写哪一本书、写到第几卷算完"，它就会一段一段地调用 AI 把小说写下去，
写到一定程度会自动停下来（这是正常的"分批"，不是出错），你再点一下它就接着写，
直到全书写完，还会自动帮你导出成书文件。

它配套 3 个文件（都在 `D:/project/NovelAgent/agent/drivers/` 这个文件夹里）：

| 文件 | 是什么 | 你要动吗 |
|---|---|---|
| `generic_writer.py` | 遥控器本体（程序） | ❌ 不用碰 |
| `driver_config.toml` | 设置单（写哪本书、写多长，都在这改） | ✅ 换书时要改 |
| `README.md` | 就是本说明 | ❌ 不用碰 |

核心思想：**写不同的书，不需要改程序，只需要复制一份"设置单"、改几个字。**

---

## 一、开始前，先确认这 4 件事准备好了

### ① 电脑装了 Python 3.11 或更高
- **怎么查**：按键盘 `Win + R` → 输入 `cmd` → 回车，会弹出一个黑窗口。
  在里面输入 `python --version` 回车。
  - 如果显示 `Python 3.11.x` 或 `Python 3.12`、`3.13` → 说明装好了，过关。
  - 如果显示"不是内部或外部命令" → 没装，去 https://www.python.org 下载安装，
    **安装时一定记得勾选 "Add python.exe to PATH"**（非常重要，否则后面跑不起来）。

### ② NovelAgent 代码已经在电脑上
- 确认文件夹 `D:/project/NovelAgent` 存在，里面有个 `agent/` 文件夹。
- （这是之前已经搭好的，正常情况你不用管。）

### ③ 新书已经在 NovelAgent 里"建好项目"
- 也就是说，在 `小说/projects/` 下面，已经有一个**以你新书命名**的文件夹，
  里面至少要有这几个文件：`state.json`、大纲 `outline.md`、分卷 `subline.md`。
- 这一步是 NovelAgent 本身的功能（相当于先把"空白稿纸"准备好）。
  新书目录名建议用英文/拼音、不要有空格和中文，例如 `sword-epic`。
- **如果这步没做，下面的步骤会报错。** 先把新书在 NovelAgent 里初始化好。

### ④ AI 的"钥匙"（API Key）已经配好
- 在 `agent/` 文件夹里，要有一个叫 `.env` 的文件，里面写了你的 AI 账号密钥。
- 没配好，AI 就不会动笔。

> **不确定 ③ ④ 做好了没？** 先跑一次"体检"：
> ```bash
> cd /d D:/project/NovelAgent/agent
> D:/env/python/python.exe scripts/_smoke_test_jipin.py
> ```
> 屏幕输出里有 `SMOKE_OK` 就说明 AI 链路通了；有 `SMOKE_ERR` 就先去检查 `.env` 和账号。

---

## 二、第一次直接用（继续/重跑《极品医仙》，零配置）

如果你就是要跑《极品医仙》，**什么都不用改**，直接：

1. 打开终端（按 `Win+R` → 输入 `cmd` → 回车）。
2. **整段复制下面 2 行**，粘贴到黑窗口里，按回车：
   ```bash
>   cd /d D:/project/NovelAgent/agent
>   D:/env/python/python.exe drivers/generic_writer.py
>   ```
3. 屏幕会一行行刷出 `章节 X | 累计 Y 字 | 当前支线 Z`，这就是正在写，正常。
4. 跑到一定字数它会**自己停**（这是"分批"，为了不卡死，不是出错）。
   **再跑一次上面完全相同的命令，就会从断点接着写**，一直到全书完结、自动导出。

---

## 三、换下一本书（重点，一步一步来）

假设你新书的目录名叫 `sword-epic`（请换成你自己的名字，用英文/拼音）。

### 第 1 步：复制一份"设置单"
进到 `D:/project/NovelAgent/agent/drivers/` 文件夹，把 `driver_config.toml`
**复制一份、改名**成 `driver_config.sword-epic.toml`。

- 会用命令的话：
  ```bash
  cd /d D:/project/NovelAgent/agent/drivers
  copy driver_config.toml driver_config.sword-epic.toml
  ```
- 不会用命令也行：在文件夹里右键 `driver_config.toml` → 复制 → 粘贴 → 把新文件重命名。

> 为什么要复制？因为原本那份是《极品医仙》的设置，留着不动；新书用新的一份，互不干扰。

### 第 2 步：用记事本打开新文件，改 3 个地方
右键 `driver_config.sword-epic.toml` → 打开方式 → **记事本**。
文件里有很多带 `#` 的说明文字，不用管，只改下面这 3 处（改的时候**只改引号里面的内容**，别删掉引号和其他符号）：

**(1) 书名（必改）** —— 找到这一行，把引号里换成你的目录名：
```toml
name = "sword-epic"
```

**(2) 日志文件名（强烈建议改）** —— 找到 `[paths]` 下面这行，换成新书专属的名字，
目的是不和旧书的进度搞混：
```toml
log = "D:/project/NovelAgent/小说/_driver_log_sword-epic.json"
```

**(3) 分卷规划（二选一，按你书的情况挑一种）**

- **情况 A：你的书有明确的"卷 / 支线"**（比如第一卷复仇、第二卷争霸、第三卷终局）：
  改 `[arcs]` 里的两行。`ids` 是每条支线的名字（要和你的 `subline.md` 里写的一致），
  `per_arc_chapters` 是每卷计划写几章。**这两个列表的个数必须一样多。**
  ```toml
  ids = ["V1_复仇", "V2_争霸", "V3_终局"]
  per_arc_chapters = [30, 30, 20]
  ```
  → 写完最后一条（V3）就算全书完结。

- **情况 B：你的书没有分卷，就想"写满 N 章就停"**：
  把整个 `[arcs]` 那一段（从 `[arcs]` 到它下面所有带 `ids`/`per_arc_chapters` 的行）**整段删掉**，
  然后在 `[run]` 那段里加一行：
  ```toml
  max_chapters = 80
  ```
  → 写到 80 章自动完结。

> 剩下的像 `cooldown_sec`（每章之间停几秒，防止 AI 限流）、`run_target_chars`（每批写多少字暂停）
> 一般**不用动**。等真遇到限流再回来调大。

改完之后，**保存**记事本（Ctrl+S）。

### 第 3 步：开写
回到终端，粘贴运行（记得把 `sword-epic` 换成你自己的名字）：
```bash
cd /d D:/project/NovelAgent/agent
D:/env/python/python.exe drivers/generic_writer.py --config drivers/driver_config.sword-epic.toml
```
屏幕上刷出 `章节 X | 累计 Y 字` 就说明成功开写了。🎉

---

## 四、平时怎么用（暂停 / 继续 / 看进度）

- **想暂停**：直接关掉那个黑窗口就行。它每写一章都会自动保存进度，不会丢。
- **想继续**：再跑一次**完全一样的那条命令**，它会从断点接着写，**不会重复、也不会从头**。
- **想看写到第几章了**：打开 `小说/_driver_log_sword-epic.json`（就是你第 2 步设的 `log` 那个文件），
  里面 `chapters` = 已写章数，`total_chars` = 累计字数。
- **全书完结**：屏幕会打印"小说已完结"，然后自动导出 TXT 成书 + 生成可视化面板（dashboard）。

---

## 五、小白常见问题（出错了对号入座）

**Q：一运行就报错，说 `tomllib` 或要 Python 3.11？**
A：你的 Python 太旧了。要么去装 3.11+；要么把 `driver_config.toml` 里
`[paths] python =` 那行，改成你新装 Python 的完整路径（例如 `C:/Python311/python.exe`）。

**Q：报错"配置文件不存在"？**
A：`--config` 后面跟的文件名拼错了，或者那份文件不在 `drivers/` 里。仔细核对文件名。

**Q：不停地出现 `429` / 写着写着报错"限流"？**
A：你的 AI 账号调用太快、额度不够。把设置里 `cooldown_sec` 调大（比如 60、90），或者先关掉歇会儿再跑。

**Q：卡住不动，或提示 `draft.wip` 相关错误？**
A：上一章写到一半崩了，留下一个"死草稿"挡路。程序本来会自动删它；万一还报错，
就手动去 `小说/projects/<你的书名>/.state/` 这个隐藏文件夹里，把 `draft.wip` 和 `draft.wip.bak`
两个文件删掉，再重跑命令。

**Q：我想丢掉旧进度、从头重写一本书？**
A：把你对应的 `小说/_driver_log_<书名>.json` 删掉，再跑就会从第 1 章开始。
（注意：这只丢"写到第几章"的记录，已经写好的章节文件还在磁盘上。）

**Q：`cd /d ...` 那行报错"系统找不到指定的路径"？**
A：说明你的 NovelAgent 不在 `D:/project/NovelAgent` 这个位置。去"此电脑"里找找它实际在哪，
把命令里的路径改成你电脑上的真实路径即可。

---

## 六、进阶参考（看懂前面就不用看这节）

### 两种"完结"模式
| 模式 | 触发条件 | 适用 |
|---|---|---|
| **支线模式**（配置了 `[arcs]`） | 写完 `ids` 里**最后一条支线** | 有明确卷/支线结构的小说 |
| **简单模式**（无 `[arcs]` 且 `max_chapters>0`） | 写到 `max_chapters` 章 | 无卷结构、按总章数收尾 |

若两者都满足（既配了 arcs 又设了 max_chapters），**优先按支线完结**，max_chapters 被忽略。

### 断点续写原理
日志（`[paths] log`）里记录了 `total_chars / chapters / arc_index / records`。脚本每次启动都会读它：
- 自动从 `arc_index` 恢复当前支线，并写回 `state.json`（**不会**硬重置回第一条支线）；
- 累计字数达到 `run_target_chars` 会**正常退出**，你再次运行即从断点继续；
- 中途崩溃/限流耗尽也只是中止本批，重跑即可，**不会重复写已完成的章节**。

> 因此「换书」时务必给新书一个**独立的 log 文件名**，否则会误读旧书进度。

### 配置字段速查表
| 配置键 | 含义 | 极品医仙当前值 |
|---|---|---|
| `[project] name` | 小说 projects 目录名 | `jipin-yixian` |
| `[project] base_dir` | 工作区根目录 | `D:/project/NovelAgent` |
| `[project] projects_rel` | 小说仓库内 projects 相对路径 | `小说/projects` |
| `[paths] python` | Python 解释器路径 | `D:/env/python/python.exe` |
| `[paths] log` | 断点 / 运行日志 JSON | `小说/_driver_log_jipin.json` |
| `[run] run_target_chars` | 单批增量上限（抗超时） | `50000` |
| `[run] hard_cap_chars` | 安全硬上限（兜底） | `450000` |
| `[run] cooldown_sec` | 章节间冷却秒（避让限流） | `45` |
| `[run] max_chapters` | 简单模式章节上限（>0 启用） | `0`（走支线模式） |
| `[arcs] ids` / `per_arc_chapters` | 支线 ID 与每弧章节数 | 5 条 / `[25,17,2,2,3]` |
| `[adjust] every` / `tail_from_arc_index` | adjust 频率 / 收官弧索引 | `5` / `3` |
| `[novel_complete] export_txt` / `dashboard` | 完结后是否导出/生成面板 | `true` / `true` |
