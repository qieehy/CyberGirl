# H4：真实订阅已接通，刷新待观察

日期：2026-10-27（Asia/Shanghai）。**H4a 已通过；H4b 真实刷新尚未观察。** 通过 Hiddify 完成独立登录并取得 8 个可见模型，选择目录首位 `gpt-6.1-sol`，三个独立进程的直接调用、真实 Runtime 中文人格和登录恢复均通过。当前证据见 [H4 记录](evidence/h4-result.json)；首次直连拒绝保留为[历史记录](evidence/h4-initial-auth-result.json)。

验收后的使用改进：现已支持当前工作目录的 `wechat-agent.local.toml` 持久代理配置，本项目已保存 Hiddify 地址；无需每次设置环境变量，使用方式以 [README](../README.md) 为准。该改动通过一个代理优先级与测试路由隔离的关键场景（1.04 秒）及 Ruff 检查，未重跑真实调用。下列 H4 请求证据、源码指纹和 wheel 保留为当时验收版本。

后续 review 修复：失败/取消记录升级为 schema 2，保留失败阶段、逻辑调用数及实际 HTTP 尝试数；既有 schema 1 历史不补造数据。硬崩溃后的 started 记录仍属于未知结果。当前包及本轮关键验证见 [修复记录](REVIEW_2026-10-27.md)。

## 本次真实验收

| 步骤 | 结果 | 模型 HTTP 次数 | 推理耗时 |
| --- | --- | --- | --- |
| provider | 完整结束，中文连接检查通过 | 1 | 3.141 秒 |
| runtime | AI_agent Runtime 完成一步，包含“小禾”的中文自我介绍通过 | 1 | 4.781 秒 |
| restart | 前一进程退出后，新进程恢复应用自有登录状态，中文检查通过 | 1 | 2.843 秒 |

三步的模型、源码指纹和登录代际一致，进程各自独立，均收到 `response.completed`；Runtime 使用本机验收 sink。令牌到期时间均未变化，所以不能据此宣布真实刷新通过。微信投递、群友问题回复和持久聊天历史不在此次验收范围。

真实接口暴露了两个离线样本未覆盖的差异：有效 SSE 响应未携带 `Content-Type`；流中包含 `response.metadata`。现在允许缺失类型头后继续严格、有界地解析 SSE，并将 metadata 作为不产生正文的事件处理，仍核对响应 ID、要求明确完成；明确的错误类型头仍拒绝。另支持服务实际使用的 `x-oai-request-id`。仅新增一个针对该组合的关键回归场景，验证正常完成、HTML、错误类型头、只有 metadata 和跨响应 ID 等情况，最终通过（2.77 秒）；Ruff 通过。

失败与诊断未删去：首次 provider 失败、两次针对性诊断、修复类型头后的 metadata 失败均保留。该排障阶段有 6 次可确认的模型 HTTP 请求（含最终三次成功）；最早失败记录未写入失败阶段/尝试数，因此其是否另发出一次模型请求只能记为 0–1，不能把整个排障过程描述为仅请求三次。没有后台自动重试或为凑结果重复成功请求。

## 首次直连结果（历史）

已通过本应用专用 `.state/codex` 发起一次独立设备码登录。认证服务在生成设备码前返回 HTTP 403；随后仅做了一次脱敏诊断，得到机器错误码 `unsupported_country_region_territory`，错误类型为 `request_forbidden`。返回内容为 JSON，没有检测到浏览器挑战标记。

当时没有取得设备码、完成账号登录、取得真实模型目录或发起模型请求。该拒绝发生在账号授权之前，不能归因于用户订阅档位、账号模型权限或所谓商业资格申请；也没有确定请求被识别为哪个地区。

首次阶段停止了重复认证，随后按用户指出的代理配置问题继续诊断。官方正常设备码流程与账号设置要求见 [OpenAI 认证说明](https://learn.chatgpt.com/docs/auth)。

## 代理路由修正

认证、模型目录和推理 HTTP 客户端均配置了 `trust_env=False`，执行进程也没有代理变量，因此没有采用用户已有的显式代理。普通沙箱中读取到的 Windows 代理关闭状态与实际用户注册表不同；经实际用户上下文复核，系统代理已开启，地址为 Hiddify 的 `127.0.0.1:12334`，用户也确认使用 Hiddify。此前把沙箱视图当成实际系统设置的判断已更正。

Clash 同时运行并监听 `7897`，但经该入口访问认证服务出现 TLS EOF；切换为当前系统实际使用的 Hiddify 入口后，本应用成功取得设备码。设备码只显示在临时登录界面，不写入证据文件。

新增 `WECHAT_AGENT_PROXY` 显式配置，由 `providers/codex/network.py` 统一验证，再注入认证（含刷新）、目录和推理三个客户端。保持 TLS 校验；代理必须为本机 HTTP/HTTPS 地址，拒绝用户信息与查询参数。代理连接失败不会自动退回直连。为防止合成凭据误出本机，loopback 与注入的测试传输忽略该配置。

修正后仅复验 3 个既有 H4 关键场景：在配置不可用代理的情况下，全部合成流程仍独立通过，耗时 6.54 秒；未重跑其他测试。真实代理入口的验证采用实际设备码请求，已经成功。

## 已完成的工程入口

- `wechat-agent models`：使用本应用当前会话实时 GET `/backend-api/codex/models`，携带账号和可选 residency 头；只返回服务端实际列出的可见 slug，不合成模型或读取其他客户端缓存。目录可见仍需实际调用验证。
- `wechat-agent verify --stage provider|runtime|restart`：每次命令仅执行一个验收步骤、一次逻辑模型调用。每步先读取当前会话目录并核对所选 slug 与登录代际，受控 401 恢复最多带来第二次 HTTP 尝试；实际尝试数写入记录。
- 验收记录：保存时间、进程、代码指纹、模型、完成状态、耗时、request ID、返回 usage（缺失为 null）、文本检查结果及 Runtime 摘要。原始提示词、回答、账号标识及令牌均不保存。
- 已存在的 `--output` 文件会在网络请求前阻止运行，避免重复消耗。取消或失败分别记录；启动记录残留表示结果不确定，不自动重放。
- 认证客户端能将已知的地区拒绝映射为 `unsupported_region`，保留 HTTP 403，避免只给出含糊的 forbidden；不输出任意服务端错误文本。

目录读取有 20 秒总时限、2 MiB 大小上限和条目上限，拒绝重定向及压缩响应，取消会关闭连接。`supported_in_api=false` 不会被误当成订阅不可用。目录查询参数 `client_version=0.164.0` 用于协议兼容，应用仍标识为 WeChatAgent/0.1.0；没有把它当成已安装 Codex CLI 版本。

目录协议参考此前固定 Hermes 提交的 `hermes_cli/codex_models.py`，并补读同一提交的 `agent/model_metadata.py`。新增静态文本及来源哈希保存在 [H4 来源补充](../research/hermes_subscription/h4/manifest.json)，不改变 H0 的 18 份历史快照，也不执行上游代码。本实现采用单个明确兼容版本，无静态模型或失败后的其他目录回退。

## 后续使用与重新验收

从项目根目录执行时，现有本地配置会自动提供代理。以下是临时覆盖方式，端口对应此次实际 Hiddify 设置：

```powershell
$env:WECHAT_AGENT_PROXY = 'http://127.0.0.1:12334'
```

环境变量覆盖只对当前终端及其子进程生效；新终端自动回到本地配置，无需重新设置。程序不会自动采用 Windows 系统代理。前一步失败时停止，不连续重试。

```powershell
& '.\.venv\Scripts\wechat-agent.exe' auth login --state-dir 'D:\qieehy\WeChatAgent\.state\codex'
```

当前已经完成授权，无需重复登录；上面的 login 仅供退出或授权失效后使用。需要重新登录时，在官方浏览器登录页完成授权。读取账号目录：

```powershell
& '.\.venv\Scripts\wechat-agent.exe' models --state-dir 'D:\qieehy\WeChatAgent\.state\codex'
```

本次已经通过验收，无需再运行以下命令。将来代码或环境改变而需要重新验收时，从返回目录中选定一个真实 slug，并使用新的证据文件名；不能覆盖既有结果。每条命令是独立进程，三步使用相同模型和凭据目录：

```powershell
& '.\.venv\Scripts\wechat-agent.exe' verify --stage provider --model '<目录中的真实 slug>' --output '.state/h4/next-provider.json'
& '.\.venv\Scripts\wechat-agent.exe' verify --stage runtime --model '<同一个真实 slug>' --output '.state/h4/next-runtime.json'
& '.\.venv\Scripts\wechat-agent.exe' verify --stage restart --model '<同一个真实 slug>' --output '.state/h4/next-restart.json'
```

三个步骤分别检查：直接 provider 短文本、真实 Runtime 的“小禾”中文人格、退出进程后的登录恢复。验证 sink 只用于本机验收，不代表微信消息已发送；重启步骤验证的是登录恢复，H3 的聊天历史仍为 RAM。

仅当三份记录都 passed、模型/代码指纹一致、登录代际一致、restart 来自后续独立进程，且请求数符合预期，才能记录 H4a 已通过。单独运行名为 restart 的命令本身不证明此前发生过重启。出现 acceptance_failed 时保留记录并检查原因，不为了凑成功次数自动重复请求。

H4b 仍需在正常生命周期中观察真实刷新、轮换凭据保存及随后重启恢复；不伪造过期或反复刷新。单份记录的 `token_expiry_advanced` 仅是观察线索，不能独自证明 H4b 通过。

## 关键验证与交付范围

新增 3 个关键场景：账号目录与凭据绑定；目录失败边界及地区错误分类；分步验收的目录前置检查、Runtime、脱敏记录和防重复执行。另复验 1 个受 HTTP 尝试计数影响的既有 401 场景。结果为 **4 项通过，7.28 秒**；没有重跑其他 H1–H3 测试。Ruff 通过；依赖锁与已安装框架保持既有锁定版本。

当前安装包位于 `.build/h4-proxy-wheels/wechat_agent-0.1.0-py3-none-any.whl`；初次 H4 包 `.build/h4-wheels/`、H3 包及历史验收记录保留。当前已完成模型连接与本机基础验收；微信收发、群友问题回复、完整消息账本及图形界面仍按后续计划实施。
