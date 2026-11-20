# H1 实现与复现记录

日期：2026-10-26（Asia/Shanghai）。对应 [Hermes Codex 路线 H1](HERMES_CODEX_PLAN.md)。本切片交付独立包、框架依赖安装、文本 provider、异步 HTTP/SSE 和可运行的本机演示。

本文件及 H1 验收 JSON 保留 H1 完成时的行为与代码指纹。后续 H2 已新增认证命令及凭据生命周期，当前实现见 [H2 记录](H2_IMPLEMENTATION.md)；历史 H1 指纹不作为更新后源码的当前哈希。

## 1. 已实现的链路

```text
wechat-agent demo
  → CodexTextProvider(messages, tools=None)
  → 固定 SyntheticSession + loopback HTTP 服务
  → 原始 HTTP 分片 → SSE 帧 → JSON 事件 → 完整终止校验
  → 已安装 AI_agent 的 llm.models.LLMResult
```

`demo` 是可执行的本机传输演示，固定使用合成消息、模型名和假凭据。它没有登录入口，也没有调用真实订阅或微信。完整 Agent Runtime 的组合属于 H3；不能把这里导入/返回框架类型描述成完整 Agent 已运行。

| 实现位置 | 行为 |
| --- | --- |
| `src/wechat_agent/providers/codex/provider.py` | 与框架 callable 合同匹配；文本输入校验；无工具；单请求执行、最多 4 个执行/等待请求；队列和凭据等待都计入总时限 |
| `transport.py` | HTTPX 异步客户端；固定官方 HTTPS 地址；显式 loopback 测试模式；无重定向、无自动重试；拒绝不支持的内容编码；关闭响应和连接池 |
| `sse.py` | 直接消费原始字节；支持 UTF-8 跨分片、BOM、CR/LF/CRLF、注释、多行 data；限制行/帧/流大小；拒绝坏 JSON、重复 JSON 键及非有限数字 |
| `response.py` | 显式成功终止后返回；区分最终正文、commentary/reasoning；支持 item.done + null output；拒绝工具、断流、身份错配、不完整结果和冲突的完成条目 |
| `errors.py` / `config.py` | 稳定错误分类、有限 HTTP 元数据；受保护的凭据 repr；固定假凭据不能替换成真实值后发给测试服务 |
| `demo.py` / `cli.py` | 安装后的命令入口通过真实 loopback HTTP 调用生产 provider；UTF-8 JSON 展示结果 |

provider 持有自己的请求任务。调用者取消会取消所等待的请求；`aclose` 停止接纳、取消执行/排队任务、等待流清理并关闭客户端，不等待调用者自己的后续恢复流程。多个关闭等待者共享清理任务，其中一个等待者取消不会取消实际清理；应用退出仍应等待 `aclose` 完成。

## 2. 框架打包问题与处理

观察到 AI_agent `0.3.0` 的 package discovery 只列出 llm/memory/tools/runtime/config/errors/rag。`llm.__init__ → llm.client` 同时导入 `observability`；原配置未包含该包。框架还具有独立的 `prompts` 包。

`scripts/build_framework.py` 只读取原框架 Python 包及公开项目元数据，在本项目 `.build` 中建立构建暂存区；仅给暂存的包发现列表增加 `observability*` 和 `prompts*`，构建普通 wheel，再安装到本项目 `.venv`。生产导入来自 site-packages，不使用临时 sys.path，也不依赖框架原有虚拟环境的旧 editable 安装。

这是一份**本项目记录过的打包修正版 wheel**，不是未经修改的上游打包产物。未修改原框架源码/配置；未读取其 `.env`、认证缓存或运行数据。未调用原框架 CLI。

- Python：本机实际使用 CPython 3.12.9 / Windows x64；本项目声明 Python ≥3.11，当前依赖锁仅验证 Windows CPython 3.12。
- `framework.lock.json` 记录 63 份框架源文件的 SHA-256、构建工具版本及打包修正。
- 源文件集合指纹：`5af9d2875abca7598e2539af1e281f9271d8cb51f482c0b5b109f335ba9b02cc`。
- 构建 wheel SHA-256：`29f4efabbe67294d4c575a393d9088be145124f466470d15bf97bdb56130837e`。
- `requirements.lock` 固定 49 个依赖包/本地 wheel 的版本及实际下载 SHA-256；包含框架声明的依赖，不表示已启用这些依赖承载的工具。
- `requirements-build.lock` 固定最初构建所需 setuptools/wheel/packaging，使首次安装不依赖尚未存在的框架 wheel。

未来若原框架补齐打包配置，应审查新版本后撤掉暂存修正并更新指纹；不能静默使用发生变化的本地源码。当前 wheel 仅满足本切片所验证的包导入；框架原 CLI、可选 RAG/MCP 功能和打包资源未在 H1 验收。

## 3. 使用与环境复现

现有环境直接运行（PowerShell，项目根目录）：

```powershell
& '.\.venv\Scripts\wechat-agent.exe' demo
```

输出为 UTF-8 JSON，应包含 `mode: loopback-synthetic`、`framework_result: llm.models.LLMResult`、`http_requests: 1` 及“H1 本机传输验证成功。”正文。这里的响应和 request ID 都是合成值。

在相同 Windows/Python 平台重建环境：

```powershell
py -3.12 -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install --require-hashes --only-binary=:all: --index-url https://pypi.org/simple -r requirements-build.lock
& '.\.venv\Scripts\python.exe' -B scripts/build_framework.py 'D:\qieehy\AI_agent'
& '.\.venv\Scripts\python.exe' -m pip install --require-hashes --only-binary=:all: --index-url https://pypi.org/simple -r requirements.lock
& '.\.venv\Scripts\python.exe' -m pip install --no-deps --no-build-isolation --no-index -e .
& '.\.venv\Scripts\wechat-agent.exe' demo
```

框架路径与快照必须匹配 `framework.lock.json`；不同平台应重新解析并验收依赖。`scripts/lock_dependencies.py` 用于**有意更新**依赖锁，会从 PyPI 解析当前依赖并改写锁文件，不是日常安装步骤。应用 editable 安装使用已锁定的构建工具，不进行额外依赖解析。

验证命令：

```powershell
& '.\.venv\Scripts\python.exe' -B -m pytest -q -o faulthandler_timeout=30 --junitxml=.build/h1-tests.xml
& '.\.venv\Scripts\python.exe' -B -m ruff check src tests scripts
& '.\.venv\Scripts\python.exe' -B -m pip check
```

Windows 的默认受限沙箱曾阻止 ensurepip 子进程、pip 临时目录和 asyncio socketpair；本次为建环境/本机测试使用相应执行权限后完成。测试网络仅为本机随机端口；依赖获取访问 PyPI。HTTP 集成测试使用真实连接，短时限需覆盖 Windows 建连开销，不能把“尚未建连”混作“已建连后的关闭通过”。

## 4. 验收证据

最终测试统计及文件指纹见 [H1 验收记录](evidence/h1-result.json)。生成的完整 JUnit 位于 `.build/h1-tests.xml`，属于可重新生成的本地产物。

最终观察结果：**43 项测试通过，814 个子用例通过，0 失败/错误/跳过**；pytest 报告耗时 71.72 秒，JUnit 的 suite 时间为 71.467 秒（统计边界不同）。Ruff 与 pip check 均通过。框架 wheel 重建 SHA-256 与锁文件一致，63 份原始文件指纹一致。

覆盖范围：请求角色与工具边界；逐字节/逐切点的 UTF-8 和 SSE；完整终止、失败/不完整/EOF、异常用量、commentary/reasoning 与工具过滤；真实 chunked HTTP、401/403/429/503、重定向不转发、媒体类型/压缩拒绝；读超时、总时限、心跳不延长总时限、凭据/排队等待、调用取消、取消后重用、关闭任务及调用者恢复；框架已安装文件指纹、Settings 未构造和实际 CLI 输出。

H1 没有真实登录、模型目录、token 保护/刷新、真实订阅调用、完整 Runtime、微信或群场景的运行证据。这些分别在 H2/H3/H4 与后续微信整合阶段验收。HTTP connect/write/pool 超时使用 HTTPX 配置，已验证实际读超时和请求总时限，未分别制造真实 connect/write/pool 卡死。

对 Codex 响应的首版兼容限制仍保留：只有 delta、没有规范完成条目、终止 output 也为空的流会被拒绝；未知事件返回协议兼容错误。真实 Codex 后端兼容性由 H4 证明，不由 loopback 演示推定。

协议依据：[WHATWG SSE](https://html.spec.whatwg.org/multipage/server-sent-events.html#event-stream-interpretation)、[HTTPX async](https://www.python-httpx.org/async/)、[HTTPX timeouts](https://www.python-httpx.org/advanced/timeouts/)、[OpenAI streaming](https://developers.openai.com/api/docs/guides/streaming-responses)。Codex 专有合同仍以[固定 Hermes 源码研究](HERMES_CODEX_RESEARCH.md)为依据，公共 API 文档不替代其认证/端点合同。
