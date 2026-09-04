# AGENTS.md - llmagent/gateway/ 模型调用网关

## 职责

★ **唯一允许 import provider SDK 的目录**。
★ **唯一读取 API key 的模块**（gateway/secrets.py）。

`Gateway.chat()` 是全系统唯一的 LLM 出口。

## 核心模块

| 文件 | 作用 |
|------|------|
| `chat.py` | `Gateway` / `GatewayError` 主类 |
| `models.py` | 模型配置与类型 |
| `packer.py` | 请求打包器 |
| `rate_limiter.py` | 限流 |
| `request_gate.py` | 请求门槛 |
| `response_gate.py` | 响应处理 |
| `router.py` | 模型路由 |
| `secrets.py` | API key 读取 |
| `providers/registry.py` | Provider 注册表 |

## 依赖规则

- 不依赖 `kernel/` 业务模块
- 只依赖标准库和第三方 SDK