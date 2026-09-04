# AGENTS.md - llmagent_tests/ 编排内核测试

## 职责

llmagent 编排内核的测试套件。

## 测试文件

| 文件 | 测试范围 |
|------|----------|
| `test_gateway.py` | Gateway 网关 |
| `test_integration.py` | 集成测试 |
| `test_kernel.py` | 核心运行时 |
| `test_m1.py` | M1 阶段 |
| `test_m2.py` | M2 阶段 |
| `test_m3.py` | M3 阶段 |

## 运行方式

```bash
python -m pytest llmagent_tests/ -q --tb=short
```