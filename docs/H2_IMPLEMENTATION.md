# H2 认证与凭据生命周期：实现和验收

日期：2026-10-26（Asia/Shanghai）。对应 [Hermes Codex 路线 H2](HERMES_CODEX_PLAN.md)。本切片已实现独立设备码登录、Windows 用户级凭据保护、串行刷新、状态查询与本地退出，并以合成认证服务完成验收。

本文件保留 H2 完成时的行为与证据。H3 已补齐完整 Runtime、应用退出组合及一次受控模型 401 恢复，当前状态见 [H3 记录](H3_IMPLEMENTATION.md)；下文的“后续 H3”是历史范围说明。

**H1/H2 整体回归：87 tests / 846 subtests 全部通过，0 失败/错误/跳过；pytest 报告 36.74 秒。** Ruff 与 pip check 均通过。新增 H2 测试为 44 项，H1 的 43 项一起回归。实际记录和代码指纹见 [H2 验收 JSON](evidence/h2-result.json)。

本次没有进行真实账号登录或订阅推理；H2 证明认证实现和本机生命周期，真实服务兼容性在 H4 验收。完整 AI_agent Runtime 组合在 H3 实施。

## 1. 可执行链路

```text
auth login
  → TokenManager 获取本应用凭据存储的独占所有权
  → 写入新登录代际的空会话
  → 请求设备码 → 用户登录界面显示网址/代码
  → 有界轮询 → authorization code + verifier 换取令牌
  → 解析账号路由/到期时间 → DPAPI 加密 → 原子保存
  → 状态 ready；get_session 可注入 H1 provider
```

认证端点、公开 Codex client 配置和资源地址绑定在一起；独立于 SIWC 动态注册/scopes。设备码轮询间隔至少 3 秒，窗口最多 15 分钟；403/404 只在轮询端点解释为等待。429、初始设备码请求的 403、换码错误分别结束当前操作，不进行无界重试。

账号、到期时间及可选 residency 从认证服务返回的 access token claims 提取，用于请求路由和刷新计划；这不是 JWT 签名验证，也不能用于证明微信主人身份。H2 不使用 ID token 作为本地身份凭据。官方端点仍通过 TLS 校验，拒绝任意 issuer、重定向与跨路线凭据复用。

| 文件 | 职责 |
| --- | --- |
| `src/wechat_agent/providers/codex/auth_models.py` | 认证路线绑定、隐藏秘密字段的数据类型、令牌与 claims 的有界解析 |
| `auth.py` | 异步设备码请求/轮询/换码与刷新；HTTP 总时限、响应大小、错误及 Retry-After |
| `protection.py` | Windows 当前用户 DPAPI；禁止交互提示；独占且不继承的文件句柄 |
| `credentials.py` | 版本化凭据状态、应用/路线校验、加密临时文件、fsync 与原子替换 |
| `token_manager.py` | 单一刷新任务、刷新前持久标记、代际校验、暂停、退出与资源生命周期 |
| `cli.py` | `auth login/status/logout`；一次性代码只显示于明确的登录界面，状态/错误不含令牌 |

## 2. 刷新与退出的规则

默认在到期前 120 秒请求刷新。所有等待者共用一个由 TokenManager 持有的任务：一个推理请求取消等待，不会取消已经开始的轮换保存；进程正常关闭也会等待该事务结束。

| 状态/事件 | 行为 |
| --- | --- |
| `active` | 令牌足够新时直接提供会话；需要刷新时建立唯一刷新任务 |
| `refresh_pending` | **先持久写入，再发送刷新 POST**；该标记不保留旧令牌；其他等待者加入同一任务 |
| 刷新成功 | 先原子保存新 pair，再允许返回；未返回新 refresh token 时保留协议允许继续使用的旧值 |
| 429 | 恢复未被消费的旧 pair 并持久暂停；读取 Retry-After 秒数或 HTTP 日期，无有效提示时默认 60 秒；不自动重新登录 |
| 401/403、刷新拒绝 | 丢弃本应用可用令牌，记录需重新登录，停止重放旧 refresh token |
| 超时/断网/坏响应导致刷新结果不明 | 保守记为需重新登录；这是避免重放的恢复策略，不表示已经证实服务端撤销了凭据 |
| 新令牌保存失败 | 当前会话进入存储错误，停止新调用；磁盘仍有 pending 标记，重启不会重放旧令牌 |
| 重启看到 pending | 转为需重新登录；不猜测上一次刷新是否已经消费旧 grant |
| 本地 logout | 更换代际并保存无令牌的退出记录，取消待完成登录；迟到的刷新/登录结果不能恢复会话 |

暂停最迟恢复时机由持久时间表示；当前 Retry-After 支持的最大等待为 24 小时。刷新事务不自动重试，之后的明确调用需先通过暂停/认证状态检查。

本地退出不是服务端授权撤销，CLI 明确返回 `local_only: true`。TokenManager 也不拥有已经取得会话的推理请求；H3 的应用组合需在退出时协调推理 provider 的取消和关闭，不能仅靠删除令牌撤回已提交请求。

## 3. 存储与进程所有权

默认目录是**当前工作目录**下 `.state/codex`，可用 `--state-dir` 固定到明确位置。只创建 `owner.lock` 和 `codex-auth.dpapi`；不会发现、读取或修复 Codex/Hermes 其他应用的登录缓存。

凭据通过 Windows 当前用户 DPAPI 和本应用上下文保护。临时文件和最终文件均为密文；保存先 flush/fsync，再替换目标。JSON 包含应用标识、版本、认证路线、登录代际和状态；不相符或损坏时拒绝加载。显式 `auth logout` 可以重置本应用损坏的 vault，状态查询不会默默覆盖损坏内容。

`CreateFileW` 的独占句柄在 manager 生命周期内一直持有，第二个进程/manager 无法抢占或删除锁文件。失败的第二次组合也不能释放第一个 manager 的句柄。正常关闭先结束任务/本机写入，再释放句柄；进程中断由 OS 回收，测试在有限等待内验证能够重新取得所有权，不靠删除锁来“解锁”。

因此，**另一个运行中的实例持有凭据时，CLI 返回 busy**。本机进程内使用 `TokenManager.logout()`；后续管理界面通过当前应用的控制接口退出。独立 CLI logout 应在持有者停止后执行。

磁盘与 DPAPI 操作在受管理的工作线程中执行。取消会先等待已开始的本机操作结束，避免“调用者退出但后台线程仍在写”。网络请求有 20 秒总时限；本机磁盘若发生 OS 级阻塞，不承诺硬实时关闭，也不会提前释放仍有写入的所有权。本轮验证了进程中断和原子替换失败，没有制造突然断电或验证所有文件系统的持久性。

## 4. 命令用法

以下在项目根目录的 PowerShell 中使用现有环境；`login` 会访问真实认证服务并要求用户在浏览器参与，**本次没有运行真实 login**：

```powershell
& '.\.venv\Scripts\wechat-agent.exe' auth status
& '.\.venv\Scripts\wechat-agent.exe' auth login
& '.\.venv\Scripts\wechat-agent.exe' auth logout
```

需要从不同工作目录调用时，固定同一个存储位置：

```powershell
& '.\.venv\Scripts\wechat-agent.exe' auth status --state-dir 'D:\qieehy\WeChatAgent\.state\codex'
```

`status` 仅显示 ready/refresh_required/waiting_for_login/paused/需重新登录等状态、Unix 到期时间、账号指纹及安全错误码，不会主动登录或刷新。设备码只在 login 屏幕显示。真实设备码登录如提示未启用，应按 [OpenAI 认证说明](https://learn.chatgpt.com/docs/auth)核对安全设置；不设置商业接入申请前置步骤。

在应用中，H2 的接线合同为：

```python
auth = CodexAuthClient()
store = CredentialStore(explicit_state_directory, auth.binding)
async with TokenManager(store, auth) as tokens:
    async with CodexTextProvider(model=selected_slug, session_source=tokens.get_session) as model:
        result = await model(messages, tools=None)
```

这是组合示意；真实模型目录、完整 Runtime 工厂和一次受控模型 401 刷新/重试的组合仍由 H3/H4 落实。H2 当前会根据过期时间触发刷新，不声称 H1 provider 已具备模型 401 后的自动恢复。

## 5. 验收与复现

```powershell
& '.\.venv\Scripts\python.exe' -B -m pytest -q -o faulthandler_timeout=30 --junitxml=.build/h2-tests.xml
& '.\.venv\Scripts\python.exe' -B -m ruff check src tests scripts
& '.\.venv\Scripts\python.exe' -B -m pip check
```

覆盖了真实 Windows DPAPI 的加密/解密及损坏检测、原子替换失败、跨进程排他与子进程中断；10 个并发等待者只刷新一次、等待者取消、保存失败、重启 pending 恢复、429 持久暂停、退出/关闭/本机写入的竞态；设备码 403/404 等待、期限、表单换码、坏响应、账号/路线绑定、重定向不转发；实际 CLI 的状态/退出及注入合成服务后的 login 组合。

认证测试同时使用进程内 HTTP MockTransport 和真实 loopback HTTP 服务；模型整合测试经过真实 loopback 服务。所有令牌、账号、一次性代码和消息均为合成值。官方 URL 的 residency 请求头用截获传输验证，没有对外发送模型请求。

未新增第三方依赖，原 `requirements.lock` 的 49 项锁定内容保持有效；Windows 保护通过标准库 ctypes 调用系统 API。原 AI_agent 未修改，仍使用 H1 构建的本地 wheel。

参考：[固定 Hermes 源码研究](HERMES_CODEX_RESEARCH.md)、[DPAPI Protect](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata)、[DPAPI Unprotect](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptunprotectdata)、[Windows 独占句柄](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew)、[asyncio shield](https://docs.python.org/3.12/library/asyncio-task.html#asyncio.shield)。
