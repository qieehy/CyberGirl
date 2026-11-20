# H3：完整 Runtime、本机多轮对话与关键验收

完成日期：2026-10-27（Asia/Shanghai）。H3 已实现并通过本机验收；真实订阅仍留在 H4。证据见 [H3 验收记录](evidence/h3-result.json)。

## 1. 已跑通的链路

```text
chat --mode mock
  → 独立合成认证 + Windows DPAPI 临时凭据
  → ChatEngine 选择当前登录与会话的已确认上下文
  → 实际 AI_agent Runtime.run_async
  → CodexTextProvider → 本机 HTTP/SSE
  → 完整 LLMResult → 本机显示确认 → 更新 RAM 历史
```

Runtime 是已安装框架的 `runtime.runtime.Runtime`，不是模拟 Runtime。每轮构造一个临时执行实例与文本记忆适配器；只调用一次逻辑模型接口，不启用默认工具工厂、embedding worker、planner 或 critic。HTTP 401 可按下述受控恢复规则产生第二次传输请求，不把它隐瞒为只有一次 HTTP。

新增的主要模块：

| 文件 | 当前职责 |
| --- | --- |
| `src/wechat_agent/bootstrap.py` | 组合认证、provider、真实 Runtime 入口及退出顺序；区分 mock/live |
| `chat.py` | 每会话串行、跨会话隔离、任务生命周期、最终结果与显示确认 |
| `context.py` | 作用域键、有限历史窗口、一次性文本 memory adapter |
| `framework.py` | 加载已校验的离线分词资源，初始化框架并关闭包含原文的库日志 |
| `mock_backend.py` | 仅本机随机端口的合成认证/Responses 服务；计数并保留当前一次请求，不积累无限请求日志 |
| `cli.py` | 实际 `chat` 命令；多轮、reset、logout、EOF/退出清理 |

H1/H2 的 provider、会话元数据和 TokenManager 同时补齐登录代际校验及模型 401 的受控恢复。

## 2. 上下文与确认

会话键同时绑定当前认证登录代际，以及 `platform + account_id + conversation_type + conversation_id`。scope 来自调用程序，不从模型回答、昵称或消息正文提取。H3 的非本机 scope 仅用于合成隔离验证，不表示已有微信渠道权限或接入。

- Runtime 只看到一个受控临时上下文，受信 system 单独保存；普通文本里的 `system:`、JSON 角色描述和 tokenizer 特殊字符串都保留为 user 文本。
- 候选回复只交给显示/投递回调。返回明确 `True` 且登录身份仍相同，才保存本轮往返；拒绝、异常或取消均不把草稿记成已发言。
- H3 本机上下文保存的是已确认完成的往返。被拒绝的整轮不保留；正式社交应用对所有入站消息的独立账本，仍在 F-03/F-05 实现，不由此内存窗口替代。
- 同一 scope 的下一轮等待前一轮显示确认完成；不同 scope 可以独立执行，provider 的实际传输仍按单并发限制。
- 默认保留最多 16 对往返、64 个 scope、4 个执行/排队任务；上下文按完整往返裁剪，受信 system 不被裁掉。整体回合时限包括排队、推理和显示确认。
- `/reset` 清空空闲的当前 scope；运行中的 scope 返回 busy。退出应用清空 RAM 历史；H3 不持久保存聊天记录。

`TurnBuffer`/`TurnMemoryManager` 是框架公开 memory 合同的应用侧文本适配器，不调用框架默认 SQLite 工厂，也不修改其私有缓存。保留策略由应用确定；其内存用量估计使用 UTF-8 大小上界，不冒充模型 token 用量。真实用量只保留 provider 返回值，缺失仍为未知。

## 3. 身份切换、401 与关闭

每次 HTTP 请求发出前检查会话携带的登录代际。即使身份在“组装上下文”和“取得令牌”之间变化，也不会用新身份发送旧上下文。完成生成与显示确认时再次核对身份，迟到结果不进入新会话。

模型端点明确返回 HTTP 401 时，允许一次由 TokenManager 持有的受控刷新，再重发相同请求；并发/迟到的旧会话拒绝不能再次刷新较新的令牌。快照版本绑定整组凭据及到期信息，即使刷新只轮换 refresh token、仍返回相同 access token，也能识别迟到的旧拒绝。第二次仍为 401，则标记当前会话需重新登录，后续请求停止；403、429、断流、5xx 等不通过该分支重放。

应用 logout 作为独立任务完成：先停止 Runtime/待显示内容，再关闭推理 provider，再使本地凭据失效。调用者取消等待不会取消实际退出；应用关闭会等待退出任务结束。常规 `/exit` 保留登录、清空 RAM 历史并关闭 HTTP/任务/凭据句柄；mock 模式的临时密文目录一并清理。

已提交给外部服务的请求不能靠本地退出撤回。显示回调须合作响应取消；若显示后确认失败，不声称“肯定未显示”或自动重发。真实微信投递回执、UNCONFIRMED 和 outbox 仍属于后续工作。

## 4. 离线启动与原文日志

首次完整导入发现框架的 `memory.token_counter` 会在 import 时获取 `cl100k_base`，缺少缓存时隐式联网；该尝试在沙箱被阻断。本轮通过明确的资源获取步骤下载公开分词表，按已锁定 tiktoken 中的预期 SHA-256 校验后随应用打包。

- 数据大小：1,681,126 字节。
- SHA-256：`223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7`。
- 目录：`src/wechat_agent/assets/tiktoken/`，包含来源 manifest 与 tiktoken 软件许可副本。
- 启动先验证本地数据，再通过 SDK 支持的缓存配置预加载，随后恢复原环境设置；缺失/损坏时报错，不在启动时补下载。
- wheel 已实际构建并检查：H3 模块、资源与许可均在包内，资源哈希匹配。

原框架会记录输入前缀。当前独立应用进程关闭 runtime/memory/tools 的 Loguru 日志，仅收集事件类型、完成状态等安全摘要，原文和完整事件 payload 不进入诊断日志。没有修改原 AI_agent 源文件。

收尾核对发现相邻 `D:\qieehy\AI_agent` 中 9 个文件已与 H1 锁定快照不同；本次没有改写这些文件或更新框架锁。当前 `.venv` 内的 61 个框架 Python 文件仍逐一匹配锁定指纹，原框架 wheel 哈希也匹配。H3 的兼容性结论针对这个已安装版本；后续采用相邻目录的新修改须另行评估。

## 5. 使用入口

项目根目录 PowerShell：

```powershell
& '.\.venv\Scripts\wechat-agent.exe' chat --mode mock
```

mock 的回复是明确标识的本机合成内容，作用是验证多轮链路。支持 `/reset`、`/logout`、`/exit`；关闭 stdin 也会正常清理。

一次性演示：

```powershell
& '.\.venv\Scripts\wechat-agent.exe' chat --mode mock --once '你好'
```

已有独立登录且已取得真实账号模型 slug 后，可显式选择 live：

```powershell
& '.\.venv\Scripts\wechat-agent.exe' chat --mode live --model '<账号目录里的真实 slug>' --state-dir 'D:\qieehy\WeChatAgent\.state\codex'
```

本次未运行 live。缺少登录时不会自动发起授权；模型目录获取、真实登录及订阅兼容性在 H4 验证。控制台读取输入在主线程、事件循环运行之间进行，避免留下阻塞 `input()` 的后台工作线程。

## 6. 验收

按用户要求，本阶段只保留 **6 个关键场景**，集中于 `tests/test_chat.py`：

1. 实际 Runtime + 原生凭据保护 + HTTP 多轮链路；仅确认历史、会话/登录隔离、无额外工具/模型及原文日志。
2. 同 scope 排队取消、其他 scope 独立执行、退出等待者取消、完整应用资源释放。
3. 401 成功恢复与二次 401 终止，验证至多一次刷新和两次模型 HTTP 请求；刷新保持相同 access token 时，迟到旧拒绝不再次刷新或使新凭据失效。
4. 登录在取令牌前切换时，旧上下文零模型 HTTP 请求。
5. 不符合合同的工具结果在 Runtime 边界被拒绝，零工具执行、零自动追加模型调用。
6. 冷进程禁止隐式分词下载，并通过已安装的 CLI 完成多轮/reset/exit。

本阶段做过一次 H1–H3 整体回归：**93 tests / 848 subtests，0 失败/错误/跳过，48.47 秒**。随后收尾审查补强凭据快照版本判断，最终源码仅复验上述 **6 个关键场景**，全部通过，耗时 12.29 秒；没有重跑其余 87 项。两个时点的代码指纹分别记录，不能把此前整体回归当成最终源码的再次全量执行。Ruff 通过；pip check 在整体回归阶段通过，原 49 项依赖锁未改变。

```powershell
& '.\.venv\Scripts\python.exe' -B -m pytest tests/test_chat.py -q
```

完整回归记录为 `.build/h3-tests.xml`，最终关键验收为 `.build/h3-critical.xml`；脱敏统计、安装包哈希、两个时点的代码指纹及框架来源差异保存于 [H3 验收 JSON](evidence/h3-result.json)。H1/H2 的历史验收文件保留各自时点，不重写成 H3 指纹。

H3 未完成真实模型目录、真实订阅推理、微信接入、持久消息账本或前端。下一阶段为 H4，先补齐账号目录入口，再按计划执行真实登录与三次短文本验收；真实刷新按独立观察项记录。
