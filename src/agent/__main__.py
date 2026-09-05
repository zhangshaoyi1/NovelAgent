"""支持 python -m agent 调用"""

# 运行期 import 红线（T2）：业务层 import provider SDK 直接拦截。
# 可用 LLMAGENT_IMPORT_GUARD=0 关闭（调试用）。
try:
    from llmagent.kernel.import_guard import install_import_guard

    install_import_guard()
except Exception:  # noqa: BLE001 - 守卫安装失败不阻断 CLI 启动
    pass

from agent.cli import app  # noqa: E402

if __name__ == "__main__":
    app()
