#!/bin/sh
# 安装 NovelAgent git 钩子（架构红线提交关卡）
#
# 用法（在 agent/ 目录下）：
#     bash scripts/install_hooks.sh
#
# 背景：项目根的 AGENTS.md 第 10 条要求「提交前必须过架构红线」。
# 钩子文件放在 scripts/githooks/（受版本控制），安装时复制到 .git/hooks/（不受控）。
# ⚠ 换机器 / 重新 clone 后必须重跑本脚本。
set -eu

ROOT=$(git rev-parse --show-toplevel)
SRC="$ROOT/scripts/githooks"

if [ ! -d "$SRC" ]; then
    echo "✗ 找不到 $SRC —— 请在 agent/ 仓根目录下运行。" >&2
    exit 1
fi

# 尊重 core.hooksPath（若已配置则装到那里）
HOOKDIR=$(git rev-parse --git-path hooks)
case "$HOOKDIR" in
    /*) ;;
    [A-Za-z]:*) ;;
    *) HOOKDIR="$ROOT/$HOOKDIR" ;;
esac

mkdir -p "$HOOKDIR"

for h in pre-commit pre-push; do
    cp "$SRC/$h" "$HOOKDIR/$h"
    chmod +x "$HOOKDIR/$h"
    echo "  已安装 $HOOKDIR/$h"
done

echo ""
echo "✓ 钩子安装完成："
echo "    pre-commit → pytest tests/architecture （秒级：架构红线）"
echo "    pre-push   → scripts/full_regression.py（分钟级：全量，**证据型判定**）"
echo ""
echo "  关卡语义（2026-09-20 升级）：pre-push 不只看退出码，而是 ① 必须有 pytest"
echo "  汇总行 ② failed/errors=0 ③ 通过数不低于基线（可选）④ 结论绑 commit"
echo "  ⑤ 原始输出落盘 .git/novelagent-regression/last_run.txt（便于归因）。"
echo ""
echo "  跳过方式（显性，须在当日日志记明原因）："
echo "    git commit --no-verify              # 跳过整钩（含架构红线）"
echo "    NOVELAGENT_SKIP_ARCH=1 git commit   # 仅跳过 pre-commit 的架构红线"
echo "    NOVELAGENT_SKIP_FULL=1 git push"
echo "  解释器不对时：export NOVELAGENT_PYTHON=/d/env/python/python.exe"
