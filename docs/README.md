# NovelAgent 代码仓库文档索引

本目录（`agent/docs/`）归档 NovelAgent 代码仓库的**过程文档**，与并列仓库 `../项目文档/`（中文设计文档）互补。

## 目录结构
```
agent/docs/
├── README.md                 # 本文件：文档总索引
├── SCRIPTS_USAGE.md          # drivers/ 与 scripts/ 下 4 个脚本的用法、可复用性、后续使用
└── issues/                   # 问题归档（一个问题一个文档）
    ├── README.md             # 问题清单 + 改进建议总览（先看这个）
    ├── issue-01-glm-rate-limit.md
    ├── issue-02-stale-draft-safe-delete.md
    ├── issue-03-sandbox-timeout-batch-resume.md
    ├── issue-04-prev-validation-world-conflict.md
    ├── issue-05-subline-range-misalign.md
    ├── issue-06-package-empty-wheel.md
    ├── issue-07-editable-install-no-isolation.md
    ├── issue-08-dotenv-discovery.md
    ├── issue-09-git-bash-path-conversion.md
    ├── issue-10-git-push-hang-gcm.md
    ├── issue-11-three-repo-split.md
    ├── issue-12-stale-doc-links.md
    └── issue-13-project-path-drift.md
```

## 阅读指引
- **想看「生成极品医仙踩了哪些坑 / 怎么改更好」** → 先读 `issues/README.md`（含改进建议总览），再按需点开单篇。
- **想复用我写的驱动 / 修复 / 自检脚本** → 读 `SCRIPTS_USAGE.md`（含可复用性评级与后续使用步骤）。
- **想了解代码本身怎么装怎么跑** → 读仓库根 `README.md` 与 `../项目文档/`。

## 背景
这些文档是在「生成《极品医仙归来：前女友跪求复合》（51 章 / 约 17.6 万字，已完结）+ 将项目重构为 `agent/` `项目文档/` `小说/` 三并列仓库并推送到 gitcode」之后，按用户要求归档的全过程问题记录与脚本说明。
