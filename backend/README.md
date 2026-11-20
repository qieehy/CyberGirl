# Backend

Python 后端源码位于 `backend/src/wechat_agent/`。工作区的 Python 构建元数据、锁文件和虚拟环境统一保留在根目录，避免出现两套依赖声明。

从工作区根目录安装与运行：

```powershell
npm --prefix frontend run build
.venv/Scripts/python.exe -m pip install --no-deps --no-build-isolation -e .
.venv/Scripts/python.exe -m wechat_agent.cli console
```

最后一条启动离线沙盒，不连接真实微信或模型。原 `console --mode wechat-private ...` 启动参数保持不变；从根目录运行，以继续使用 `.state/` 和原生 worker。

后端可在没有前端构建时提供 API；生产工作台页面需先构建前端。

| 目录 | 职责 |
| --- | --- |
| `web/` | HTTP 生命周期、Bearer／Origin 校验、严格 JSON、构建资源白名单 |
| `workbench/` | 私聊工作台用例、草稿、自动回复、管理接口、陪伴与记忆 |
| `social/` | 人格、会话规则、SQLite inbox/outbox 与投递状态 |
| `providers/codex/` | 当前模型服务协议、登录与凭据生命周期 |
| `bridges/wechat/` | Windows 微信接收和发送适配 |
| `bridges/clawbot/` | 已停用路线的可选适配，默认不启动 |
| `companion_vendor/` | 已署名的上游纯领域模块与许可证 |
| `assets/web/` | 前端构建产物，自动生成，不手工编辑 |

测试在根目录 `tests/`。协议与开发说明见 [架构](../docs/ARCHITECTURE.md) 和 [API](../docs/API.md)。
