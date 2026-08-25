# 小说创作团 (Novel Writing Team)

多智能体长篇小说创作专家团。把一个「写小说」任务拆成三个协作角色，由一个主理人动态编排，循环推进主线、产出正文、审查修订，避免重复与设定漂移。

## 类型

Team 型（多角色协作团队）

## 角色构成

| 成员 ID | 名字 | 职责 |
|---------|------|------|
| novel-writing-team-lead | 顾执衡（创作总监） | 编排调度，按当前创作状态动态决定调用谁 |
| novel-outline | 苏谋远（大纲架构师） | 世界观/主线推进、章节规划、伏笔埋设与回收 |
| novel-writer | 沈砚行（正文执笔） | 按大纲产出章节，内置防开场重演 |
| novel-critic | 严镜明（审稿主编） | 查矛盾/设定漂移/重演，给出可执行的修订指令 |

## 动态协作流程（自适应写章循环）

1. **主理人判定**：读 state.json，判断压力阶段（铺垫/冲突/高潮/舒缓）、主线进度、伏笔回收率、连续失败数。
2. **大纲推进（按需）**：新书或主线空白时，调大纲架构师产出未来 3–5 章的「章节计划」。
3. **正文执笔（每章）**：把「上一章结尾摘要 + 角色状态白名单」注入正文执笔，避免开场重演；若有 NovelAgent 工作区可跑 `python -m agent.cli write -d <workspace> --mode auto --strict-review`。
4. **审稿修订（每章，可循环）**：审稿主编审查，P0/P1 打回重改（≤3 次），P2 通过。
5. **周期维护**：每满 5 章 reindex，每满 3 章 adjust-relation；自停条件满足时标记 COMPLETE。

## 内置经验（继承自《我在长安开殡仪馆那些年》164 章实战）

- **防开场重演**：write 前必须注入上一章结尾摘要，否则 LLM 会退化为「灵堂的烛火…」等固化模板（已观察到 17 章一字重复）。
- **防设定漂移**：角色状态白名单（生死/关系/时间线/死因/伏笔）随章节计划下发给执笔。
- **frontmatter 防回卷**：终局段若被标成「铺垫/N01」需纠正为「舒缓/N03」。
- **提交安全**：小说仓若开启 commit.gpgsign，gpg 无缓存时 `git commit` 会挂起，需 `--no-gpg-sign`。

## 使用示例

- 「开始创作一部新小说（先搭世界观与主线）」
- 「继续续写，把主线推进到第 X 章」
- 「检查最新章节有没有不合理或矛盾的地方」

## 头像

头像已自动生成在 `avatars/` 目录下占位（.gitkeep）。如需替换为自定义头像，要求：PNG/JPG、512×512、≤500KB，命名对应成员 ID（team.png / novel-writing-team-lead.png / novel-outline.png / novel-writer.png / novel-critic.png）。

## 安装 / 激活

专家目录：`~/.workbuddy/plugins/marketplaces/my-experts/plugins/novel-writing-team/`
已通过 `register_expert.py` 注册，可在「专家」中心直接开启对话使用。
