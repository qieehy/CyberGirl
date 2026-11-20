# Cybergirl / WeChatAgent

本机微信 AI 私聊工作台：人格与模型管理、指定联系人自动回复、媒体理解、可管理的长期记忆与陪伴、群历史缓存。

当前普通好友方向为 **电脑 Amadues → Cybergirl 回复 qieehy**。主动联系默认关闭；最小化／托盘发送尚未开放。ClawBot路线已停用。当前状态见 [项目进度](docs/CURRENT_STATUS.md)。

## 项目结构

- [frontend/](frontend/README.md)：页面、原生JavaScript ES Modules、CSS、独立开发服务器及前端测试。
- [backend/src/wechat_agent/](backend/README.md)：Python API、应用服务、账本、模型与微信适配。
- `scripts/`：Windows原生worker和诊断工具。
- `tests/`：Python检查；`docs/`：当前文档与历史证据。
- `.state/`：本机数据和加密凭据；`.build/`：临时产物，不发布。

技术栈：**原生HTML/CSS/JavaScript + Node内置开发工具；Python asyncio + httpx + SQLite；ai-agent Runtime；Windows原生微信桥。** 版本和依赖边界见 [架构与技术栈](docs/ARCHITECTURE.md)，接口见 [API约定](docs/API.md)。

## 构建与运行

从项目根目录运行。已有虚拟环境时：

```powershell
npm --prefix frontend run build
.venv/Scripts/python.exe -m pip install --no-deps --no-build-isolation -e .
.venv/Scripts/python.exe -m wechat_agent.cli console
```

最后一条启动离线沙盒，不调用真实模型或发送微信。打开终端打印的完整认证链接。真实私聊继续使用原 `console --mode wechat-private ...` 参数与原账号账本，不通过沙盒启动真实发送。

前后端独立开发：

```powershell
# 后端另开终端，使用上面的 console 命令
npm --prefix frontend run dev
```

开发前端默认5173，API代理到本机8765。沙盒访问`/sandbox`，普通私聊访问`/`；将后端链接中的`#auth=...`片段放到前端链接末尾。源码修改后刷新。生产构建后由Python同时提供静态资源和API，无需运行Node。

首次安装请按根目录依赖锁文件和本机`framework.lock.json`准备Python环境；构建依赖见`requirements-build.lock`，微信读取／媒体依赖见`pyproject.toml`可选组。前端没有第三方npm依赖，要求Node >=22。

## 检查

统一工程入口（从根目录执行，沿用当前虚拟环境）：

```powershell
.venv/Scripts/python.exe scripts/project.py check
.venv/Scripts/python.exe scripts/project.py test
.venv/Scripts/python.exe scripts/project.py build
.venv/Scripts/python.exe scripts/project.py frontend
```

`test`先构建页面再检查前后端，使用合成模型与发送器；这些命令不会启动真实私聊。`frontend`只启动开发前端，后端独立运行。以下命令可分别执行：

```powershell
npm --prefix frontend run check
npm --prefix frontend run test
.venv/Scripts/python.exe -m pytest tests/test_web_assets.py tests/test_console.py tests/test_workbench.py tests/test_managed_companion.py
```

Windows asyncio测试需要允许本机回环套接字。检查使用合成模型／发送器，不要求再次发送测试码。

## 功能说明

- [持续私聊](docs/PRIVATE_AUTO_REPLY.md) · [媒体理解](docs/PRIVATE_MEDIA.md) · [前后台发送](docs/PRIVATE_FOREGROUND_SEND.md)
- [陪伴与长期记忆](docs/MANAGED_COMPANION.md) · [模型切换](docs/PRIVATE_MODEL_SELECTION.md) · [管理界面](docs/STUDIO_MANAGEMENT.md)
- [重构前历史README](docs/history/README_BEFORE_STRUCTURE_20261118.md)

历史记录中的目录、运行快照与能力描述只代表对应阶段；当前布局以本文和架构文档为准。
