# Hermes 的 GPT 订阅接入评估

核对日期：2026-10-26（Asia/Shanghai）。本文件从属于 `../PROJECT_PLAN.md`；结论基于 Hermes 官方文档与 GitHub 选定源码抽样，未安装 Hermes、读取本机认证缓存、登录账号或发起模型请求。新增源码链接为移动 main 分支，尚未固定统一提交。

后续进展：用户已指定优先研究此路线；[固定源码研究与离线验证](HERMES_CODEX_RESEARCH.md)记录提交 `e82bf01eec9b`、18 份校验快照及 19 项通过的离线合同测试。本文保留首次调查记录；具体源码结论优先看后续报告。原生 Codex 路线现为优先研究/验证方向，尚未执行真实连接。

当前实施顺序见 [Hermes Codex 路线计划](HERMES_CODEX_PLAN.md)。下文“仅新增候选、不改变 SIWC 主路线、尚待选定”的表述属于首次评估时点，已被用户后续指定的 Codex 优先计划取代；当前无需再次选择研究路线。

## 1. 结论

**有帮助，值得把 Hermes 的 Codex OAuth 接入作为独立候选，重点参考认证恢复与 Responses 转换；目前不足以作为本项目即插即用的模型代理，也不能据此宣布群友自动问答已解决。**

项目仍复用用户的 `AI_agent`。本轮不引入 Hermes runtime，不改变已选 SIWC 主路线；若后续选择 Codex OAuth，应另记路线变更、适用授权、模型合同及验收影响。

## 2. 找到的三种不同功能

| 名称 | 查到的事实 | 对本项目的意义 |
| --- | --- | --- |
| Hermes `openai-codex` provider | 官方文档提供 ChatGPT/Codex 订阅登录选项，默认 device-code，也有浏览器登录；源码默认 base 为 `https://chatgpt.com/backend-api/codex`，transport 为 `codex_responses` | 用户所说的方法很可能指它；它与本项目 SIWC 公共 API 路线不同，需单独评估 |
| Hermes Subscription Proxy | 文档列出的上游为 `nous` 与 `xai`；代理负责附加凭据并转发，不运行 Agent 循环 | 架构适合保留自有 Runtime，但文档没有提供现成 OpenAI Codex adapter，不能只改 base_url 就认定可用 |
| Hermes API Server | 文档明确表示 HTTP 请求由 Hermes 的完整 Agent 工具、记忆和 skills 处理 | 直接接它会引入另一套 Agent 执行循环，不符合当前约束；本轮不选 |

证据：[Provider 文档](https://hermes-agent.nousresearch.com/docs/integrations/providers)、[providers.py](https://github.com/NousResearch/hermes-agent/blob/main/hermes_cli/providers.py)、[Subscription Proxy](https://hermes-agent.nousresearch.com/docs/user-guide/features/subscription-proxy)、[API Server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server)。

Nous Portal 是另一项服务；其代理能访问模型，不代表这些请求在消费用户已有的 ChatGPT 订阅。不能把 Portal、Codex OAuth 和 SIWC 混称为同一种订阅方案。

## 3. 与 SIWC 的具体区别

| 项目 | 基线 SIWC | Hermes `openai-codex` |
| --- | --- | --- |
| 应用认证身份 | 本应用动态注册并保存 issued client ID | 所查源码使用固定 Codex OAuth client 配置 |
| 推理地址 | `https://api.openai.com/v1/responses` | provider 默认 Codex 后端 base，使用 Responses transport |
| Token 交换/刷新合同 | SIWC 自有注册及授权 scopes | 所查常量为 `https://auth.openai.com/oauth/token`，与 SIWC 文档合同不同 |
| 历史/正文格式 | 按 SIWC 白名单构造 Responses input | Hermes 有 Chat messages 到 Responses input 和结果归一化转换 |
| 本项目状态 | 尚未实现、登录与实测 | 新增候选，仅静态调查 |

源码证据：[auth_constants.py](https://github.com/NousResearch/hermes-agent/blob/main/hermes_cli/auth_constants.py)、[auth_codex.py](https://github.com/NousResearch/hermes-agent/blob/main/hermes_cli/auth_codex.py)、[codex.py](https://github.com/NousResearch/hermes-agent/blob/main/agent/transports/codex.py)。公开 client ID 不是密码，但其存在不代表本项目可以无条件借用该应用身份。

[OpenAI 官方 Models and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)规定 SIWC 这一路线使用公共 Responses 端点。不能把 Hermes 的 Codex token、SIWC token 和两条端点互换，也不能把 Hermes 的所有请求参数照搬到 SIWC。

## 4. 对现有 AI_agent 的帮助

| 可参考的部分 | 价值 | 适配要求 |
| --- | --- | --- |
| Auth 与 transport 分开 | OAuth 生命周期不绑在社交策略或人格中 | 在本项目提供窄的凭据/推理接口；不用 Hermes 默认组合入口 |
| 刷新、终止错误与重新登录 | 避免失效刷新令牌被无限重试 | 单一刷新所有者、持久轮换与有限重试；不导入或改写其他应用认证缓存 |
| 消息和结果转换 | 现有 AI_agent 是 Chat Completions 合同，可参考转换为 Responses 的做法 | 对接 `LLMResult`，只在完整成功后返回；M1 无工具，裁剪工具/推理历史分支 |
| provider 与 endpoint 绑定 | 避免切换路由后把凭据发到错误上游 | 把账号、认证方式、端点和模型选择作为一个校验单元 |
| 独立代理的边界 | 本地模型服务可以不拥有 Agent 循环 | 只是设计参考；新增 Codex 代理还需认证、协议转换、访问控制和独立验收 |

源码中已观察到同步 HTTP 刷新实现，以及 transport 对 Hermes 其他模块的依赖。因此不能把两个文件复制到异步 Runtime 就宣称接好；需要明确 async I/O 或有界 worker、连接关闭、取消和错误映射。Hermes 根 [LICENSE](https://github.com/NousResearch/hermes-agent/blob/main/LICENSE)是 MIT，实际复制前仍需固定文件版本并检查依赖/声明。

不纳入的功能：Hermes Agent loop、自动插件/skills、账号池轮换、自动计费 fallback、全功能 API Server、读取既有 Codex 登录缓存。它们不是接通文本推理的必要条件。

## 5. 能否解除当前 BLOCKED

- **本地个人接入申请：** 前文已经口头澄清，此处同步记录：官方支持在本机运行的个人项目，不能仅因项目私有自用就要求等待商业资格申请。SIWC 当前应记“待登录授权与实测”。依据 [OpenAI 官方个人项目说明](https://developers.openai.com/cookbook/articles/sign-in-with-chatgpt)。
- **模型能否实际调用：** Hermes 提供一条值得验证的候选实现，但本机没有实测，不能把上游文档支持视为自己的 PASS。
- **群友自动问答：** 原 BLOCKED 判断针对选定的 SIWC 触发方式。Codex OAuth 是另一条路线，需要另核适用授权与用途；不能只因为 Hermes 能登录就宣称该用途获准，也不把 SIWC 特定合同机械等同于所有其他路线。当前证据没有解除该问题。
- **微信收发：** 不受这一发现直接解决；版本、身份、目标、回执和恢复仍需原定微信 PoC。

Hermes Provider 文档对 ChatGPT 计划层级及 Codex 配额如何计入仍标注未完整说明；不能据此估算固定消息量、无限用量或精确成本。

## 6. 建议纳入计划的验证切片

先做离线、无账号的接口验证：固定所参考 Hermes 提交 → 列出最薄 auth/transport 依赖 → 用合成 Responses 流映射到 AI_agent 的 LLMResult → 覆盖断流、取消、401/429、刷新轮换及无工具调用。候选实现放在独立 adapter 后，不污染社交逻辑。

进入真实登录前，先选定到底验证 SIWC 还是 Codex OAuth，并明确该路线的应用身份和适用范围。获准实施后才进行一个本应用独立的登录及 3 次主人手动短文本测试；不接微信、不用真实群消息、不复用现有 Codex 凭据。只有全部完成才把“模型连接未验证”改为 PASS。

**首次评估决定（历史）：加入研究候选，优先参考 auth/transport 设计。当前执行决定以新的路线计划为准。**
