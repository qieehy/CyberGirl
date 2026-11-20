# Frontend

原生 HTML、CSS、JavaScript ES Modules。开发和构建使用 Node.js 内置模块，无第三方 npm 依赖；Node.js 要求22或更新版本。

从工作区根目录执行：

```powershell
npm --prefix frontend run dev
npm --prefix frontend run check
npm --prefix frontend run test
npm --prefix frontend run build
```

开发服务默认 `http://127.0.0.1:5173`，将 `/api/*` 代理到 `http://127.0.0.1:8765`。后端需单独启动；把后端打印链接的 `#auth=...` 片段放到前端链接末尾完成认证。令牌不写入源码或构建文件。

- 普通私聊：前端 `/` 或 `/workbench`。
- 离线沙盒：前端 `/sandbox`，后端使用 `wechat-agent console`。
- 群缓存：前端 `/history`，连接提供群历史 API 的后端。

自定义端口或后端：

```powershell
npm --prefix frontend run dev -- --port 5174 --backend http://127.0.0.1:8765
```

修改源码后刷新页面；当前没有热更新或框架编译步骤。开发代理只连接本机后端，不开启跨域通配。

| 目录 | 职责 |
| --- | --- |
| `src/pages/` | 工作台、沙盒、历史页面 |
| `src/shared/api.js` | 唯一的认证令牌读取与 JSON API 客户端 |
| `src/shared/theme.js` | 日夜／跟随系统，首屏应用主题 |
| `src/workbench/` | 工作台与管理模块；`entry.js` 为入口 |
| `src/sandbox/`、`src/history/` | 沙盒与历史页面行为 |
| `src/styles/` | 公共样式、控制台样式 |
| `tools/` | 本机开发代理、构建、语法检查 |
| `tests/` | Node 测试及实际页面处理器冒烟 |
| `dist/` | 构建产物，不手工编辑 |

构建同时输出 `frontend/dist/` 和后端包的 `assets/web/`，生成带 MIME 与 SHA-256 的资源清单；只将清单列出的文件提供给浏览器。生产环境无需 Node.js，Python 服务直接提供这些资源和 API。
