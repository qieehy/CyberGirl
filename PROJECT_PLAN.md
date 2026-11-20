# WeChatAgent 项目基线与 M0 / M1 计划

> 后续实施（2026-11-16）：文件助手工作台 E 阶段已开始，详细任务/接口/验收见 [后续实施计划](docs/WORKBENCH_PLAN.md)，代码及验证状态见 [实施记录](docs/WORKBENCH_IMPLEMENTATION.md)。新工作台的 E7 实机闭环尚未验收；11 月 11 日 BG1 限定结果仍按原范围有效。

> 当前（2026-11-11，优先于下方历史）：本轮文件传输助手 BG1 限定验收完成。已适配固定微信 4.2.0.13，两条获准标记各只发送一次并获手机完整确认；独立纯读取段连续 1800.015 秒、516 轮，15 分钟重启游标一致、零重发/重复，5 次快照并发更新恢复通过。原账本 16 操作/14-10 许可、8 条旧 UNKNOWN 不改，一般发送阻断保持。原中断记录全部保留。654 passed、9 项旧版文件缺失跳过，Ruff 通过。一般自动回复、补充字符及 B21 最小化/托盘未开放。见 [最终验收](docs/W2B20_BG1_ACCEPTANCE.md)。

> 当前进展（2026-11-08，优先于下方历史）：COVERED 遮挡单次重测电脑端完整通过，手机确认待回复；B19 原 SocialService 后台适配器与 B20 原账本接收/重启验收入口已实现，正在离线核验，未创建新实机动作或开放一般发送。旧八条 UNKNOWN、RETURN 原收尾拒绝和补充字符暂缓均保持。见 [服务接入方案](docs/W2B19_BACKGROUND_SERVICE.md)。

> 版本：初始基线 v1.0；资料核对日期：2026-10-26（Asia/Shanghai）。
> 目标位置：`D:\qieehy\WeChatAgent\PROJECT_PLAN.md`。
> 本文是后续项目展开的**唯一初始设计依据**。公开项目、视频与历史聊天是参考材料，不自动改变本文的需求、权限或技术选择。后续变更应记录理由、证据和验收影响，再更新基线。
> 当前路线（后续变更优先）：先实施 Hermes 原生 Codex 订阅接入，保留自有 AI_agent；详见第 18 节及 [Hermes 路线实施计划](docs/HERMES_CODEX_PLAN.md)。第 5 节 SIWC 保留作对照，不再是当前优先开发路线。
> 状态（2026-10-27）：H0–H3 已完成，H4a 真实订阅已接通。修复 Hiddify 路由及 SSE 兼容问题后，独立登录、账号目录、直接模型调用、真实 Runtime 中文人格和独立进程登录恢复均通过。H4b 真实刷新待自然观察，当前证据见 [H4](docs/H4_IMPLEMENTATION.md)，历史见 [H1](docs/H1_IMPLEMENTATION.md)、[H2](docs/H2_IMPLEMENTATION.md)、[H3](docs/H3_IMPLEMENTATION.md)。

> 当前开发优先级（2026-11-07）：限定 filehelper 的前台收发 PoC 已通过，下一项优先完成后台操作 BG1；先完整复现固定上游，再集成和验证重启，不抢前台、不动鼠标是必需验收条件。详见 [后台操作实施计划](docs/BACKGROUND_OPERATION_PLAN.md)。E–F 真实发送接线在 BG1 之后推进。

> B18a 执行进展（2026-11-07）：已交付固定源码的离线对照运行器、72 个 vendor Python 源文件及元数据/许可证摘要、导入清单和配置—效果矩阵。复现显式 UTF-16 清框换编码、chat=None 会话变化后仍提交、命名互斥失败仍放行的问题。未经修改原版不准入；受约束版本尚未实现完整绑定/恢复，B18b–B21 未执行。全量 526 passed、848 subtests passed，准入专项最终 10 passed，Ruff 通过。没有微信动作或真实账本改动。详见 [B18a 准入调查](docs/W2B18A_BACKGROUND_ADMISSION.md)。

> B18a 继续实施（2026-11-07）：已实现受约束单次控制、失败拒绝的 Windows 进程互斥、原账本持久意图和后台事件/鼠标观察。用户重登后，原账号/filehelper/空输入/控件/几何真实后台准备通过，临时 gate 初始化及还原、子进程回收和锁释放可核查；没有输入或提交，真实账本仍 7 次操作、各 5 次许可及原阻断。新增延迟队列反例表明，投递前后原生检查仍不能绑定微信稍后处理字符时的会话，B18b 继续不准入。实现期间全量 577 passed、848 subtests passed，后续变更有专项/相关回归。详见 [最新 B18a 结果](docs/W2B18A_BACKGROUND_ADMISSION.md)。

> B18 独立窗口实际进展（2026-11-07）：用户打开独立 filehelper 窗口并切换主窗会话后，独立窗口原生规范身份、两模式空输入及主窗切换隔离通过。可回收宿主与原账本背景审核接线完成，随后用户批准的一次新标记实际输入但丢失末尾 emoji，正文守卫在提交前停止。输入/提交许可 1/0，未点击、未清框/补发，子进程、gate 和锁恢复通过；真实账本现 8 次操作、累计 6/5 次许可、四条 UNCONFIRMED、原阻断保持。已实现完整 Unicode 值输入的新候选，等待用户手动处理草稿、重登准备及之后的新单次许可；未创建 value 标记动作。详见 [实际结果](docs/W2B18_STANDALONE_CONTROL.md)与[新候选](docs/W2B18_VALUE_CONTROL.md)。后台发送尚未验收，B19–B21 未执行。

> B18 最新结果（2026-11-07，优先于上述历史进展）：获批的 value 与 deferred 两次完整值写入均在提交前被前台观察拒绝，均无提交许可。最后一次在 value_input 阶段停止，尚未投递伪焦点或鼠标消息；正文含中文/emoji 完整保留在草稿。旧观察未保存触发来源和事件 object/child，激活归因仍未确定，后续只补充只读诊断，不改写旧结果。原账本现 10 次操作、累计许可 8/5、六条 UNCONFIRMED、活动操作 0、完整性 ok，原阻断保持；子进程、gate 和锁已恢复。三次后台单次许可均已消费，B18b 未通过，B19–B21 未开放。见 [最新实机结果与证据](docs/W2B18_DEFERRED_CONTROL.md)。

> B18 来源诊断准备（2026-11-07）：已补充前台事件来源证据，并实现固定标记的 input-only 诊断，在控制、窗口传输和原账本独立禁止点击/提交；仍保留原前台拒绝，不对旧结果重新判成功。自建隐藏窗口与诊断专项通过，当前原账本和草稿保持；等待用户手动处理草稿、新登录只读准备及该项新具体许可，不自动试发。见 [来源诊断方案](docs/W2B18_SOURCE_DIAGNOSTIC.md)。

> B18 来源诊断现场（2026-11-07 21:23）：用户自行处理旧草稿并重登后，PID 6684/HWND 3605350 的原账号、独立 filehelper、双模式空输入及 input-only worker 只读准备通过。旧输入进程代际均退出，代码摘要与 535 项回归版本一致；gate、子进程、观察器和锁恢复，原账本仍 10 次操作、许可 8/5、六条 UNKNOWN、原阻断保持。等待该新固定标记的单次输入许可，没有创建 SOURCE 动作或提交许可。见 [准备证据](docs/evidence/w2b18-source-standalone-readiness-2026-11-07.json)。

> B18 来源诊断结果（2026-11-07）：单次 SOURCE 诊断已获批并消费，完整值写入时，系统窗口自身事件、回调当前前台和直接采样均命中微信，确认真实前台命中；未投递伪焦点、鼠标或提交。完整草稿保留，原账本 11 次操作、许可 9/5、七条 UNKNOWN、活动操作 0，子进程/gate/锁恢复与完整性通过。535 项回归对应代码未改，当前 UIA 路线不通过 BG1；正在询问是否先限定常用中文/BMP 字符完成后台验收，未接受前不修改原标准、不增加实机动作。见 [诊断结果](docs/W2B18_SOURCE_DIAGNOSTIC.md)。

> B18 当前范围（2026-11-07，优先于上条待选状态）：用户已接受先完成常用中文/BMP 的后台收发，补充平面字符暂缓并在输入前整体拒绝。已实现显式 BMP 窗口消息候选，不调用 SetValue、不删字/截断、不放宽正文核对或前台守卫。用户处理草稿并重登后，PID 29920/HWND 11668968 的原账号、独立 filehelper、空输入及 worker 只读准备通过；相关专项 103 passed，最终全部微信回归 564 passed（93.86 秒），Ruff 通过。原账本/旧七条 UNKNOWN 保持，尚无 BMP 单次发送许可；B18b/B19–B21 未完成。见 [受限中文后台方案](docs/W2B18_BMP_CONTROL.md)。

> B18 最新提交调查（2026-11-07）：BMP 单次许可已获批并消费，中文正文完整，窗口鼠标提交段出现前台事件而直接查询未命中，守卫停止，未有唯一回显，完整草稿仍在，保持 UNKNOWN。原账本 12 次操作、许可 10/6、八条 UNKNOWN、活动操作 0、原阻断及完整性保持。直接 Invoke 虽报告可用，但原生 action 路径无 press 分支，仅作只读调查，未调用。已实现只替换提交为固定窗口回车的候选，原账号新登录 PID 40604/HWND 136907112 空输入/后台 worker 只读准备通过，尚无这条新标记的许可；B18b/B19–B21 未完成。见 [实际结果](docs/W2B18_BMP_CONTROL.md)与[回车候选](docs/W2B18_RETURN_CONTROL.md)。

> B18 回车送达与遮挡重测（2026-11-07）：回车单次已执行，唯一完整回显和手机全文确认通过，投递单独记 CONFIRMED；原生收尾读取变化仍保留原失败，Qt 伪焦点即时未恢复，后台验收未通过。用户说明微信放在前面并要求再测一次，已新登录并用其他活动应用遮挡；PID 43264/HWND 28708360 的原账号/空输入/五点遮挡/后台 worker 准备通过。修复仅限最多三次只读一致性/焦点核对，不重投输入或提交。最新 600 项微信回归通过；原账本 13 次操作、许可 11/7、旧八条 UNKNOWN 不变。将按用户直接重测请求执行一次新 COVERED 标记，B18b/B19–B21 尚未完成。见 [回车结果](docs/W2B18_RETURN_CONTROL.md)与[遮挡重测](docs/W2B18_COVERED_CONTROL.md)。

## 1. 对话承接、目标与约束

历史对话表达了“让 AI 拥有独立微信账号，主动给我发消息，与群友聊天”的目标，并指定 [sourcepro5/WeChatAgent][ref-main] 及 [B 站视频 BV1ss8R6zERG][ref-bilibili] 为参考。本次读取到的历史助手回复只有内容引用占位符，无法恢复此前完整技术结论；因此以用户本次明确要求为准，不把缺失回复当成已验证事实。此前出现过“不自研 Agent”的表述，当前已明确改为：**不用其他现成 Agent 框架，优先复用用户自己的异步 Agent 框架**。

### 1.1 要实现的产品行为

1. 使用独立的普通个人微信账号，由主人手动注册、登录并控制。账号是 AI 的通信身份，不声称具有人类身份或独立意识。
2. 能回复主人私聊，并在主人明确授权的时间、频率和主题内，主动向主人发起私聊。
3. 仅在逐群许可、知情同意的普通微信群中观察当前上下文，判断何时值得发言；不要求每条都回复，也不把普通群改造成命令机器人专用群。
4. 有稳定人格、有限记忆与可解释的发言策略；自然参与是表达目标，透明 AI 身份是前提。
5. 主人能随时暂停、撤销某群权限、停止主动私聊、删除记忆，并查看发送及失败记录。

### 1.2 已固定的设计约束

| 事项 | 当前决定（后续变更见第 15–18 节） |
| --- | --- |
| Agent 核心 | 复用自有 Python 异步框架 `D:\qieehy\AI_agent` 的锁定 wheel；H3 完整 Runtime 已接入，本机模拟验收通过；相邻源码的新变化不自动升级 |
| 第三方框架 | 不引入 DSH、LangChain、LangGraph、AutoGen、Wechaty 等作为本项目 Agent 运行核心；研究它们承载的设计不等于采用 |
| 模型路线 | 优先实施 Hermes 所参考的原生 Codex OAuth / Responses 订阅路线；SIWC 保留作对照，无 API Key 自动回退 |
| 模型资格 | 账号、工作区、地区、应用类别及实际授权必须验证；订阅存在不等于本场景获准使用 |
| 微信接入 | 独立 `WeChatAdapter`；真实当前微信版本的能力验证通过后才选型 |
| 外部动作 | 模型只提出建议；程序中的权限、限频、身份和发送策略决定是否执行 |
| 初始范围 | 围绕 M0 / M1 分步验证；当前先梳理本机 Codex 文本调用，实际代码与真实连接状态见路线计划 |

SDK、OAuth 库、SQLite 驱动及底层消息协议库可以作为基础设施候选，须单独审查许可证和依赖；不得通过它们偷偷引入另一套 Agent 执行循环。用户自有框架若已有 scheduler、memory 或工具系统，应优先扩展接口，避免重建并行核心。

### 1.3 暂不做

不做大规模群运营、陌生人主动私信、批量加好友、自动入群、营销群发、账号池、自动切号、绕过限额或风控。M1 不开放任意文件读写、Shell、浏览器操作、支付、自动安装插件，也不默认启用图片、语音、视频、联网搜索和跨群检索。人格日记与可安装插件仅保留扩展位置，不作为首轮上线要求。

## 2. 资料证据与复现边界

### 2.1 本次核对的仓库快照

通过 GitHub 公开仓库文件清单记录以下提交，并只读检查选定文本。没有克隆运行项目，也未完成全部传递依赖或二进制供应链审计。

| 项目 | 本次 main 提交 | 核对内容 | 当前用途 |
| --- | --- | --- | --- |
| [sourcepro5/WeChatAgent][ref-main] | `5cdedddc60936c550ce5c02558eb025a5350f48e` | README、LICENSE / LICENSES、components 来源记录、包声明、三个核心 Engine/Buffer、群友记忆/主动聊天/日记/插件/Hook 文档 | 主要行为与可靠性设计参考 |
| [hcr707305003/wechat_ai_agent][ref-hcr] | `568a1ea2f95b081c0212e3271441da8a6ec31e13` | README、文件清单、pyproject、Codex 适配器 | 渠道适配、会话和发送分层参考 |
| [wwb45703-creator/wechaty-bot][ref-wwb] | `bd40f5e1c056b483cb166bdec2b793372c838b9e` | README、文件清单、package、群触发/记忆/定时源码 | 较轻量聊天逻辑参考 |

后续开发应重新记录所用提交、依赖锁文件、获取来源、文件哈希、许可证副本及测试环境。下文源码链接尽量固定到本次提交；官方文档链接是动态页面，开发时必须复核。

### 2.2 如何解读本文

- **需求/拟议设计**：本项目要实现的行为；不是第三方功能已验证的声明。
- **公开实现说明**：从 README、文档、源码读到的结构；即“基于 README/源码说明、尚未端到端实测”。
- **待验证/待决策**：缺乏本机实测、授权或用户框架信息，不得作为上线前提已经满足。
- 第三方文档中“实测”“作者已授权”等措辞仅代表作者自己的陈述，不转化为本项目测试或授权。

## 3. 主要参考：sourcepro5/WeChatAgent

### 3.1 当前实现与本项目的关系

本次 README 标示 0.4.1，定位是 Windows 微信助手与桌面控制台，当前通过 DSH 原生会话承载聊天及助手任务。不能将它描述为纯 SIWC、无需外部运行时的即用方案。本项目借鉴其社交决策、可靠投递及作用域划分，核心替换为用户自己的异步框架。人格聊天与高权限任务分开的思路值得保留；M1 只开文本聊天。[README][ref-main-readme]、[DSH 插件依赖声明][ref-main-package]。

### 3.2 重点模块研究

| 模块 | 公开实现要点 | 本项目采用的设计原则 |
| --- | --- | --- |
| SocialEngine | 消息先缓冲，分私聊/群聊组织上下文；支持直接唤醒及普通群消息的批处理；每会话限制并发，维护冷却和配额 | 拆为 runtime / decision / context builder；先本地规则过滤，再决定是否调用模型；每会话串行 |
| ProactiveEngine | 定时机会先查时段、安静程度、待回复/待发送状态；可沉默；生成后再次检查上下文，发生变化取消草稿 | M1 主动目标只允许主人；本地定时检查不调用模型；不补发睡眠或离线期间全部错过任务 |
| MessageBuffer | 持久化近期输入、去重标识、待决策/待投递状态；损坏数据拒绝继续而非覆盖；记录未确认发送 | 使用 SQLite inbox/outbox、事务与唯一约束；保留输入消费位置及发送不确定状态 |
| 群友记忆 | 按账号、群、稳定成员标识隔离；证据支持、可过期的互动观察，不能靠昵称识别人 | 群内观察仅属于本群；记录来源及可信度，M1 默认关闭长期群友画像 |
| 人格与日记 | 人格卡与聊天内容分开；“鱼的记事本”已成为可选插件，按聊天/人格/日期存储 | 人格是受信配置；日记是生成内容，不是事实库，不能自动公开或跨群读取 |
| 插件 | ZIP 清单、按聊天授权、默认禁用、子进程及超时限制；不等于恶意代码安全隔离 | M1 不安装第三方插件；后续接自有工具注册接口并保留逐能力、逐群权限 |
| Windows 桥接 | NT 数据库读取子集 + 原生发送后端 + OneBot 适配层；第三方 Hook/客户端是外部组件 | 将收、发、账号健康检查分开验证，不把“数据库读到了”当作“可稳定发送” |

对应源码：[SocialEngine][ref-social]、[ProactiveEngine][ref-proactive-code]、[MessageBuffer][ref-buffer]。对应行为说明：[主动聊天][ref-proactive-doc]、[群友记忆][ref-members]、[记事本][ref-notebook]、[插件规范][ref-plugins]、[微信桥接契约][ref-hook]。

源码中的决策协议包含 `SILENT`、`RESPOND`，也处理其他控制状态；本项目的 `REPLY` / `INITIATE_DM` 是自己的动作协议，不能直接假定兼容。MessageBuffer 默认以 JSON 文件存状态；本项目选择 SQLite 是新的设计决定，不是对原项目存储方式的转述。[决策解析源码][ref-decision-code]

### 3.3 许可证、依赖与版本注意事项

- [根 LICENSE][ref-main-license] 含 MIT 授权及较长的历史第三方说明，部分内容涉及 QQ/SnowLuma；不能据此认定当前所有组件都是统一 MIT。[LICENSES.md][ref-main-licenses] 对 Bridge 子集、NT 读取子集、外部组件分别说明范围。
- [components.json][ref-components] 将 WeChat-Hook 总体许可标为未确认，同时记录维护者的再分发授权声明；两者都需要核对具体授权对象、版本、用途和本项目是否被覆盖。作者的授权不自动传递给本项目。
- 桥接文档标注 Windows x64 微信 `4.1.10.27`、外部 Hook 来源 `aixed/WeChat-Hook` 的特定提交。源码副本不包含第三方 Hook 原始源码、DLL 或微信客户端；不意味着当前版本持续可登录。[桥接契约][ref-hook]
- 根包声明 Node `>=22.13.0`，桌面构建依赖 Electron 等；DSH 插件依赖 `@deepseek-ai/dsh-llm`，并声明 DSH peer 依赖。它们不是本项目默认安装清单。[根包声明][ref-main-root-package]、[插件包声明][ref-main-package]
- M0 要补查各组件许可证、锁文件、构建/安装脚本、动态下载地址、二进制来源及校验、原生注入行为。不能把一份根 LICENSE 视为全链路许可证明。

## 4. 两个对照项目与选型结论

**下表均基于 README/源码说明、尚未端到端实测。** 仅比较设计，不承诺可运行或无账号风险。

| 维度 | sourcepro5/WeChatAgent | hcr707305003/wechat_ai_agent | wwb45703-creator/wechaty-bot |
| --- | --- | --- | --- |
| 核心形态 | Windows 控制台 + 社交桥 + DSH | 渠道到 Codex/Claude 的双向 Session Bridge | Wechaty + puppet-xp + 本地 Ollama |
| 接收/发送 | NT 数据库读取与外部原生发送后端分工 | README 描述只读密钥恢复/增量读库，默认 UIA 文本发送，另有原生 Hook 路径 | Wechaty puppet-xp / 注入式接入 |
| 微信版本声明 | `4.1.10.27`，不构成当前兼容保证 | README 写 `4.1.12+`；仍需按具体构建和发送模式逐项验收 | README 明确锁定 `3.9.10.27`，不能当作新版通用方案 |
| 社交能力 | 批处理决策、沉默、主动机会、成员记忆、人格、日记/插件 | 侧重持久会话、Agent 路由、通道与 sender 拆分；不能推定已有完整自主社交判断 | @/关键词/问题规则及概率触发，cron 主动话题、JSON 长期记忆 |
| 借鉴重点 | SocialEngine / ProactiveEngine / Buffer 与可靠投递 | UnifiedSession、SQLite 会话、异步桥、账号与私聊/群允许列表 | 稳定群 ID 记忆作用域、人格切换、简单限频及定时防重复注册 |
| 与当前要求的冲突 | 整套采用会引入 DSH 核心 | 整套采用会依赖 Codex/Claude Agent SDK，不等于自有框架 + 直接 SIWC | 整套采用引入 Wechaty/Ollama 和锁版接入路线 |
| 许可状态 | 分组件授权，外部 Hook 权利需独立确认 | 本次完整文件清单未见项目级 LICENSE，pyproject 未给出项目许可；上游许可证附件不能替代本项目许可 | 本次完整文件清单未见项目级 LICENSE，package 未声明许可 |
| 初始决定 | 主要研究对象，不直接部署整套 | 辅助研究分层，不直接复制未明确获许可的代码 | 辅助研究行为，不选择其锁版栈作为默认方案 |

对照来源：[hcr README][ref-hcr-readme]、[pyproject][ref-hcr-package]、[Codex 适配器][ref-hcr-codex]；[wwb README][ref-wwb-readme]、[依赖][ref-wwb-package]、[群触发源码][ref-wwb-room]、[记忆源码][ref-wwb-memory]、[定时源码][ref-wwb-scheduler]。

hcr 的已读包声明含 Python `>=3.10`、PyYAML、wechatauto-replica、UI Automation、PySide6/qasync、Windows 密钥/内存相关依赖，以及可选 `openai-codex` / `claude-agent-sdk`。这些基础依赖与许可需逐项检查；其 Codex 适配器调用 `AsyncCodex`，不能作为官方 SIWC 直接调用成功的证据。[依赖声明][ref-hcr-package]、[适配器源码][ref-hcr-codex]

wwb 的包声明含 Node `>=18`、Wechaty、`wechaty-puppet-xp 2.1.1`、node-cron。其记忆路径按群 ID 和成员 ID 划分，但已读群处理代码仍用群名构造部分会话/限频 key；本项目统一使用稳定 ID，避免同名群/改名混淆。其“聊天数据不出电脑”是原项目 Ollama 场景的描述，不能沿用到本项目云端模型路线。[依赖][ref-wwb-package]、[群处理][ref-wwb-room]、[记忆][ref-wwb-memory]

### 4.1 WeChatPadPro 等历史候选

WeChatPadPro 及此前提到但未明确版本/来源的接入方案统一标为**待审查候选**。本次未确认其权威仓库、分支、许可证、服务依赖、是否收费、协议来源、维护状态和当前登录/收发可行性；不写成已经验证可用，也不凭相似名称选中某个 fork。

纳入选型前须补齐：准确上游 URL、固定提交或发行包哈希、许可证及商业限制、原生二进制/闭源服务来源、凭据存储和数据去向、协议与版本支持、运行权限、供应链风险、停止/卸载方式，以及与 M1 五项微信能力相同的实测证据。无法核清来源或许可则不进入候选实现。

## 5. OpenAI 官方 SIWC + Responses 接入基线

本节保留原 SIWC 技术合同作为对照。当前优先路线为第 18 节的 Codex provider；以下动态注册、scope、端点及场景限制不能直接套用到该 provider。

### 5.1 计划与资格

官方提供 ChatGPT plan usage 的 OSS/本地应用 OAuth 路线，面向符合资格的 Plus/Pro 用户；商业伙伴和部分私有客户端的准入有范围限制。身份登录与使用订阅是不同权限。用户当前计划、工作区、服务地区、私有项目资格和模型目录都尚未验证，不承诺任何订阅可直接用于本项目。[官方 Quickstart][ref-siwc-quick]、[OSS 概览][ref-siwc-overview]

不硬编码模型名，登录后从当前账户可用目录选择。`llm` 只做认证与推理适配，不采用 Codex/DSH 的完整 Agent runtime；不复用其他应用的 client ID、认证缓存或 ChatGPT 网页会话。

### 5.2 OAuth 实现契约（开发时复核）

首次动态注册使用 `dynamic_agent_client` 入口及本应用真实名称，保存返回的 issued client ID；固定本机 `ext_agent_host_id`。采用系统浏览器、PKCE S256、随机 state/nonce 和 `127.0.0.1` 回环回调；授权及换码使用完全一致的 callback URI。申请 `openid profile email offline_access resource.invoke chatgpt.tokens.use.direct`，验证 ID token 和实际 granted scopes。此公开客户端路线无需 client secret 或 partner API Key。[注册与登录][ref-siwc-signin]

凭据按账号/工作区隔离，使用 Windows DPAPI/凭据存储等本机保护方式；不写入仓库、聊天记忆或日志。令牌刷新串行化并保存轮换后的新 token；撤销/退出停止推理和后台任务。持久凭据保存遵循 SIWC 条款的本地、用户控制要求；本项目初始仅在用户本机运行。[账号与会话][ref-siwc-sessions]、[SIWC 条款][ref-siwc-terms]

### 5.3 最小 Responses 合同

采用公开 `POST https://api.openai.com/v1/responses`，OAuth access token 作为 Bearer；SDK 的参数即使名为 `api_key`，也只是承载 OAuth token，不能据此改称传统 API Key 计费。先用同一 token 查 `GET /v1/models`，从该路线返回的 `models` 中选择可显示项，以其 `slug` 调用。[模型与推理][ref-siwc-inference]

文档中的最小请求体形状如下，仅作为后续 PoC 合同，**本次未执行**：

```json
{
  "model": "<当前账号返回的可用 slug>",
  "input": [{"role": "user", "content": "只回复：连接验证成功"}],
  "store": false,
  "stream": true
}
```

本次官方预览限制要求：`input` 数组携带所需历史；采用 `instructions` 或 developer 消息，避免显式 system message；HTTP 不传 `previous_response_id`。当前不支持字段包括 `background`、`conversation`、`max_output_tokens`、`max_tool_calls`、`metadata`、`moderation`、`multi_agent`、`prompt`、`prompt_cache_retention`、`safety_identifier`、`temperature`、`top_logprobs`、`top_p`、`truncation`、`user`。因此不能把普通 API Key 的请求体未经检查直接复用。[预览限制][ref-siwc-limits]

M1 仅纯文本、不挂工具；以后再核对 function/custom tools 的具体合同。不能预设托管 MCP、Code Interpreter、原生 computer use 等在此路线可用。输出必须完整消费事件，只有收到 `response.completed` 才可进入成功路径；失败、不完整、断流均不得把部分文本发送到微信。[预览限制][ref-siwc-limits]、[模型与推理][ref-siwc-inference]

### 5.4 限额与恢复

Plus 文档描述跨相关应用共享五小时用量窗口，应用没有独立额外配额；Pro 不适用同一五小时限制，但**不能推导为无限用量**。计划、应用限制及 credits 以用户设置和实际返回为准，本文不承诺固定消息数、token 数或重置时间。[账号与用量][ref-siwc-sessions]

| 信号 | 程序行为 |
| --- | --- |
| 缺少订阅调用 scope / 未同意 | 保留可用身份登录状态，禁用订阅推理 |
| `subscription_sharing_user_not_eligible` / 403 | 停止此能力，核对资格/工作区/地区，不循环登录重试 |
| `subscription_sharing_usage_limit_exceeded` / 429 | 暂停新推理，提示查看 ChatGPT 用量设置；不猜测重置时间 |
| `subscription_sharing_usage_unavailable` / 503 | 有界退避；不持续高频请求 |
| 不支持能力 / 400 | 修正请求字段或功能；不重发同一无效请求 |
| 401、撤销或终止刷新失败 | 停止推理并提示重新授权；不打印 token |
| 流中 `response.failed` / incomplete / 断流 | 标记失败或不确定，拒绝投递草稿；记录 request ID 及脱敏诊断 |

错误可能不是标准 `error` 对象；保留实际状态、错误码和 request ID。不得自动切到付费 API Key，也不得换号规避限额；更换计费路线是未来单独决策。[错误与恢复][ref-siwc-errors]

### 5.5 与群聊目标直接相关的条款关口

截至核对日，[SIWC 条款][ref-siwc-terms] 要求请求服务于已认证用户，后台使用取得明确授权；同时限制其他用户活动触发该账号请求，以及用一人的订阅满足他人的请求。它还要求使用本应用身份、保护本地凭据，并禁止共享 token/订阅用量、绕限和收费解锁订阅使用。

**工程判断：普通群友发消息或 @ AI 后自动触发主人 SIWC 推理，与上述限制存在直接冲突，当前不得当作允许的默认路径。主人授权、群同意或把消息延迟批处理/改由定时器触发，都不能自动消除该限制。** 群聊“先判断是否沉默”本身也是模型请求，不能靠最后不发消息规避。将群消息触发的私人提醒改成发给主人，同样不是自动合规的解决办法。

保留项目的长期群聊目标，但 M1 的真实 SIWC 整合先限制为主人自己的私聊及明确授权、服务于主人的主动任务。群场景先用本地规则、mock 和合成转录验证。真实群自动推理需取得适用场景的明确官方确认/授权，或另行选择获准计费路线并经用户决策；未解决时相关整合测试记为未通过/阻塞，不宣称全目标完成。

## 6. 微信适配层选型与验证原则

普通个人微信的非官方读库、UI 自动化、Hook、协议模拟等接入方式，可能受客户端升级、登录状态、运行权限及账号风控影响。第三方项目“能收发”与腾讯正式提供该能力不是一回事。独立测试账号可以限制影响范围，不能消除账号风险。

先记录本机真实环境：Windows 版本、微信完整版本和核心模块版本、架构、账号 ID、登录方式、候选桥接版本/提交、运行权限、网络监听地址和凭据去向。**不先降级微信以迎合某个 README，不把修改版本号当成适配。** 若当前版本不支持，先记录失败和替代选项，重新决策，不静默替换客户端。

| 接入能力 | 必须单独证明的事实 |
| --- | --- |
| 登录/绑定 | 测试账号确实已登录，通道连接的就是该账号；多账号时明确指定，不能猜最近账号 |
| 私聊接收 | 能收到主人发给 AI 的新文本，正确识别 sender / conversation / message ID |
| 私聊发送 | AI 经桥发送后，主人端实际收到对应测试文本；不是只看本地函数返回成功 |
| 群接收 | 普通测试群的新消息可见，群 ID、成员 ID、@ 和自身消息标记正确 |
| 主动发给主人 | 无当前入站消息时，由本地显式测试任务发往固定主人 ID |
| 群发言 | 只能在已同意测试群按明确目标发送，成员端确认收到；不能只验证私聊就推定群可发 |

收发可使用不同技术，但统一藏在 adapter 后面。OneBot 等协议只作为内部适配合同，不等于微信官方授权。企业微信机器人也不自动满足普通个人微信/普通群目标，若考虑应单列为需求变更。

桥接仅本机回环监听、具备认证和账号核验。存在版本错配、账号不明、无发送回执时停止发送。暂不选定 WeChatPadPro、WeChat-Hook 或 puppet-xp，先完成来源、许可和能力审查。

## 7. 推荐模块与数据流（本项目拟议设计）

```text
WeChatAdapter -> normalized event -> policy 入站过滤 -> inbox / dedup
  -> per-conversation buffer -> context builder -> 自有异步 runtime
  -> decision / LLM adapter -> action proposal -> policy 出站复核
  -> transactional outbox -> delivery worker -> WeChatAdapter
  -> delivery evidence / memory / audit

owner-authorized scheduler -> proactive eligibility -> 同一决策与投递链路
```

| 模块 | 职责与边界 |
| --- | --- |
| `channels/WeChatAdapter` | connect/disconnect、receive、send_text、health、capabilities、receipt 查询；把特定桥协议归一化；不生成回复、不管理人格 |
| `providers/codex/` | Codex OAuth、受保护凭据、刷新/退出、账号模型目录、Responses 流及请求字段白名单；对应 H1/H2，不直接发微信 |
| `agent/runtime/` | 连接自有框架、会话执行锁、取消、超时、背压和生命周期；不另起第三方 Agent 循环 |
| `agent/decision/` | 输出 SILENT / REPLY / INITIATE_DM 建议；严格校验结构，不授予权限 |
| `agent/context_builder/` | 只装入当前作用域历史、受信人格、相关记忆、发送状态及预算；将群原文标为不受信数据 |
| `memory/` | SQLite 事务、作用域查询、证据、保留/删除；owner / group / agent 分离 |
| `autonomy/` | 显式授权的 scheduler 和 proactive engine；时间、闲置、去重、撤销；M1 主动目标仅主人 |
| `messaging/inbox/` | 持久输入、规范化、重复事件过滤、消费位置；自发消息不得形成回声循环 |
| `messaging/outbox/` | 持久草稿、动作 ID、目标、有效期、状态；与决策提交在事务内衔接 |
| `messaging/delivery/` | 顺序发送、回执核对、不确定状态对账、有限重试；不自行重新调用模型生成 |
| `policy/` | 每群白名单、主人身份、同意记录、动作权限、所选模型路线的场景范围、冷却/限频和总停用开关 |
| `observability/` | 脱敏日志、指标、拒绝原因、决策与投递关联 ID、错误诊断；不记录密钥或内部推理 |
| `tests/` | 合成群聊回放、模拟 adapter / LLM、策略、隔离、投递故障、桥接及 OAuth 合同验收 |

### 7.1 初步目录树

语言与文件扩展名随用户现有框架确定；以下不是另建框架或立即创建全部目录的指令。

```text
WeChatAgent/
├─ PROJECT_PLAN.md
├─ config/                 # example 配置；不放真实账号/凭据
├─ src/
│  ├─ channels/            # WeChatAdapter、fake adapter、候选后端隔离实现
│  ├─ llm/                 # siwc_oauth、token_store、responses_adapter
│  ├─ agent/
│  │  ├─ runtime/          # 接用户自有异步框架
│  │  ├─ decision/
│  │  └─ context_builder/
│  ├─ memory/              # SQLite repositories、作用域、删除/保留策略
│  ├─ autonomy/            # scheduler、proactive_engine
│  ├─ messaging/
│  │  ├─ inbox/
│  │  ├─ outbox/
│  │  └─ delivery/
│  ├─ policy/
│  └─ observability/
├─ tests/
│  ├─ unit/
│  ├─ contract/
│  ├─ replay/fixtures/     # 合成或获许可且脱敏的数据
│  └─ integration/        # 默认不连接真实微信/模型
├─ docs/evidence/          # 版本、许可审查、PoC 报告及脱敏证据
├─ docs/decisions/         # 后续决策记录；不得与本基线形成冲突双版本
└─ data/                  # 本机 SQLite/状态；忽略提交；凭据另用系统保护存储
```

### 7.2 统一事件与执行模型

入站至少包含 `platform`、`account_id`、`conversation_type`、`conversation_id`、`sender_id`、`message_id`、`occurred_at`、`received_at`、`text`、`mention_self`、`is_self` 和可靠来源标记。使用原始稳定 ID；昵称只用于显示。无法可靠识别账号/目标的事件拒绝发言，不让模型猜群名或微信号。

同一会话一次最多一个决策任务，收发均有背压和有界队列；不同群可并发但受全局模型预算及投递配额约束。所有后台任务由用户框架生命周期托管，退出可取消；不能留下无人管理的循环。

## 8. 决策、上下文、权限与自主性

### 8.1 三种动作

| 动作 | 含义 | 程序约束 |
| --- | --- | --- |
| `SILENT` | 本轮不发送，可以等待后续上下文 | 不创建可发送 outbox；沉默也需遵守存储策略 |
| `REPLY` | 回复当前私聊或当前已授权群中的上下文 | 目标由程序绑定当前会话；不能被模型改到另一群/其他联系人 |
| `INITIATE_DM` | 主动私聊主人 | 目标只能是配置中的 `owner_id`；需主人主动任务授权、时段和配额通过；群内容不自动触发 SIWC 调用 |

M1 不设“主动向任意群开新话题”动作。群内无需 @ 的自然插话以 `REPLY` 对当前批次提出建议；是否可用 SIWC 仍受第 5.5 节限制。

候选建议示意：

```json
{
  "action": "REPLY",
  "context_version": "<程序传入的快照标识>",
  "text": "这里是拟发送的简短回复",
  "reason_code": "direct_question"
}
```

只接受固定 schema、动作枚举、合理长度与已知 reason_code；不接受模型提供的收件人、Shell 命令、权限变更或新工具。`SILENT` 必须无正文。`INITIATE_DM` 的 owner 目标由程序注入。schema 支持方式需按 SIWC 验证；即使不支持服务端结构化输出，也要本地严格解析；非法输出保持沉默，不能把原始 JSON 或解释性分析发给群友。

### 8.2 群消息缓冲与限频

以下为本项目**初始可调测试值**，不是微信风控安全阈值，也不是照搬参考项目默认值：

- 只有白名单且完成同意记录的群才允许进入上下文缓冲；M1 从一个小测试群开始。
- 每群以约 3 秒短窗口合并消息，最长约 10 秒出批；有长度上限，避免持续刷消息使决策无限延后。
- 本地先过滤自身回声、重复、过期消息、无授权群及无需处理的系统事件；被 @ 只提高相关性，不提升权限或绕过限频。
- 每群两次发送至少间隔 30 秒，每 10 分钟最多 3 条，每日最多 20 条；主人主动私聊最多 2 次/日、间隔至少 4 小时、默认仅 09:00–22:00（Asia/Shanghai）。
- 模型调用与实际发送分别计限额，避免“模型大量沉默、仍耗光订阅”；M1 可先设全局最多 30 次模型调用/日，实际值由主人确认。
- 不因新消息累积突破配额；过期草稿直接丢弃，不在恢复后批量补答。冷却中保留必要上下文但不逐条排队发送。
- 发送前复核新消息/快照、白名单、同意、停用开关、账号和配额。生成主动私聊期间若主人发来消息，取消未发送主动草稿，正常回复优先。

### 8.3 知情同意与 AI 身份

每群存独立许可记录：稳定群 ID、管理员许可依据、告知内容版本、成员知情/反对处理方式、允许观察/上云/记忆/发言范围、时间与撤销状态。管理员同意不自动代替成员必要的知情同意。告知 AI 身份、哪些数据发往 OpenAI、记忆保留/删除方式；新增成员、范围变更或撤销时重新评估，必要时暂停该群。

AI 账号资料和入群说明明确其身份；人格不能声称是真人或捏造线下经历。提供“停止/暂停/忘记本群或本人”的本地规则路径，撤销立即影响后续上下文、推理和待发消息。群文本、转发和引用都只是资料，不能修改这些设置；权限修改只通过经过身份校验的主人控制入口。

策略执行顺序：**账号/来源核验 → 同意与白名单 → 所选模型路线的场景范围 → 本地过滤及预算 → 上下文与模型建议 → 本地输出解析 → 出站权限再核验 → outbox → 投递**。不允许模型自己声明“已获同意”后跳过程序策略。

## 9. 记忆与可靠投递

### 9.1 SQLite 作用域

| 作用域 | 内容 | 可进入哪些上下文 |
| --- | --- | --- |
| `owner_private` | 主人私聊、偏好、明确授权的主动任务 | 主人私聊；默认不进入任何群 |
| `group:<group_id>` | 本群有限上下文、获许可的群摘要及群内成员观察 | 仅本群；不能跨群合并同一成员画像 |
| `agent` | 人格配置版本、运行状态、主动任务状态；以后可加独立日记 | 人格公共部分可用于各会话；包含私人事件的生成日记仍需原来源作用域限制 |

所有记录带 `account_id`，群内成员记忆再带 `group_id + member_id`，查询 API 强制 scope，不提供任意跨域 SQL 给模型。相同 wxid 跨群也不默认关联，主人在群里的消息属于群上下文，不自动成为私聊记忆。

建议逻辑表：`conversations`、`consents`、`inbox`、`message_batches`、`decisions`、`outbox`、`delivery_attempts`、`memories`、`memory_evidence`、`scheduled_jobs`、`policy_versions`。唯一索引、外键和事务保证状态一致；单个 SQLite 文件不等于天然隔离，隔离必须由仓库接口、查询条件和测试实现。

M1 原始测试消息默认保留不超过 7 天；长期群友记忆默认关闭，启用时单独同意，记录证据/更新时间/过期时间并支持更正删除。不推断敏感属性、心理诊断或现实身份。不把模型总结当成原始事实，不把未发送草稿写成“AI 已说过”。备份同样受作用域和删除期限约束，日志默认只留 ID 和原因。

### 9.2 Inbox / Outbox / Delivery 的幂等合同

1. 入站用 `account_id + conversation_id + 原始 message_id` 做唯一键；缺少稳定 ID 时只能做有限辅助去重，必须记录可靠性不足，不伪造强保证。
2. 用批次 ID、上下文版本、策略版本和动作序号生成稳定 `action_id / delivery_id`。重启恢复同一草稿，不重新生成新消息；同文但不同合法任务不能仅因文本相同永远被合并。
3. 决策及待发 outbox 在事务内提交。状态至少包含 `DRAFT / QUEUED / SENDING / CONFIRMED / UNCONFIRMED / FAILED / CANCELLED / EXPIRED`。
4. 每次真正提交前核验目标、账号、许可、冷却、TTL 和上下文。M1 草稿可先设 2 分钟 TTL；超时只标过期，不自动补发。
5. 桥接返回“已提交”不等于成功送达。结合桥回执、原始发出消息 ID 和接收端测试证据核验；不能声称对方已读。
6. 若发送后超时或进程崩溃，状态为 `UNCONFIRMED`，先对账；无法查证时停下等待人工处理，不盲目再次发送。传输层没有幂等/回执能力时，不能保证端到端 exactly-once；本项目保证持久动作不重复创建，并尽量抑制重复副作用。
7. 明确尚未提交且可重试的故障才做有界退避；不确定发送、账号错配、撤销许可或版本不匹配不自动重试。重启不重放离线期间全部旧消息和主动任务。

## 10. 第一阶段仅 M0 / M1：顺序、关口与验收

顺序更新：第 18 节允许模型路线 H1–H4 / 独立 M1-B 先于微信 M1-A 推进。真实微信整合仍需两侧分别通过；M1-B 已同步为 Codex 验收，原 SIWC 合同见第 5 节。

**本节全部是后续开发计划，不代表本次已经执行。** 各阶段报告必须写 PASS / FAIL / BLOCKED 和证据，不能用“看起来支持”代替通过。只靠 mock 不能宣布微信实测完成，收到一个 token 也不能宣布 SIWC 完整成功。

### M0：约束与来源审查

1. 定位用户自有框架：代码路径、语言、异步任务 API、事件总线、工具权限、memory/scheduler、取消与持久化接口；画出最薄适配边界。
2. 固定并审查三个参考仓库的提交、LICENSE、上游代码来源、锁文件和传递依赖；特别核查第三方原生 Hook、安装脚本、二进制、动态下载及凭据访问。
3. 分清微信桥接协议、平台条款、OSS 许可和 SIWC 条款，四者互不替代。补查 WeChatPadPro 等候选的准确来源；未明确许可的代码不复制进入本项目。
4. 记录本机真实微信完整版本和账号；制作候选“版本 × 能力 × 权利/风险”矩阵。不安装桥接来代替审查。
5. 按所选 Codex 路线记录账号连接、模型目录和场景边界，执行 H1–H4；SIWC 条款只作对应路线的对照。

**M0 出口**：有源码/组件清单、许可与依赖审查表、用户框架适配方案、候选桥的来源与版本证据、所选模型路线的场景边界。微信候选失败可淘汰；进入微信 PoC 前应明确至少一条适用候选，这不阻止独立模型路线的开发。未解决事项不勾选为完成。

### M1-A：微信桥接 PoC，不接 LLM

只采用经 M0 审查并另行进入开发授权范围的候选，使用显式测试账号和主人/测试群。文本为固定字符串或人工输入，不能后台调用模型。

| 测试 | 初始验收标准 |
| --- | --- |
| 登录/绑定 | 至少两次启动能明确识别同一测试账号；退出/切号后不向旧目标继续发 |
| 主人私聊接收 | 连续 20 条带编号新消息正确接收，含中文、换行及短时间连发；ID/顺序/发言人可对账 |
| 主人私聊回复 | 10 次固定文本往返，主人端逐条收到；目标错误为零，重复副作用为零 |
| 群消息接收 | 一个已同意普通小群、至少两名测试成员，共 20 条文本/@消息；群与成员识别正确，无跨会话混入 |
| 主动发给主人 | 无入站消息触发下，手动创建 3 个本地一次性测试任务，主人端各收到一次 |
| 群聊发言 | 人工明确触发 5 条固定文本，只到指定测试群；成员端核对；未知群/未授权群发送拒绝 |
| 故障与恢复 | 断线、退出、重启、重复入站、发送超时各至少一例；状态准确，不盲目补发，不产生自回复循环 |

保存脱敏日志、环境指纹、发送/接收两端对账和失败说明。存在错目标、错账号、无法解释的重复发送则停止并判 FAIL。测试样本通过仅表示该环境基本可行，不证明长期稳定或账号无风险。

### M1-B：Codex 订阅最小调用，完全独立于微信

1. H1–H3 完成包结构、异步 HTTP/SSE、凭据生命周期与完整 Runtime 接线；无工具、无额外隐式模型调用。
2. 用户在浏览器完成本应用独立设备码登录；采用 Codex 客户端合同，参数与 SIWC 分开。
3. 枚举当前账号模型，完成直接 provider、真实 Runtime、重启后 Runtime 共 3 次短文本请求；完整终止与脱敏证据齐备，记录 H4a。
4. 合成服务覆盖刷新轮换、退出竞态、401/403/429/5xx、断流、超时与取消；真实刷新与轮换持久化另记 H4b，不耗尽额度制造错误。
5. 本阶段使用主人本机测试，不接微信、不用真实群聊数据；不自动切 API Key 或变更模型/计费。

**出口**：H4a 的基础调用与 H4b 的真实刷新分别标状态，细则见 [路线计划](docs/HERMES_CODEX_PLAN.md)。身份登录成功或只有部分流文本不算调用通过；3 次成功请求不表示长期稳定。

### M1-C：离线模拟群聊决策

用合成或明确获许可且脱敏的转录，先接 fake WeChatAdapter 和 mock LLM；若进行真实模型回放，单独确认该输入与发起方式被允许，不能借“回放”规避 SIWC 条款。

至少准备 30 组有预期标签的场景：无人找 AI 的闲聊、@ AI、话题已结束、多人连发、引用别人/引用 AI、自身回声、冒充主人、越权改白名单、要求泄露私聊、两群同名、群改名、群许可撤销、模型断流、定时草稿撞上新消息和重复事件。

**出口**：schema 校验通过；所有越权/跨域/无同意案例零实际发送，目标绑定与幂等测试全通过；冷却/配额/时区/草稿取消按预期执行。正常社交标签一致率先以不少于 80% 为观察目标，逐例记录误判；权限测试必须 100%，不能用总体准确率掩盖。

### M1-D：真实小测试群受控整合

只有微信 PoC、所选模型路线、离线策略与对应场景核验通过后才进入。先主人私聊 → 主人授权的主动私聊 → 群内影子模式（场景核验通过时仅记录候选、不发送）→ 人工放行少量群回复。一次只开一个已同意测试群，M1 试验发送上限 10 条/日、测试期先观察 48 小时，不开自动插件/联网/其他高权限工具。

**SIWC 群消息触发限制未解决时**：可以继续 fake/mock 群整合和无 LLM 的固定文本桥接测试；真实群的模型观察/自动判断/自动回复保持关闭，M1-D 群功能标 BLOCKED。不能仅因为“影子模式不发言”就调用订阅模型，也不能为了通过验收偷偷改为 API Key。

**最终验收**：主人私聊回复与主动私聊可对账；允许的群行为与沉默选择可解释；撤销/停用立即阻断待发；48 小时受控观察中错账号/错目标/重复发送/隐私串群为零；断线恢复不补刷屏；错误可定位、凭据不泄露。任何关键关口未过，不宣布整体项目完成。M2 及以后功能暂不排期。

## 11. 风险登记表

概率为未实测前的工程初评，不代表平台承诺；“高”影响包含账号、隐私或项目路线不可用。

| ID | 风险 / 概率 / 影响 | 触发信号 | 应对与验收证据 |
| --- | --- | --- | --- |
| R1 | 非官方接入导致账号限制；未知/高 | 登录限制、异常提示、发送失败突增 | 专用测试账号、最小范围；立即停用并人工判断，不自动规避风控；保留脱敏状态 |
| R2 | 微信升级/锁版不可登录；高/高 | 完整版本错配、Hook/读库接口失效 | 真实版本能力矩阵；不自动降级/改版本号；adapter fail closed |
| R3 | Codex 账号连接或模型不可用；基础调用已实测，长期可用性未证实 | 登录失败、推理 403、模型目录不可用 | 按 H4 独立验证并记录原因；不静默换号或计费路线 |
| R4 | 群触发用途未核验；Codex 未定、SIWC 原路线受限/高 | 准备启用真实群模型判断 | 分路线核验；SIWC 依据第 5.5 节，Codex 不机械沿用该判断；本机主人测试独立推进 |
| R5 | 仓库/Hook/二进制许可不完整；高/高 | 无项目 LICENSE、仅作者口头声明、组件授权不明 | 按提交审查、追溯上游；不复制/分发未明确获许可内容 |
| R6 | 订阅限额及延迟；中/中高 | 429/503、队列增加、频繁沉默调用 | 入模前过滤、独立调用预算、有限退避和 TTL；不自动付费切换 |
| R7 | 重复发送或状态不明；中/高 | 超时、发送后崩溃、回执丢失 | 稳定动作 ID、SQLite outbox、UNCONFIRMED 对账；不盲重试 |
| R8 | 群/私聊/账号记忆串线；中/高 | 上下文含其他 scope、同名群混淆 | 强制作用域接口、稳定 ID、隔离与泄露回放测试 |
| R9 | Prompt injection / 冒充主人；高/高 | 要求改规则、查私聊、执行命令 | 群原文不受信；身份由 adapter 证明；程序执行权限，M1 无高权限工具 |
| R10 | 凭据、解密消息或调试日志外泄；中/高 | token/密钥/原文入日志或远端服务 | 本机保护存储、最小采集、脱敏、保留期限与日志检查 |
| R11 | 打扰、刷屏或人格越界；中/中高 | 连续无人回复、投诉、追问与跨时段发言 | 同意/白名单、安静时段、冷却/日限额、停止词和总开关 |
| R12 | 自有框架接口不匹配及并发失控；未知/中高 | 双执行循环、取消不生效、任务泄漏 | M0 接口盘点；每会话串行、全局背压、取消与退出测试 |
| R13 | 第三方插件或安装脚本带来供应链副作用；未知/高 | postinstall、动态 DLL 下载、异常权限/出网 | 本次不执行；M0 静态审查，M1 不加载第三方插件 |

## 12. 待决策问题（按阻塞优先级）

1. **Codex 真实连接与场景**：H4 验证当前账号/目录/推理；群场景在真实整合前单独核验。当前无需再选择研究路线，也不设置个人项目商业申请前置步骤。
2. **自有框架接线**：路径已确认，静态接口与真实模型类型已有证据；剩余为可复现包安装、完整 Runtime 组合及上下文隔离，按 H1/H3 处理。
3. **实际微信环境**：当前完整版本/架构、测试账号、允许的运行权限和可接受风险是什么？哪个候选在此环境通过全部能力测试？
4. **来源和许可**：两个无明确项目许可证仓库能否获得许可？Hook 授权是否覆盖本项目？WeChatPadPro 的准确上游是哪一个？
5. **身份和范围**：主人稳定 wxid、最初测试群稳定 ID、群许可/成员告知流程、撤销入口如何落地？显示名称不能作为权限键。
6. **行为参数**：主动私聊主题、时段、日上限、群发送/推理配额、消息 TTL 是否采用本文测试建议值？
7. **数据策略**：原文保留期限、群友记忆是否启用、删除及备份清理规则、哪些内容可以发给 OpenAI？
8. **M1 出口后**：是否需要图片/表情、人格日记、插件、联网；各功能重新审查权限、条款、成本和验收后再排期。

上述事项按阶段解决，不阻碍 H1–H3 的独立实现；H4 时用户参与浏览器登录，微信身份与群资料在对应整合阶段补齐。

## 13. 交接和变更规则

- 后续执行者先读本文；所有“已通过”都必须有环境、版本、输入输出和脱敏证据。
- 本次目录预检结果：`D:\qieehy\WeChatAgent` 已存在且未发现文件；未发现 `D:\AGENTS.md`、`D:\qieehy\AGENTS.md`、目标目录 `AGENTS.md`。正式保存仍需再次检查同名冲突。
- 本次不覆盖任何已有文件。若最终同名文件出现，创建带日期/序号的新文件，并在交接中明确哪个文件是初始基线；不同时维护两份互相独立的设计。
- 执行顺序以第 18 节为准：先 H1–H4 / 独立 M1-B，微信 M1-A 单独验证，满足依赖后整合 M1-C/M1-D。本次计划梳理不表示已实施账户连接或真实微信发言。
- 文档中的公开链接是证据入口，不是执行命令或推荐下载安装包。许可证事实与平台条款在开发及分发前重新核查。

## 14. 公开参考链接

### OpenAI 官方

- [SIWC Quickstart][ref-siwc-quick]：适用用户、集成类型与资格范围。
- [OSS ChatGPT plan usage 概览][ref-siwc-overview]：本地/开源客户端与 host/client 概念。
- [注册与登录][ref-siwc-signin]：动态 client 注册、PKCE、回调和 scopes。
- [模型与推理][ref-siwc-inference]：账户模型目录、公开 Responses 端点和完整流。
- [预览限制][ref-siwc-limits]：当前字段、状态及工具支持边界。
- [账号、会话与用量][ref-siwc-sessions]：刷新、撤销及共享限额。
- [错误与恢复][ref-siwc-errors]：资格、限额及流中失败的处理。
- [Sign in with ChatGPT Terms][ref-siwc-terms]：本次页面标注 2026-09-29；需核对后续更新。

### 项目与素材

- [sourcepro5/WeChatAgent][ref-main]、[hcr707305003/wechat_ai_agent][ref-hcr]、[wwb45703-creator/wechaty-bot][ref-wwb]：三仓库主页；正文使用固定提交链接。
- [WeChatAgent 组件来源][ref-components]、[许可证范围][ref-main-licenses]、[原生发送契约][ref-hook]：选型前必读。
- [视频 BV1ss8R6zERG][ref-bilibili]：用户提供的灵感来源，本次未观看核验内容；不作为能力、授权或兼容性证据。

[ref-main]: https://github.com/sourcepro5/WeChatAgent
[ref-hcr]: https://github.com/hcr707305003/wechat_ai_agent
[ref-wwb]: https://github.com/wwb45703-creator/wechaty-bot
[ref-bilibili]: https://www.bilibili.com/video/BV1ss8R6zERG/
[ref-main-readme]: https://github.com/sourcepro5/WeChatAgent/blob/5cdedddc60936c550ce5c02558eb025a5350f48e/README.md
[ref-main-license]: https://github.com/sourcepro5/WeChatAgent/blob/5cdedddc60936c550ce5c02558eb025a5350f48e/LICENSE
[ref-main-licenses]: https://github.com/sourcepro5/WeChatAgent/blob/5cdedddc60936c550ce5c02558eb025a5350f48e/LICENSES.md
[ref-components]: https://github.com/sourcepro5/WeChatAgent/blob/5cdedddc60936c550ce5c02558eb025a5350f48e/components.json
[ref-main-root-package]: https://github.com/sourcepro5/WeChatAgent/blob/5cdedddc60936c550ce5c02558eb025a5350f48e/package.json
[ref-main-package]: https://github.com/sourcepro5/WeChatAgent/blob/5cdedddc60936c550ce5c02558eb025a5350f48e/packages/dsh-social-bridge-plugin/package.json
[ref-social]: https://github.com/sourcepro5/WeChatAgent/blob/5cdedddc60936c550ce5c02558eb025a5350f48e/components/qq-bridge/src/core/social-engine.js
[ref-proactive-code]: https://github.com/sourcepro5/WeChatAgent/blob/5cdedddc60936c550ce5c02558eb025a5350f48e/components/qq-bridge/src/core/proactive-engine.js
[ref-buffer]: https://github.com/sourcepro5/WeChatAgent/blob/5cdedddc60936c550ce5c02558eb025a5350f48e/components/qq-bridge/src/core/message-buffer.js
[ref-decision-code]: https://github.com/sourcepro5/WeChatAgent/blob/5cdedddc60936c550ce5c02558eb025a5350f48e/components/qq-bridge/src/core/decision-format.js
[ref-proactive-doc]: https://github.com/sourcepro5/WeChatAgent/blob/5cdedddc60936c550ce5c02558eb025a5350f48e/docs/PROACTIVE_CHAT.md
[ref-members]: https://github.com/sourcepro5/WeChatAgent/blob/5cdedddc60936c550ce5c02558eb025a5350f48e/docs/MEMBER_MEMORY.md
[ref-notebook]: https://github.com/sourcepro5/WeChatAgent/blob/5cdedddc60936c550ce5c02558eb025a5350f48e/docs/FISH_NOTEBOOK.md
[ref-plugins]: https://github.com/sourcepro5/WeChatAgent/blob/5cdedddc60936c550ce5c02558eb025a5350f48e/docs/PLUGIN_DEVELOPMENT.md
[ref-hook]: https://github.com/sourcepro5/WeChatAgent/blob/5cdedddc60936c550ce5c02558eb025a5350f48e/docs/WECHAT_HOOK.md
[ref-hcr-readme]: https://github.com/hcr707305003/wechat_ai_agent/blob/568a1ea2f95b081c0212e3271441da8a6ec31e13/README.md
[ref-hcr-package]: https://github.com/hcr707305003/wechat_ai_agent/blob/568a1ea2f95b081c0212e3271441da8a6ec31e13/pyproject.toml
[ref-hcr-codex]: https://github.com/hcr707305003/wechat_ai_agent/blob/568a1ea2f95b081c0212e3271441da8a6ec31e13/agent_bridge/agents/codex.py
[ref-wwb-readme]: https://github.com/wwb45703-creator/wechaty-bot/blob/bd40f5e1c056b483cb166bdec2b793372c838b9e/README.md
[ref-wwb-package]: https://github.com/wwb45703-creator/wechaty-bot/blob/bd40f5e1c056b483cb166bdec2b793372c838b9e/package.json
[ref-wwb-room]: https://github.com/wwb45703-creator/wechaty-bot/blob/bd40f5e1c056b483cb166bdec2b793372c838b9e/src/handlers/room.js
[ref-wwb-memory]: https://github.com/wwb45703-creator/wechaty-bot/blob/bd40f5e1c056b483cb166bdec2b793372c838b9e/src/memory.js
[ref-wwb-scheduler]: https://github.com/wwb45703-creator/wechaty-bot/blob/bd40f5e1c056b483cb166bdec2b793372c838b9e/src/scheduler.js
[ref-siwc-quick]: https://developers.openai.com/siwc/quickstart
[ref-siwc-overview]: https://developers.openai.com/siwc/token-sharing-open-source
[ref-siwc-signin]: https://developers.openai.com/siwc/token-sharing-open-source/sign-in
[ref-siwc-inference]: https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference
[ref-siwc-limits]: https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations
[ref-siwc-sessions]: https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions
[ref-siwc-errors]: https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery
[ref-siwc-terms]: https://openai.com/policies/sign-in-with-chatgpt-terms/

## 15. 2026-10-26 可行性评估与执行细化

用户本轮确认自有框架为 `D:\qieehy\AI_agent`，并补充：与微信平台无关的部分应广泛参考 GitHub 的 QQ 等项目，重点评估 LLM Prompt、人格及前端。此确认补齐第 1.2 节的框架路径，不改变“自有框架为核心”的约束；参考 QQ 项目不自动新增真实 QQ 接入。

- [可行性与执行计划](docs/FEASIBILITY_AND_EXECUTION.md)：本机框架静态发现、适配缺口、资源所有权、任务依赖、工作量与验收状态。
- [平台无关能力参考评估](docs/REFERENCE_REVIEW.md)：MaiBot、AstrBot、SillyTavern、Koishi 与原有参考的分工，Prompt/人格/前端取舍及逐模块复用审查。

本轮只新增分析和计划文档；未修改 AI_agent、安装桥接或依赖、登录 OAuth、调用模型、读取聊天数据或发送消息。新增公开项目为选定页面/源码抽样，尚未统一固定提交或完成依赖/许可审计；原始基线的固定提交审查不等于这些新项目已完成同等审查。

执行细化：保持 M0 → M1-A → M1-B → M1-C → M1-D 的真实验收门禁；M0 形成接口后，允许提前推进 M1-C 的纯离线核心、人格和 UI 沙盒，不必等待微信候选或 SIWC 实账号才能开展这些不依赖外部账号的工作。理由是降低平台无关部分的不确定性并响应本轮优先级；提前建设不代表提前通过里程碑，不开放真实群模型观察或发送。

参数澄清：第 8.2 节群日上限 20 条与第 10 节 M1-D 试验上限 10 条同时适用时，取更严格的 **10 条/日**。SIWC 群消息触发限制仍保留，不能因通用部分可行就宣称完整目标已可上线。

## 16. SIWC 个人自用澄清与 Hermes 候选

按 [OpenAI 官方说明](https://developers.openai.com/cookbook/articles/sign-in-with-chatgpt)，本地个人项目属于支持的使用类别，不能仅因本项目私人自用就要求先提交商业接入申请。此前“应用资格待核对”在本项目应理解为正常登录授权、账号权限与实测，不应据此凭空新增申请等待。

用户要求调查 Hermes 使用 GPT 订阅的方式；结果见 [Hermes 订阅接入评估](docs/HERMES_SUBSCRIPTION_REVIEW.md)。其 `openai-codex` provider 默认使用 Codex OAuth 后端，与第 5 节 SIWC 公共 Responses 路线分开记录；仅新增候选，尚未批准替换路线或引入其 runtime。Subscription Proxy 与完整 API Server 的能力也分别评估，不能混为一个可直接接入的 GPT 代理。

本轮仅公开资料/源码静态研究及文档更新；未安装、登录、读取认证缓存、调用模型或连接微信。Hermes 能力说明尚未转化为本项目运行证据，群自动问答的适用范围也未因此确认。

## 17. 优先研究 Hermes Codex 订阅路线

用户随后明确“开始研究这条先”。研发验证优先级因此调整为 Hermes 原生 Codex OAuth/Responses provider；SIWC 保留为对照路线。复用 AI_agent 核心、无真实微信默认连接的边界不变；本项研究不等于已经完成模型路线的真实部署验收。

研究结果见 [固定源码研究与离线验证](docs/HERMES_CODEX_RESEARCH.md)：固定上游 `e82bf01eec9bb58848209b07f0bc7d8895a39f9b`，保存并校验 18 份静态源码/许可/测试样本；新增独立研究探针，导入实际框架 llm 包后，19 项离线合同测试通过。没有安装/运行 Hermes、修改 AI_agent、构造其 Settings、读取账号凭据或发起真实模型/微信请求。

原第 5 节 SIWC 的动态注册、scopes、端点和应用合同仅适用于 SIWC，不机械套用到 Codex provider。Codex 设备码/固定客户端及请求合同单列于新报告；后续按独立凭据、异步传输、完整终止事件及框架接线分步实施，真实订阅连接仍记“待验证”。

## 18. Hermes 路线梳理与实施顺序

用户要求对该路线进行梳理与计划。当前执行入口为 [Hermes Codex 订阅路线实施计划](docs/HERMES_CODEX_PLAN.md)：同进程的薄 provider 接入原 AI_agent，独立设备码登录、受保护凭据、异步 HTTP/SSE 和完整结果转换，先交付本机文本对话入口。

实施顺序：H1 包结构与传输 → H2 认证与刷新 → H3 完整 Runtime 接线 → H4 真实订阅验收。H4a 的 3 次真实短文本请求/重启恢复与 H4b 的真实令牌刷新分别记录；19 项离线研究测试不能替代这两项。

这项调整将原 E-03/M1-B 的优先实现从 SIWC 改为 Codex，并解除其对微信 M1-A 的串行依赖；不是删除微信或群功能验收。群友问题回复仍需场景核验、社交策略和微信投递的独立证据，当前不记录为已解决，也不沿用 SIWC 专属 BLOCKED 作为 Codex 整条路线的状态。

路线估算约 5–8 人日，含缓冲约 6–10 人日，真实令牌周期等待另计；与原 E-03/F-01/F-09 存在重叠，不能直接加到旧总工期。下一可执行切片为 H1；本次仅梳理与更新计划，未开始生产实现或真实登录。

## 19. H1 实施与验收完成

用户要求“h1开始”后，已建立独立 `wechat_agent` Python 包和 `.venv`，实现 Codex 文本 provider、异步 HTTP/SSE、完整结果校验、错误分类、有界执行/排队、取消与清理；安装后的 `wechat-agent demo` 通过一次真实 loopback HTTP 请求返回实际框架 `llm.models.LLMResult`。

AI_agent 原打包配置漏掉必需的 observability 包；本项目在构建暂存区加入 observability/prompts 的包发现修正，形成正常安装的本地 wheel，未修改原框架。`framework.lock.json` 固定 63 份原始文件与修正版 wheel 的哈希，重建一致；`requirements.lock` 固定 49 个依赖的实际 wheel 哈希。

最终验收：43 项测试、814 子用例通过，无失败/错误/跳过；Ruff 与 pip check 通过。详细行为、Windows 环境复现、限制与证据见 [H1 实现记录](docs/H1_IMPLEMENTATION.md)及[验收 JSON](docs/evidence/h1-result.json)。

本次只验证合成凭据/消息及本机 HTTP，未运行完整 Runtime、登录 OAuth、调用真实模型或连接微信。H1 已完成；下一切片为 H2 认证与凭据生命周期，H3/H4 仍按原出口验收。

## 20. H2 认证与凭据生命周期完成

用户继续实施后，已加入独立设备码登录、换码、Windows 当前用户 DPAPI 存储、独占进程所有权、原子保存、令牌刷新、状态查询和本地退出。命令入口新增 `auth login/status/logout`；真实登录命令已接线，但本次仅使用合成认证服务验收，未访问真实账号。

刷新前持久写入无旧令牌的 pending 标记；成功后先保存轮换 pair，再向调用者提供会话。等待者取消不会丢弃刷新保存；重启发现状态不明则要求重新登录；退出更换代际，迟到登录/刷新不能恢复已退出会话。429 持久暂停，损坏 vault 和保存失败均停止新调用。

新增 44 项 H2 测试，与 H1 一起 **87 tests / 846 subtests 全部通过**；Ruff、pip check 通过。真实 Windows DPAPI、跨进程排他与进程中断、合成 OAuth、实际 CLI、H1/H2 provider 合成整合均有证据，见 [H2 实现](docs/H2_IMPLEMENTATION.md)及[验收 JSON](docs/evidence/h2-result.json)。

未增加第三方依赖或修改原 AI_agent。H3 下一步接完整 Runtime 和上下文/关闭组合；H4 再验证真实账号目录、订阅推理及真实刷新。H2 的原生系统与合成协议通过不替代 H4，也不代表微信整合已完成。

## 21. H3 完整 Runtime 与本机多轮验收完成

2026-10-27，已把 H1/H2 接入锁定版本的真实 `AI_agent Runtime.run_async`，新增本机 `chat --mode mock` 入口和显式 live 入口。多轮上下文按账号、平台、私聊/群与会话 ID 隔离，并绑定当前登录代际；候选回复只有在显示回调确认后才进入 RAM 往返历史。退出协调 Runtime、推理连接和凭据所有权，无默认工具、额外模型客户端或 embedding worker。

H3 同时实现一次受控模型 401 刷新/重发、二次拒绝终止、身份切换前的 HTTP 检查、取消和退出清理；完整 Runtime 启动所需的公开分词表按固定哈希随包携带，冷启动不隐式下载。正式 inbox/outbox、微信投递回执、人格管理与图形控制台仍未完成。

按用户“只做关键测试”的要求，本阶段只保留 **6 个关键验收场景**。做过一次整体 93 项回归；收尾补强凭据快照版本判断后，仅复验这 6 个场景，全部通过，不重复其余 87 项。两次验证的代码指纹、记录和限制分别保存，见 [H3 实现](docs/H3_IMPLEMENTATION.md)及[验收 JSON](docs/evidence/h3-result.json)。

本项目未修改原 AI_agent。收尾时相邻框架已有 9 个文件偏离 H1 快照；实际安装的 61 个 Python 文件和原 wheel 仍匹配锁定版本，因此保留既有依赖，未把新变化计入本次验收。下一项为 H4：账号模型目录、独立真实登录、3 次短文本及重启恢复；真实刷新单列观察。微信、群友问题回复继续按独立场景验收。

## 22. H4 实施记录（首次拒绝已解决，当前 H4a 通过）

用户启动 H4 后，已实现实时账号 `models` 目录命令，以及 `verify --stage provider|runtime|restart` 分步验收入口。每步核对当前目录、模型与登录代际，记录实际 HTTP 尝试数和不含原文/令牌的摘要；已有记录文件阻止误重复执行。新增 3 个关键场景及 1 个受影响的既有 401 场景全部通过，没有重复全量回归。

实际独立设备码登录在生成代码前返回 HTTP 403。随后一次脱敏诊断确认错误码为 `unsupported_country_region_territory`，并非浏览器挑战；这是本次请求的真实外部阻塞，不是沿用 SIWC 的判断，也不代表需商业接入申请。在该首次阶段尚未完成真实登录、取得真实目录或调用模型；后续结果见 22.2。

记录见 [H4 实现与恢复步骤](docs/H4_IMPLEMENTATION.md)及[验收 JSON](docs/evidence/h4-result.json)。下一步是在认证服务支持的合法使用环境中恢复正常授权，再完成三个独立进程的短文本请求。平台无关开发可继续，真实微信整合仍须满足各自验收条件。

### 22.1 用户代理配置排查与修正

用户说明使用 Hiddify 后，检查确认执行进程没有代理变量，三个 HTTP 客户端均禁用环境代理。实际用户的 Windows 系统代理为 `127.0.0.1:12334`；沙箱注册表曾显示代理关闭，不能据此认定用户系统代理未开启。同时运行的 Clash `7897` 入口出现 TLS EOF。

新增 `WECHAT_AGENT_PROXY` 专用变量，显式接入当前 Hiddify 端口，统一用于认证、目录和推理。通过实际代理已经取得真实设备码，首次直连的地区拒绝不再作为当前总阻塞；该阶段曾等待用户浏览器授权，随后已完成。仅复验 3 个 H4 关键场景全部通过，合成服务不会误走代理。后续真实模型和刷新仍按 H4a/H4b 各自出口确认。


### 22.2 H4a 真实订阅调用通过

经 Hiddify 完成独立登录、实时获取 8 个可见模型并选定 `gpt-6.1-sol`。真实响应缺少 Content-Type，并包含 response.metadata；补齐这两处兼容，保留有界 SSE、响应身份和完整结束校验，新增一个关键回归场景通过。

直接 provider、真实 AI_agent Runtime 中文人格回复、前进程退出后的新进程登录恢复三项均通过；源码、模型与登录代际一致，每步一次 HTTP 请求。早期失败和诊断记录保留，不将整个排障过程误记为仅三次调用。详见 [H4 证据](docs/evidence/h4-result.json)。

当前本机订阅文本入口可用，H4b 真实刷新在自然生命周期中观察，无需再次登录或重跑成功验收。后续可推进平台无关的消息账本、社交行为、人格管理与 UI；微信桥接和真实群回复仍需独立验收。

### 22.3 代理持久配置及 review 修复

新增项目本地 `wechat-agent.local.toml`，从项目目录启动自动采用已保存的 Hiddify 地址，无需每次设置环境变量。后续 review 的 R1–R3 已修复：构建先校验且保护旧包、聊天可恢复错误保留上下文、失败与取消的验收记录保留阶段及 HTTP 次数。只执行 3 个新增关键场景及 1 个受影响的既有场景，全部通过；未重复真实订阅请求。详见 [review 修复记录](docs/REVIEW_2026-10-27.md)。下一切片仍为平台无关社交核心，H4b 自然刷新单列观察。

## 23. S1 平台无关消息闭环通过离线验收

已完成首个合成主人私聊切片：policy/inbox 去重 → 实际 AI_agent Runtime + Mock LLM → 严格 SILENT/REPLY → SQLite 原子决策/outbox → FakeAdapter → 确认与恢复。稳定账号/会话键隔离；目标由程序绑定；撤销、暂停、新上下文及 TTL 阻止旧草稿提交；进程崩溃后的 SENDING 变为 UNCONFIRMED，不自动重发。

5 个关键场景通过，包括真实子进程在模拟外部提交后崩溃；安装后 `social-demo` 已运行。合成演示 2 次模型调用、1 次 FakeAdapter 提交、0 次真实模型请求、0 次真实消息。详见 [S1 实现和限制](docs/S1_SOCIAL_CORE.md)。

F-02/03/04/05/08/09 仅完成该切片所需子集，不宣称全部任务或微信整合完成。下一步为人格版本及本地回放/管理界面；群缓冲、限频、主动调度、保留/删除、人工对账和真实桥仍待后续实施。H4b 自然刷新单列观察。

## 24. S2 人格版本与本地工作台通过离线验收

已实现不可变人格版本、提示词/上下文快照、乐观版本检查和旧草稿失效；既有 SQLite 账本自动备份并迁移至 schema 2。新增 `console` 入口，提供工作台、人格设定和消息回放三页，支持生成合成草稿、显式模拟投递、回执检查、去重回放和总暂停。

服务只监听本机 127.0.0.1:8765，校验访问凭证、Host 与写入 Origin，不调用真实模型或微信。3 个新关键场景及 5 个受影响 S1 场景最终通过；浏览器完整流程通过，并修复投递后状态提示未同步的问题。详见 [S2 实现与验证](docs/S2_PERSONA_CONSOLE.md)。

F-06/U-01/U-02 完成主人沙盒子集，尚非五页完整管理端。下一切片为显式真实模型的本地人格预演，复用已通过 H4 的订阅连接和持久代理；外部投递继续与该切片分开。群策略、微信桥和真实群回复仍待各自验收，H4b 自然刷新单列观察。

## 25. S3 显式真实模型人格预演完成

人格页新增单轮真实预演，CLI 通过 `--live-model` 显式开放。仅按钮操作触发订阅请求，复用已有账号和代理；历史模拟对话不提交，结果不进入消息发送队列。独立预演账本保存请求身份与状态，重复/重启不补发；并发、版本变更、暂停和中断均有确定行为。

3 个新增与 3 个受影响关键场景全部通过。浏览器完成一次 `gpt-6.1-sol` 人格 v2 真实预演，实际模型 HTTP 尝试 1 次、外部消息 0 次；旧账本自动备份升级且原记录保留。详见 [S3 记录](docs/S3_LIVE_PREVIEW.md)。

下一切片为合成群的被动触发、缓冲、限频与预算策略，继续用 FakeAdapter 验证。真实群适用范围、微信桥、主动调度及 H4b 刷新仍未因此完成。

## 26. S4 合成群被动策略与群沙盒完成

新增合成群许可、明确 @/本群确认引用触发、3 秒安静窗口/10 秒最长缓冲、输入与任务上限。复用原 Runtime 和持久 outbox；模型调用及发送分别受 30 秒、滚动 10 分钟 3 次、上海自然日 10 次预算约束。未知投递占用发送额度；重启、暂停、撤销、旧上下文均不触发补发。

群沙盒提供成员 A/B、三类消息输入和状态回放，固定使用模拟模型，即使服务同时启用了 S3 真实预演也不会调用订阅模型。schema 4 自动备份迁移并保留旧私聊、人格及预演记录。10 个关键场景通过；浏览器普通消息零模型、@ 消息 1 次模拟调用/1 次 FakeAdapter 提交通过。详见 [S4 记录](docs/S4_GROUP_SANDBOX.md)。

F-07 完成被动群缓冲子集，F-04/08 补充该子集预算与恢复；不等于主动调度、完整权限后台或真实群功能完成。下一步为本机微信版本与桥接候选兼容性核验，随后独立收发 PoC；H4b 自然刷新仍单列观察。

## 27. 本机微信版本与桥接候选调查完成

2026-10-28，本机启动器与核心 DLL 均为 Weixin 4.2.0.9 x64，腾讯签名有效，版本/架构/哈希已保存。固定六个候选仓库提交并静态审查版本、消息身份和发送返回；三个版本绑定桥未覆盖本机版本，UI Automation 路线仍需实测。优先参考 pywechat 的新版 pyweixin 做 W1 只读控件探测，后续 W2 固定会话无 LLM 收发、W3 身份与回执合同、W4 真实群闭环依次验收。

E-01 的本机环境与候选调查完成，不等于完整 M0 或 M1-A 通过。部分 UI 消息标识来自临时控件、发送成功仅为本地操作结果，不能直接映射稳定去重或 CONFIRMED。详见 [桥接核验与计划](docs/WECHAT_BRIDGE_REVIEW_2026-10-28.md)。本轮未改微信、未安装/运行候选桥、未读取聊天或发送消息；未重复模型和沙盒测试。

### 27.1 W1 窗口探测部分完成

2026-10-29，先前失败的桌面原生管道已恢复。打开已安装的微信后取得唯一目标窗口及可访问性快照；检测到登录提示，尚待用户手动登录。未操作认证、输入或发送；仅保存结构摘要，未保存原始界面文本或截图。W1 仍未全部通过，后续只需补做登录后的聊天控件探测，不重跑已通过的模型和沙盒测试。详见 [W1 记录](docs/W1_UI_PROBE.md)。

后续用户已完成登录。重新枚举并置前后，两个观察均只取得 1 个窗口、2 个窗格，未取得聊天列表、按钮或编辑控件。已解除登录等待；W1 出口仍未通过，下一项改为 W1-D 原始 UIA 控件暴露诊断，区分提供方和快照工具限制。追加静态源码已固定提交与哈希，不安装或执行候选桥。不能据此宣称 pyweixin 不兼容所有 4.2 客户端；真实身份与收发继续待证据。

### 27.2 W1-D 原始 UIA 诊断完成

新增有界只读脚本，正常桌面用户通过 .NET UIAutomationClient 取得 Raw/Control 两个视图，均为 3 节点、无异常/截断，FrameworkId=Win32；窗口可见、未最小化。结果说明缺失并非仅由快照工具过滤造成，仍不能定位聊天控件。首次沙箱脚本执行策略失败单列保留，未改变执行策略。详见 [W1-D 记录](docs/W1D_RAW_UIA_DIAGNOSIS.md)。下一项 W1-E 为控件提供方初始化来源与版本适用性评估，不重跑相同探测；W1 收发前提仍未通过。

### 27.3 W1-E 初始化源码与本机静态匹配完成

固定 wechatauto-replica 提交 6e603974bbac3bda93d88bbd6995510ae2547f5c，核验 Apache-2.0 文本与 7 份来源文件。确认其初始化会写入微信进程候选字节，部分失败分支会开启系统读屏标志；版本表没有 4.2.0.9。本项目新增纯静态 PE 检查器，在指纹匹配的本机 DLL 中找到一个靠近 Qt 无障碍相关引用的候选，尚未打开或修改微信进程内存。

上游异常恢复、恢复结果检查与弱文件缓存身份不直接复用。下一项 W1-F 为独立受控验证器及关键恢复验收，实际写入须明确授权；当前不宣称控件树已启用。详见 [W1-E 报告](docs/W1E_ACCESSIBILITY_REVIEW.md)。

### 27.4 W1-F 验证器与真实只读预检完成

实现固定 EXE/DLL 指纹、候选及进程模块绑定的默认只读验证器；显式临时实验具备写前日志、写后异常恢复、恢复回读和恢复失败独立状态。3 个关键合成场景通过。真实预检发现旧 PID 无可见窗口；恢复客户端窗口后重新核对到 PID 44256/HWND 199878，文件、内存指令和页面匹配，候选原值为 1，无写入。

当前无需启用实验，下一步先执行 --observe-only 结构属性采集；新窗口是否已登录尚未确认，不与此前已登录进程混用。用于识别登录标志的全文可访问性快照被自动审批拒绝，后续不读取 Name/Value/界面文本。详见 [W1-F 记录](docs/W1F_CONTROLLED_VERIFIER.md)。W1/W2 实际聊天与收发仍未通过。

后续只读结构采集成功：PID 44256/HWND 199878 返回根类 mmui::LoginWindow、Qt 框架，深度上限内 29 节点，无异常；确认它是新进程的登录窗口。没有读取 Name/Value 或执行写入。已补充根窗口分类，一个登录/搜索窗口不得误判为聊天能力的关键场景通过。当前等待用户恢复已登录聊天主窗，再重新绑定 PID/HWND 观察；不把登录窗控件当作 W1 聊天出口通过。

用户随后恢复原主窗。结构枚举区分出原 PID 30376/HWND 460506 的三节点窗口与另一进程的登录窗/对话框；对原主窗重新预检，候选值为 0，固定文件、模块、指令及页面匹配，无写入。已完成单次临时设 1、结构观察、恢复 0 并回读的具体实验提案，等待明确授权。无需再次登录或重复已成功的测试。

用户明确回复“同意单次临时启用并恢复”后，已核对目标和脚本指纹并执行一次：0→1 时原主窗变为 mmui::MainWindow，采得 51 节点（深度 8 截断、无异常）；恢复 0 并回读成功。独立只读复查确认仍为 0，恢复为 3 节点外壳。没有保留启用状态，没有消息发送或模型调用。临时启用机制在本机得到实证，但深层 Edit/List 与身份/收发尚未验收；后续采集若需再次启用须另行明确授权，不能复用本次一次性许可。

### 27.5 whale_gril 参考评估完成，调整后续桥接计划

2026-10-30，用户更正参考对象为 Banan1cee/whale_gril。固定提交 e14a8c2fbf0b751ae3a9478f94688ea954d974db，149 份文件完成来源校验。它基于 wechatauto-replica 的数据库读取与 UIA 发送，提供 Qt 外层窗口、深层 session_list/chat_input_field 和多账号绑定经验；没有找到本机 4.2.0.9 的运行证据，且初始化仍调用进程内存标志启用。未安装、运行桥接或再次启用微信。

将它作为主要业务桥接参考，保留 AI_agent、Hermes/Codex、现有 inbox/outbox 和工作台。普通文本的多轮重试、额外回车、前 12 字回显确认、名称路由，以及线程超时后仍可能继续操作的路径需要改写；较新的 sticker_transport 可参考其单次动作、草稿保护和未确认状态，但本地数据库回显不等于对端送达。

实施顺序：W1-G1 完善固定目标的有界结构探针 → W1-G2 纯离线身份/事件/投递合同 → W2-A 限定账号和会话的接收验证 → W2-B 单次唯一标记文本与回显/对端证据 → W3/W4 真实闭环。依赖精确版本、数据库密钥/缓存处理和实际身份能力分别验证；不直接复用其自动回复、重试或主动分享循环。详细范围、源码定位和三个关键离线场景见 [whale_gril 评估](docs/WHALE_GRIL_REVIEW_2026-10-30.md)。

详细实施基线已补充到 [微信接入实施计划](docs/WECHAT_BRIDGE_IMPLEMENTATION_PLAN.md)：V0 为真实固定文本 PoC，V1 为主人私聊草稿/确认发送闭环，V2 为受控群聊。新增计划包括同一 Ledger 内原子接收与游标、独立投递证据/对账、按任务短时启用与恢复、可终止的 UI 工作进程，以及保留沙盒限制的真实模式接线。本轮只完善计划，未实施新桥接功能或现场动作。


### 27.6 W1-G1

2026-11-02：固定目标的深层结构探针通过关键离线及真实验证。临时启用后采得 171 节点，找到 session_list/chat_input/navigation，无截断或异常；恢复 0 并回读通过，独立只读复查回到 3 节点。模型请求、消息发送和收藏修改均为 0。A / W1-G1 结构出口通过，真实账号/会话身份、接收、发送和回执仍待验证。

用户已同意继续既定工作，限定仅文件传输助手可发送、不修改收藏等内容；同类且未扩大的操作不重复确认。下一阶段 W1-G2 需将此限制落实为产生输入动作前的目标校验，V1/V2 的联系人及群发送验收不在当前允许范围。详见 [W1-G1](docs/W1G1_DEEP_PROBE.md)、[持续操作范围](docs/WECHAT_OPERATION_SCOPE.md)。

### 27.7 W1-G2 合同与账本基础通过离线验收

2026-11-02：新增账号/会话/来源事件合同，同 Ledger 原子接收与游标、观察模式、来源冲突停止、仅文件传输助手可领取发送动作的校验，以及独立投递证据/人工对账。消息身份不随窗口重启变化；本地回显不自动确认，人工凭据与平台消息 ID 分开；UNCONFIRMED 对账不重发。schema 5 备份迁移和故障回滚通过。

5 个新增综合场景及 13 个受影响场景，共 18 项通过；补强检查后仅补跑对应 2 项通过，Ruff 通过。未操作微信或调用真实模型。下一项为 W2-A 限定账号/会话的真实数据库接收；原生身份验证、运行期启用/恢复协调、UI 发送工作进程及工作台真实接线仍未实施。详见 [W1-G2 实现与限制](docs/W1G2_BRIDGE_CONTRACTS.md)。

### 27.8 W2-A 文件传输助手限定读取 PoC 通过

2026-11-02：当前进程账号对应唯一数据目录；cfg 主密钥派生失败后，显式 WCDB 配置路线通过两个选定分片的页认证和内存快照。修正实际预分配 WAL 的旧尾部处理，仍校验有效代际、连续校验和及提交边界。

用户手机发送的一条文件传输助手标记被精确读取，分片发送者映射为本人，正确落为 self_echo，不进入决策/发送。重复回放新增 0 条；账本重开及探针退出后的独立持久化复查通过。新增 5 个综合离线场景最终通过，Ruff 通过；没有代发消息、模型调用、进程写入或收藏修改。

该成果仅覆盖固定文件传输助手/单分片的新文本 PoC；正式多分片账号代际、轮转、压缩文本、读取器生命周期及主人/群事件仍待实施。下一切片准备 W2-B 单次文件传输助手发送，仍须强制目标/草稿/焦点核验和未知投递不重发。见 [W2-A 实现与证据](docs/W2A_DATABASE_READER.md)。

### 27.9 W2-B1 单次发送控制层

2026-11-02：实现 Ledger 内持久输入/提交许可、按任务启用恢复租约、可回收子进程及有界协议、父子两侧的目标/草稿/焦点复核和严格本地回显关联。schema 6 迁移在临时测试账本中验证。固定标记通过显式 operator 来源入账，不伪造平台入站，不调用模型。新增与受影响场景最终 37 项通过，Ruff 通过。

真实 UI 后端尚未实现，当前只读可访问性快照仍为外层 3 节点；没有取得规范会话 username、可验证输入控件及发送方式。本次没有临时启用微信可访问性、发送消息或修改收藏。下一切片 W2-B2 为身份/输入能力取证、原生后端与 W2-A 快照接线，再进行单次限定发送。W2-B / V0 尚未通过。详见 [实现与验收](docs/W2B_SINGLE_SEND.md)。

### 27.10 W2-B2 真实输入能力取证

2026-11-02：新增有界原生能力探针，3 个新增综合场景加 1 个恢复回归最终通过，Ruff 通过。固定原进程/窗口临时启用后取得 147 节点、唯一输入框、空草稿读取和发送按钮候选；4 次按新增属性逐步取证均恢复 0 并回读，最后独立复查为 3 节点。

已查 AutomationId、限定 Name/Value、HelpText、ItemStatus、ItemType 等属性没有建立规范 filehelper 身份映射；资料入口已知标签和消息 ID 形态也未给出足够证据。没有实际写入、聚焦、点击或发送，没有调用模型、读取密钥或修改收藏。真实 UI 后端和 V0 尚未完成。

下一步调查新的原生身份来源，或由用户决定是否采用明确记录为人工身份核验的单次 PoC。当前没有接受显示名称直接放行，也未启用人工替代模式。详见 [取证结果与路线](docs/W2B2_INPUT_CAPABILITIES.md)。

### 27.11 W2-B3 原生对象映射与账号复核

2026-11-02：继续原生身份路线。拆出不访问密钥字段的账号读取接口，开始/结束核对均与 W2-A 验证账号指纹一致。通过固定 Qt 缓存位置、瞬时 UIA RuntimeId 管道、弱引用与 C++ 类检查，把选中行稳定映射到真实 ChatSessionCell；最后该层首次读取 568 字节，复核通过。

AutomationId 中未命中 filehelper 的 MD5；session_item 格式仍走显示文本路径；选中对象的唯一动态属性对应布尔 context_menu_connected，不提供会话身份。没有把控件对象、文本接口或 RuntimeId 宣称为规范会话键。新增/受影响 14 项离线验证通过，收尾补强后对应 1 项复验通过，Ruff 通过；最终恢复 0 与独立 3 节点复查通过。

本轮没有发送、点击/聚焦/输入、模型调用、密钥字段读取、数据库打开或收藏修改。下一项沿会话列表控制器的业务绑定查找规范会话键，并核对当前输入页；原生发送后端及 V0 尚未完成。详见 [W2-B3](docs/W2B3_NATIVE_IDENTITY.md)。

### 27.12 W2-B4 当前输入页规范身份通过

2026-11-02：从选中行与输入框的有界 QObject 父链关联同一主窗，定位输入页 ChatMessagePage。固定 DLL 元数据/分派证明 OnNewSessionDataLoaded(std::string) 与页面 +0x1D0 会话键的相等比较路径；新增静态复现脚本。运行时固定类、metaobject、vtable、代码和字符串布局，精确读到 filehelper，7,411 字节的首次对象读取及结束回读稳定。无密钥账号在前后复核中与 W2-A 页认证证据一致。

新增可复用原生会话检查，错误会话、缺失祖先/窗口归属、布局或字段变化、账号不匹配均拒绝。现场曾切到其他会话，检查停止并恢复；用户手动切回目标后通过。最终新增及相关测试 25 项、Ruff 通过，独立检查确认开关恢复 0、外层 3 节点。程序点击/聚焦/输入、真实发送、模型调用、密钥读取、数据库打开及收藏修改均为 0。

身份取证出口通过，真实 UI 后端仍未接通，W2-B / V0 尚未通过。下一项 W2-B5 将原生检查接入可终止的定向输入/提交后端，并连接 W2-A 快照基线和回显，仅做既定文件传输助手单条标记验收。详见 [W2-B4](docs/W2B4_CURRENT_SESSION_KEY.md)。

### 27.13 W2-B5 定向后端与一次手机人工确认

2026-11-02：实现仅接受 filehelper 固定标记的原生后端，持久聚焦许可、账号/原生页面/控件/草稿的动作前复核、单次 ValuePattern/InvokePattern，以及 Job 内全部工作进程退出核对。准备阶段最多 15 秒、实际动作仍最多 7 秒、回收最多 2 秒；PoC 显式外层 25 秒，默认投递时限不变。两个固定分片的认证内存基线与单次回读已接线，密钥不落盘。

第一次因冷启动在输入前超时，分阶段后因 Windows 前台请求拒绝再次在输入前停止，两次均为 0 文本许可且恢复完成。用户手动保持前台后，第三个幂等请求仅执行一次标记输入/提交；用户在手机确认完整标记，已追加人工证据并登记 CONFIRMED，未增加发送尝试或解除通道阻断。没有向其他联系人/群发送，没有模型请求或收藏修改。

新增及相关场景 63 项、Ruff 和 PowerShell 语法检查通过；最终独立复查开关 0、外层 3 节点，账号一致。自动 local_echo 仍缺失，提交后 ValuePattern 仍返回标记，不能宣称草稿已清空；W2-B / V0 不标为全部通过。下一项 W2-B6 只读诊断真实编辑器/可访问性值和限定分片回显路径，不重发、不清草稿、不解除发送阻断。详见 [W2-B5](docs/W2B5_NATIVE_SINGLE_SEND.md)。

### 27.14 W2-B6 原按钮动作接口缺陷已定位

2026-11-02：只读核对发现 ValuePattern/TextPattern 均为同一 24 字符标记；两个既定分片无新增 filehelper 记录，目录无新分片，本地编号也未增长，排除了本次单纯 ValuePattern 缓存差异及水位漏读新 local_id 的解释。

固定 DLL 证明 XOutlineButton 继承 XSkiaWidget/QWidget。对当前按钮的原生可访问接口读取 689 字节并回读稳定，运行时 vtable/方法字节与磁盘一致；ActionInterface 的 doAction 只接受 SetFocus，对 Press 直接返回。原 Invoke 路径不能作为已验证发送出口，现已在聚焦/输入/提交前关闭；未知替代实现也拒绝。补齐未来路径的提交后有界空输入双模式检查，检查失败不二次 Invoke。

新增及受影响 72 项、Ruff 和 PowerShell 语法检查通过。本轮未输入、聚焦、提交、清草稿或修改账本；历史人工确认保留，通道仍阻断。最终开关 0、外层 3 节点及账号复查通过。下一项 W2-B7 为自绘控件设计、离线验证正确的定向提交路径；条件具备前不启用真实发送或补发原动作。详见 [W2-B6](docs/W2B6_POST_SUBMIT_DIAGNOSIS.md)。

### 27.15 W2-B7 窗口定向点击候选与工作进程启动修正

2026-11-02：新增仅依赖注入 IO 的单次 HWND 定向鼠标候选，核对原生输入身份、正文、按钮命中、物理坐标/DPI、窗口代际/线程、焦点及捕获；按下后变化不向新目标释放，未知不重试。没有默认真实观察器、发送器或微信入口。

自建隐藏接收窗口验证正常 DOWN/UP、DOWN 挂起、UP 挂起，核对坐标与回收。实验识别到 Windows 虚拟环境有额外启动进程，已改为基础 Python 隔离启动并加载当前 venv 的锁定依赖，测试确认创建 PID 就是实际脚本 PID，版本和参数保留，Job 回收完成且无残留。

新增 36 项与相关回归合计 108 项通过；最后绑定参数加强后对应 36 项复验通过，Ruff 通过。本轮没有微信进程访问或 UI 操作、模型请求、草稿修改、账本写入或收藏修改。原发送路径及通道继续阻断。下一项 W2-B8 是只读几何/路由观察和自绘控件取消行为验证，未通过前不启用候选发送、不补发原动作。详见 [W2-B7](docs/W2B7_DIRECTED_CLICK_CANDIDATE.md)。

### 27.16 W2-B8 真实几何只读观察及按钮事件静态路径

2026-11-02：补齐只读几何观察器与严格的脱敏投影，前后核对原生账号/filehelper、窗口代际、控件和草稿。一次真实观察得到按钮 72×40、客户区 1276×798 物理像素、DPI 144，坐标转换和边界检查通过；微信未在前台且中心未命中目标，未通过真实命中/焦点条件，没有激活或调整窗口。草稿哈希保持，子进程回收、线程 DPI 恢复、可访问性恢复 0、独立 3 节点及无密钥账号复查通过。

固定 DLL 与 B6 历史按钮 vtable 证据的静态复现确认 event 类型 2/3 分派到按下/释放处理，以及 pressed/released/stateChanged/clicked 信号。内部左键释放分支不以既有按下位作为前置条件，不能将 Win32 取消捕获理解为自绘手势已撤销；真实窗口到 Qt 路线及取消行为仍未验证。没有向微信发送鼠标消息或启用候选。

27 个新增场景与受影响回归合计 135 个不同用例通过，Ruff 及静态复现通过。账本只读复查仍为 3 个历史尝试、1 次输入许可、1 次提交许可、1 条人工确认，草稿和发送阻断保留。下一项 W2-B9 继续只读追踪实际提交连接、窗口调度和取消/状态重置；必要时先评估单次定向事件路线，不盲试、不清草稿或补发。W2-B / V0 尚未通过。详见 [W2-B8](docs/W2B8_READONLY_GEOMETRY_AND_ROUTE.md)。

### 27.17 W2-B9 当前 clicked 连接和取消默认路线

2026-11-02：新增有界连接观察器，固定 Qt 布局指令、类/父链、信号索引及唯一接收者。修正对连接类型高位填充的错误要求后，当前按钮到同一 ChatInputView 的唯一同线程连接读取及回读稳定，首次读取 8,537 字节；回调实现与固定 DLL 的 Send(SendActionType)、参数 3 路线匹配。没有执行信号、回调或槽。

只读窗口入口查询被 Windows 以错误 5 拒绝，未提升权限或注入。固定 DLL 复现窗口类注册、消息分类及默认调度，取消/捕获丢失进入默认未处理返回及 DefWindowProcW，未证明自绘状态或事件队列清理；未观察原生过滤器，也未把静态入口绑定为当前 HWND。鼠标候选继续关闭。

新增 34 项与相关回归合计 108 passed，Ruff 和静态复现通过。两次短时启用均恢复，草稿哈希不变，独立 0/3 节点和账号复查通过；账本仍为 3 次历史尝试、各 1 次输入/提交许可及 1 条人工确认，通道阻断保持，没有新增微信动作。下一项 W2-B10 先只读调查单事件定向提交入口及目标/焦点/队列条件，再设计离线合同；不盲试或补发旧动作。详见 [W2-B9](docs/W2B9_CLICKED_CONNECTION_AND_CANCEL_ROUTE.md)。

### 27.18 W2-B10 单消息键盘资格核查与离线合同

2026-11-03：新增固定 DLL 静态复现脚本，确认输入框回车过滤分支、SendActionType 1/2 的 send 信号、到 ChatInputView::Send 的参数转发；同时确认所查字符消息产生键值 0 的按下/释放，原生按键存在记录写入、释放查找/移除及抓取对象重定向。当前偏好、实时过滤器和输入连接未观察，不将历史类绑定当作实时能力。

所查 WM_KEYDOWN/WM_CHAR/WM_KEYUP 均未取得单事件提交资格，没有新增后端或真实操作。12 项新增静态验收和 2 项既有拒绝回归共 14 passed，Ruff 通过。本轮未访问微信进程、开启可访问性或操作草稿；发件账本只读复查仍为 3 次尝试、各 1 次输入/提交许可及 1 条人工确认，发送阻断保持。

下一项 W2-B11 已写成有界完整手势离线合同，补齐持久意图和 UNKNOWN 人工对账，明确取消只作尽力清理、调用返回不等于完成。不放行真实发送；路线资格、当前草稿及通道复核仍须完成，不通过新账本绕开。详见 [W2-B10](docs/W2B10_KEYBOARD_ROUTE_QUALIFICATION.md)和 [W2-B11 合同](docs/W2B11_BOUNDED_GESTURE_CONTRACT.md)。

### 27.19 阶段 C 正式接收器与持久恢复

2026-11-03：按用户要求持续完成阶段 C，暂缓继续扩大发送研究。已实现稳定账号代际和独立分库代际、限定当前账号的分库发现、按明确 WCDB 标记处理压缩文本、父进程主账本 schema 7 的原子消息/游标/本地编号水位、独立读取子进程、单批 ACK 和可验证退出。全新读取进程按已保存游标恢复，不能因轮转或重启制造新账号代际。首次基线决策也持久化，避免登记后中断导致历史重扫或跳过。

阶段 C 已完成。原账号及固定微信进程的两个分库真实基线通过；用户确认阶段 C 范围并从手机发送唯一标记后，从保留游标准确读到 1 条，正文/发送者核对、同批幂等重放和全新读取子进程恢复同一账号代际/游标全部通过。事后另一连接只读核对仅 1 条 self_echo、2 个持久游标、无 inbox/outbox/发送尝试，绑定已停用且读取子进程均退出。11 个接收关键场景、最终全量 306 项及 848 子场景通过，Ruff 通过。分库轮转和异常中断用离线场景验证，不修改微信数据库。旧发送账本只读复查完全保持，接收器没有模型、UI 输入或发送。本次持续目标到 C 为止，D/工作台主人/真实群仍另行推进。另修复旧合成测试代理绕过 MockTransport 导致一次假凭据认证失败的问题，详细事实及完成审计见 [阶段 C 实施与验收](docs/W2A_C_RECEIVER.md)。

### 27.20 W2-B11 持久完整手势离线实现与当前现场复查

2026-11-03，用户要求继续 D。已在既有主账本 journal 中接通单次使用、DOWN/UP/取消意图及终态，子进程通过有界双向帧等待父进程提交确认后才执行；身份、固定标记、本机 operator 来源和已消费提交许可共同绑定。异常保留 UNKNOWN，取消日志失败仍尽力清理，重启不重放。当前原生后端的提交拒绝保持，没有注册真实发射器或更换账本绕过通道阻断。

新增 18 项持久管道/故障/恢复场景，自建隐藏 HWND 的三种消息实验也加入真实 SQLite 提交。相关分组累计 132 个不同用例通过；收尾新手势/定向消息 54 项复验及 Ruff 通过。三次只读租约取得新的空输入、前台/命中/焦点/DPI 以及按钮 clicked 唯一连接证据，pressed 位为 false，按钮在空输入时禁用。全部工作进程退出并恢复；独立复查 gate 0 和原账号一致。旧发送账本仍为 schema 6、3 次尝试、各 1 次输入/提交许可、1 条人工确认和原阻断原因。本轮没有模型请求、焦点操作、输入、提交、消息发送或发送账本写入。

D / V0 尚未完成：实际窗口消息入口仍未取得资格，下一项是受约束的真实目标适配及独立通道复核，随后才进行一次新的固定标记验收；不重复被拒绝的只读查询、不提权注入、清草稿或重放旧动作。详见 [W2-B11](docs/W2B11_DURABLE_GESTURE.md)。

### 27.21 W2-B12 一次性通道复核与实际新标记试验

2026-11-04：重启微信后重新核验原账号及固定 EXE/DLL。窗口过程查询仍被拒绝，补齐原探针因前项失败未执行的独立窗口类查询，类过程入口及 128 字节固定代码匹配成功；每窗口子类过程未知。针对有持久副作用意图和 UNKNOWN 阻断的一次固定试验，明确调整资格依据为标准 HWND 交付合同、当前类代码、规范 filehelper、当前按钮连接和真实命中，仍需本次新回显及手机验收，不能称为已验证的提交路线。

已实现仅显式入口可用的固定新标记原生后端、原账本 schema 8 的一次性复核、历史摘要/进程代际/期限约束、消费与新动作原子绑定、结束和崩溃后重新阻断；不注册默认发送器。原账本由 schema 6 备份迁移，旧手机确认保留。认证、准备、发送和回显共用外层短时租约，前后核对及子进程回收仍独立；操作员等待最长 60 秒，旧超时进程不能被后续回复复活。

用户明确同意仅向内置 filehelper 试验一次 `W2B-20261104-directed-once`。几次准备因页面检查或等待超时在复核/action 之前停止；随后新的“开始检查”对应当前认证和页面准备通过，创建唯一新复核/动作，取得各 1 次输入/提交许可。按钮状态复核异常，持久手势仅记录 DOWN 意图，没有 UP 意图；尽力取消后保留 UNKNOWN，未补发或改走其他输入路线。旧错误码不能区分 DOWN 前的最终检查与 DOWN 返回后的状态检查，未宣称已确定根因。

用户确认手机没有新内容、电脑有草稿；只读复查草稿哈希与新标记一致。没有唯一新回显或新的手机肯定确认，新动作/尝试均 UNCONFIRMED。原发送账本现为 4 次尝试、累计各 2 次输入/提交许可、1 次已结束复核、1 条历史人工确认和原阻断原因；活动绑定 0、完整性通过。UI/读取子进程退出，独立 gate 0 及原账号复查通过。没有清草稿、重试、模型调用、其他联系人/群发送或收藏等修改。

补充按下位缺失时不发 UP、父进程生命周期/无回显状态、手机确认清理前提，以及未知尝试不能重复领取或通过新进程绕过的关键场景，最终全量 **355 passed、848 subtests passed**，相关源码及测试 Ruff 通过。原脚本的 `directed_trial_finished` 仅表示流程返回，现已改为无回显明确报未确认并返回失败退出码；原证据不覆盖。D / V0 继续未通过，下一项先离线核查标准窗口消息到 Qt 鼠标事件及当前按钮的实际条件，并完善未知尝试/草稿的独立人工对账合同，不删除检查强行提交。详见 [W2-B12](docs/W2B12_REVIEWED_TRIAL.md)。

### 27.22 按最新上游发送器重新确定适配基础

2026-11-04，用户建议先阅读相关项目源码、直接在其基础上修改。重新固定 whale_gril `0af4dcd5596192e0fa4d3938ea93b404771d6e02` 及 wechatauto-replica `bc2e6f2461330eb1a53996d2f2afc18416376b52`，保存相关源码/许可及摘要，未安装或运行。新版桥接已迁到 `bridge/engine.py`，新增 `bridge/bg_sender.py` 后台优先通道，文档有作者报告的 4.2.0.9 实测。旧快照没有这些证据，不继续把旧版“未找到”结论套用于最新版。

上游点击明确为 PostMessage MOVE→DOWN→等待（默认 70ms）→UP；当前失败路线是 SendMessageTimeout DOWN→立即复核→UP，缺少 MOVE。作者解释 MOVE 用于 Qt 内部鼠标进入状态；这与失败有关的可能性值得优先验证，尚不认定本机根因已证实。下一项调整为以独立上游点击序列为基础做最小适配及离线验证，暂停扩展自研逆向；保留正式读取器、原生规范身份、原账本及 UNKNOWN 阻断，不整包替换项目。移除上游自动清框、编码试写、姓名路由、弱成功判定及多通道补发；队列接收也不当作点击/投递完成。旧单次许可已消费、草稿保留，本轮没有微信输入、消息、启用或新动作。具体修改范围和证据见 [上游发送适配调整](docs/UPSTREAM_SEND_ADAPTATION_2026-11-04.md)。

### 27.23 W2-B13 上游排队手势组件与离线持久接线

2026-11-04，用户同意按上游适配方案推进。已基于固定 whale_gril 的点击序列实现 PostedClick，保留 MOVE→DOWN→至少 70ms→UP，按原坐标/身份合同注入观察、投递和取消；补齐 MOVE 写前提交、ACK 后复核和有界按下位等待。MIT 版权/许可及来源摘要进入发行资源，没有引入上游依赖、清框、编码试写、伪焦点、系统读屏、全局键鼠或回退通道。真实微信排队发射器尚未注册或接线，默认与旧入口保持。

现有 journal/父子进程协议已支持新阶段及最多 5 次意图确认，区分同步结果、消息入队和取消入队；MOVE-only 中断也是 UNKNOWN，重启不重放。子进程挂起由父进程回收，队列取消不冒充目标已处理或回滚。自建隐藏 HWND 在消息循环放行前接受三条投递但尚未处理，放行后才依次收到，验证了这一边界，没有微信动作。新增 33 个关键场景，全量 **388 passed、848 subtests passed**，相关 Ruff 通过。

旧真实账本只读核对仍为 4 次尝试、1 次结束复核、累计各 2 次许可、1 条历史手机确认、活动绑定 0 及原发送阻断，完整性通过。本轮无新发送/模型请求、草稿修改或可访问性启用。旧未知尝试和草稿的人工对账合同已明确，但没有实现未确认历史的新放行。下一项接受绑定原生投递/观察和提交后状态核对，再准备独立对账与具体新现场方案。此次“同意改造”不作为下一条消息的发送许可，D / V0 仍未通过。见 [W2-B13 实现与验收](docs/W2B13_UPSTREAM_POSTED_ADAPTER.md)。

### 27.24 W2-B14 原生排队接线、独立复核与真实只读核验

2026-11-04，用户继续后已实现 NativePostedTrial / BoundPostedMessages，沿用原账号/原生 filehelper/固定程序/控件/几何合同，在 PostMessage 前再次验证窗口代际/线程/代码/参数和前台/硬件；每条消息只尝试一次，异常只有原目标尽力取消。UP 后有界核对同一编辑器两模式为空、按钮释放及捕获归零，仍不等同新回显或手机送达。新路线仅接受 `W2B-20261104-posted-once`，默认和旧定向后端保留，聚焦阶段不调用激活/聚焦回退。

旧 UNKNOWN 可在既有一次性复核 facts 中增加具名 action/ref 的独立接受记录；不写肯定投递证据，不改旧 outbox/attempt，不自动解锁。仅固定标记/operator、outbox 和 attempt 均未知且已结束回收恢复的明确旧记录适用；所有旧进程退出、新进程代际、当前原账号/filehelper/空输入等条件仍强制，未列出的未知或下一次未知不能继承。相同新请求/正文/绑定/历史摘要的动作才可在 60 秒内消费，结束和中断重新阻断；没有新增 schema/表，也未在真实账本消费新复核。

只读现场发现系统 MainWindowHandle 指向另一个同进程可见 Qt 窗口，主窗守卫拒绝；原主窗的其他会话也拒绝。用户回复“已打开，草稿保留”后，核对原账号/规范 filehelper、当前编辑器/按钮和类代码成功，草稿非空、按钮未按下。首个完整快照约 4.64 秒，同一子进程预热后约 125ms；没有简单延长按钮等待或去掉复核。其后只读草稿专用观察遇页面候选拒绝，停止且不声明已读取当时内容。没有聚焦、输入或向微信投递消息，临时租约均恢复；独立 gate 0、原账号和真实账本只读核对通过。

新增关键场景后全量 **445 passed、848 subtests passed**（182.96 秒），收尾手机确认的实际清理记录/标记摘要修正后审核与控制器 **42 passed**，相关 Ruff 通过。真实账本保持 4 次尝试、1 次结束复核、累计各 2 次许可、1 条历史确认、活动绑定 0、旧动作 UNKNOWN/UNCONFIRMED 及原发送阻断。D / V0 仍未完成；具体新单条方案已准备，后续须由用户决定旧草稿、手动处理并完整重启微信，接受具名旧不确定性且批准新一次试验，不能把本轮“继续”或只读准备当作发送许可。见 [W2-B14 具体方案](docs/W2B14_POSTED_NATIVE_TRIAL.md)。

### 27.25 W2-B14 获批单次真实试验结果

2026-11-04，用户明确批准 `W2B-20261104-posted-once` 的一次试验及具名旧不确定性审核，并报告已手动处理旧草稿/重启微信。新 PID 35300/创建代际、原账号和固定程序核对通过。读取认证后的第一次等待超时、后来的准备核对及自动前台等待均在新复核/action 前停止，不消费许可。准备方式增加有界只读前台/全原生条件等待，8 个新增关键场景与相关回归 65 passed；没有修改正文、消息次数、投递代码或放宽前台/按下位检查。

前台持续不足时明确告知，执行一次既有的受绑定聚焦准备：先核对原账号/规范 filehelper/双模式空输入，写前记录再定向聚焦输入元素，无输入、提交或全局键鼠。准备核对全部通过后，同一份有效单次批准只进入一次实际新动作。新 action `e8b2e295fb357ddf33b6df8be33b82e07aae55c124c9e3a3b1388258757ed7aa`，operation `d9d623f51a5244629db1f3ce95033aec`，新审核只接受具名旧不确定性、未确认旧投递。

输入/提交许可各 1；MOVE 和 DOWN 获得入队成功，但在有界观察内没有预期按下位，终态 `posted_button_press_not_observed`，没有 UP 意图。只尽力请求原目标取消，记录 CANCEL_ENQUEUED 而非处理/回滚确认。没有唯一新回显。用户手机确认“未收到，电脑有草稿”；只读核对当前草稿哈希为本次完整新标记，后续按钮未按下不证明之前从未处理或已经回滚。没有补发、清理或新的肯定投递证据。

单次许可现已消费，原账本 schema 8、5 次操作/尝试、2 次结束复核、累计各 3 次输入/提交许可、1 条历史手机确认、活动绑定 0；新旧两次未知均 UNCONFIRMED，发送阻断保持、完整性通过。子进程退出，各租约恢复，独立 gate 0 与原账号复查通过，模型请求 0，没有其他目标消息或收藏等修改。**D / V0 未通过**。下一项仅对照上游完整初始化及实际鼠标事件/状态采样缺口，不把入队、上游版本声明或合成测试当作本机成功，也不通过删守卫、复用许可或清草稿强行继续。见 [B14 单次结果](docs/W2B14_POSTED_NATIVE_TRIAL.md)、[账本审计](docs/evidence/w2b14-posted-consumed-trial-audit-2026-11-04.json)及 [最终恢复](docs/evidence/w2b14-posted-final-recovery-2026-11-04.json)。

### 27.26 W2-B15 离线按下采样诊断

2026-11-07，检查最新进度后继续 B14 的既定离线调查。复核固定上游源码摘要，比较完整初始化、伪焦点输入、MOVE/DOWN 间持久检查间隔及 UP 前观察差异；这些只形成待验证解释，不当作本机失败根因。修复按下等待的诊断分类：区分及时假值、观察返回已过期、调度耗尽期限及轮数上限，保留 500ms / 32 轮和全部身份/草稿/前台/按下条件。

新增 8 个慢读取、调度与目标变化场景，相关 113 项通过，修改文件 Ruff 通过；完整回归 461 passed、848 subtests passed（258.20 秒）。没有访问微信进程、当前草稿或真实账本，没有新启用/输入/消息/模型请求，也不改历史 UNKNOWN 或复用 B14 许可。D / V0 仍未通过，下一项准备只读分阶段采样耗时工具。见 [W2-B15 诊断与验证](docs/W2B15_PRESS_OBSERVATION_DIAGNOSIS.md)。

### 27.27 W2-B16 上游完整手势适配及现场只读准备

2026-11-07，用户要求继续直至完成并直接参考其他开源项目。固定最新 whale_girl 和 wechatauto-replica 源码作静态对照，继续使用已保存 MIT 快照作为代码来源。实机完整观察冷态约 5.7–6 秒、预热约 343–359ms；按下位读取处于完整观察内部，仍不能反推 B14 根因。新增独立完整手势候选：动作前持久记录所有意图并完整预检，期间原生绑定核对且不插入 UIA/父进程往返，采用上游 MOVE→DOWN→70ms→UP，动作后严格空输入/释放/捕获及新回显核对。明确这是不再以中间 pressed=true 作为 UP 前提的新试验，旧后端及默认路径保留。

新增 27 场景，全量 488 passed、848 subtests passed（299.24秒），相关 Ruff 通过。用户启动微信后，原账号/固定程序、新代际、规范 filehelper、双模式空输入及当前控件通过只读核验，当前前台不足，未输入或投递消息。原发送账本只读仍 5 次操作、2 次复核、两条 UNCONFIRMED、活动绑定 0、阻断保持且完整性通过，独立 gate=0。具体新正文 W2B-20261107-sequence-once 和新一次审核方案已准备，但尚未执行；须明确接受两条旧不确定性并批准该新单次方案。见 [W2-B16](docs/W2B16_UPSTREAM_COMPLETE_SEQUENCE.md)。D / V0 仍未通过。

### 27.28 W2-B16 单次完整手势未送达

用户明确批准并接受旧两条不确定性后，前置拒绝均未消费许可；随后将已批准的绑定聚焦纳入同一外层租约，唯一一次实际动作输入新标记后 MOVE/DOWN/UP 入队，结束复核未通过，操作原因 posted_completion_not_verified。新回显读取发生 source_changed_during_snapshot，未取得唯一证据；用户确认手机未收到、电脑有草稿。只读双模式哈希精确匹配，未清理。许可已消费，账本现 6 次操作、3 次审核、累计各 4 次许可、活动绑定 0，三条 UNCONFIRMED，独立 gate=0、原阻断和完整性保持。新增失败原因投影及控制器区分手势/后续回显错误的修正，相关回归通过。见 W2-B16 末节。

### 27.29 W2-B17 受绑定真实鼠标候选

新增显式 NativePhysicalTrial，只允许新的固定文本，完整原生身份/草稿/控件/几何/前台核验之后，一个 SendInput 调用注入两条绝对虚拟桌面鼠标事件，结束仍完整核对和独立回显；默认不注册、没有自动回退。注入数不冒充送达，部分注入/失败不重试。实现、关键回归、真实只读准备和具体方案已完成，未对微信调用 SendInput。首轮全量 515 passed + 848 subtests，2 项排队手势时钟场景失败；Windows GetTickCount64 精度被确认是相关缺口，实际完整手势改用 QPC，协议测试使用确定性时钟，包含失败场景的 81 项复验全部通过。见 [W2-B17](docs/W2B17_BOUND_PHYSICAL_CLICK.md)。

用户已明确批准真实鼠标的新一次标记，并接受新增旧不确定性；批准仍有效、尚未消费。初次检查仍是旧进程，提醒完整退出后出现新进程代际且原账号核验成功；后续新进程退出，动作在启用/审核/输入之前停止，没有真实鼠标事件或新动作。正在等待用户重新登录准备好，再按同一批准继续；不得把前置停止当作已消费或另行补发。

### 27.30 W2-B17 已取得唯一真实回显，待手机确认

用户重新登录准备后，新 PID 39560/创建代际和原账号通过；原 SetForegroundWindow-first 准备被 Windows 拒绝，仍在新审核/action 前停止。改为已绑定输入框标准 SetFocus、必要时请求原前台并实际核对焦点，82 项相关回归通过，未放宽空输入或原生身份条件。随后同一批准仅一次实际输入 W2B-20261107-physical-once，并一次 SendInput 两事件批次；新 action 866b1291579f48a91c032e3a718f2470b6975210c79b7a42ef03dad17a7d9a0d，operation 5c2101b59fb045d799bcfda45edc3600。

瞬时结束观察的 input-name 候选提示不匹配，原 UNKNOWN/filehelper_candidate_0_1_1_1 保留；同一读取器已取得唯一精确的新本人回显并记独立 local_echo。后续全新只读进程核对原账号/规范 filehelper，Value/Text 双模式均空，另一次原生连接检查按下位为假。原账本 7 次操作、4 次审核、累计各 5 次许可，活动绑定 0，三条旧 UNKNOWN 不改、新动作 UNCONFIRMED，完整性和原阻断保持；独立 gate=0、全部工作进程回收/租约恢复、模型请求 0。已请求用户手机完整文本核对，尚未回复；此时不登记肯定手机确认、不再发送，D / V0 暂不宣布通过。见 [W2-B17 实际结果](docs/W2B17_BOUND_PHYSICAL_CLICK.md)。

### 27.31 W2-B17 手机确认，限定 D / V0 PoC 通过

用户明确确认“手机已收到，电脑输入框已空”。只运行既有人工确认入口，新增独立 manual_confirmation，action 866b1291579f48a91c032e3a718f2470b6975210c79b7a42ef03dad17a7d9a0d 现 CONFIRMED，没有再次输入/点击。结合阶段 C 接收去重/恢复、本次唯一新回显、手机确认、延后双模式空输入/按钮释放以及退出/恢复证据，D / V0 的限定 filehelper 收发 PoC 通过。范围仅当前构建/前台目标会话和绑定物理输入；原瞬时结束观察拒绝保留，旧三条 UNKNOWN 不改、通道不自动解锁。后台发送、无人值守和主人/群工作台仍未通过验收。见 [B17 手机确认](docs/evidence/w2b17-physical-phone-confirmation-2026-11-07.json)及 [B17 收尾](docs/W2B17_BOUND_PHYSICAL_CLICK.md)。

### 27.32 后台操作计划与优先级调整

2026-11-07，用户要求计划实现后台操作。新增独立实施计划，首轮 BG1 为微信窗口可被遮挡、用户继续使用其他应用，后台收发不抢前台、不移动真实鼠标；最小化/托盘分为 BG2，锁屏/会话断开不作为首轮承诺。先 B18 完整复现固定上游的初始化/输入/提交/收尾，明确原版配置与补丁差异，再 B19 接入现有发送器和原账本，B20 持续接收/重启，最后 B21 验证窗口隐藏状态。后台失败不自动转前台补发，三条旧 UNKNOWN 与现有操作范围保留。

首轮最多三个正向新标记，具体正文/请求/方法和许可在准备完成后再列入有界批次；本次仅计划，不创建新发送许可、微信动作或系统状态改动。后台完成标准不以物理 PoC 或上游报告代替实机结果。README 与桥接计划同步优先级，并修正当前事实段落中的历史 D 未通过状态；旧实验证据不改写。见 [后台操作实施计划](docs/BACKGROUND_OPERATION_PLAN.md)。


### 27.33 后台操作计划 review

2026-11-07，核对固定上游源码与现有发送器后，补充 B18a 离线准入/B18b 实机门槛：显式 UTF-16 仍可能触发内部清框换编码重写，chat=None 会跳过内部目标检查，不能仅靠参数宣称约束满足。允许明确标注的受约束对照，不放宽原目标/草稿范围。新增分阶段超时、跨进程互斥、崩溃恢复矩阵、唯一回显匹配字段及 30 分钟限定持续观察标准。本次仅修订计划，后台发送仍未验收；下一项为 B18a。见 [修订计划](docs/BACKGROUND_OPERATION_PLAN.md)。

### 27.34 E7 限定文件助手工作台闭环通过

2026-11-16：在已有BG1基础上完成E0–E7的限定工作台验收。用户恢复代理并明确授权新63728来源后，通过真实工作台HTTP接口执行新消息展示、gpt-6.1-sol一次推理、保留模型版本1/保存operator编辑版本2、具体正文准备/确认和唯一一次后台发送。取得唯一新local_echo；用户确认手机完整收到且电脑输入框空，独立manual_confirmation已追加，本次action为CONFIRMED。

确认后全新读取器/只读工作台启动，fresh/replay=0、发送六表与模型计数在重启前后不变。独立原生收尾核对原账号/规范filehelper、双模式空输入、按钮释放、遮挡条件及gate0，全部工作进程回收、活动操作/绑定0、完整性ok。累计17次操作、输入/提交许可15/11，八条旧UNKNOWN的完整身份列表及原六表历史行均保持；先前目录连接失败来源不重放。本轮全量801项与848子场景通过，9项旧二进制场景跳过。正式源码未因现场操作修改；辅助脚本补齐显式绑定/模式/来源和失败退出码。

范围限原账号、微信4.2.0.13、非最小化且由其他应用遮挡的独立文件助手；实际闭环使用工作台HTTP，网页视觉验收仍为此前合成边界。本次许可已消费，原通道逐条审核，不据此开放自动回复、最小化/托盘、主人私聊或群。下一项按R1新版只读持续30分钟及中途重启推进，不重复E1–E7或登录。见 [E7完整事实与证据](docs/E7_WORKBENCH_ACCEPTANCE.md)及 [后续计划](docs/WORKBENCH_PLAN.md)。

### 27.35 R1新版30分钟只读通过，R2最小化调查取得结果

2026-11-16，用户要求持续工作。新增新版工作台专用R1观察CLI，实际1800秒、60次认证HTTP检查，三条分散新手机文本各落账一行且保持self_echo；900.219秒重启读取器/工作台，旧进程退出，新PID恢复相同账号代际/游标。3次有界快照重试全部恢复、无耗尽；观察器未见目标前台事件/采样。独立审计确认零新草稿、模型trace、发送动作，仍17次操作/许可15/11、旧八条UNKNOWN不变、E7动作CONFIRMED、活动操作/绑定0、gate0、完整性ok，全部工作进程回收。

两次R1工具启动因历史NULL正文处理错误停止且安全回收，证据保留；修复后完成正式轮。后续源码补强隐藏窗口拒绝及替代receiver校验失败的回收，实际运行快照与收尾源码分别记录哈希，不热加载混淆。最终全量812 passed、9 skipped、848 subtests passed（285.27秒），Ruff/JS通过；此前全量异步等待轮和独立群策略5项复验单列，不改写为通过。

R2由用户手动最小化独立filehelper后，MIN新文本只读接收/HTTP/读取器重启与去重通过；现有准备探针明确standalone_window_minimized拒绝、gate0。没有激活/恢复窗口、模型或程序发送；该结果不表示支持最小化发送。托盘现场仍等待用户设置，不能套用最小化结果。详见 [R1](docs/R1_WORKBENCH_OBSERVATION.md)与 [R2](docs/R2_WINDOW_STATE_READINESS.md)。

### 27.36 R2托盘合同拒绝与手动恢复游标接续通过

2026-11-16，用户收起托盘并发送新TRAY文本。原微信PID/创建代际未变，独立HWND31721126消失、主窗329642隐藏；现有读取器初始化reader_source_failed后回收。两个窗口的独立只读NativeMemory前置检查均复现selected window is not visible in target process。没有放宽可见窗口合同或改绑绕过，没有显示/激活/输入/发送；原TRAY当时尚未落账。托盘时没有合格窗口重新核验gate，保留未复核事实；该轮只读句柄无进程写入。

用户手动恢复独立filehelper并遮挡且不重发TRAY。新HWND14617104重新取得原账号、规范filehelper、双模式空输入、后台准备及gate恢复0证据后，从先前MIN已提交local_id182游标读到原TRAY一条；全新读取器恢复后fresh=0，HTTP正文核对一致。最终独立审计确认该来源全账本仅一行、零新草稿/模型trace/发送动作，原发送历史和八条UNKNOWN身份列表不变、E7仍CONFIRMED、17/15/11、活动操作/绑定0、全部工作进程回收、完整性ok，并在合格新窗口独立读回gate0。

R2窗口状态调查已完成，能力结论为最小化下本次只读接收可用、输入准备拒绝；托盘下当前合同不支持读取，恢复可见后可接续，不称为托盘支持。本轮没有修改正式源码，沿用812项与848子场景全量通过/9项预期跳过；辅助审计语法及实际只读验证通过。后续R3需明确主人目标范围，不能从filehelper范围自动扩展联系人/群。见 [R2事实](docs/R2_WINDOW_STATE_READINESS.md)及 [最终独立审计](docs/evidence/r2-window-state-final-audit-2026-11-16.json)。


### 27.37 R3指定私聊只读接收，测试对象更正为Amadues

2026-11-16。用户先选昵称LFY，后明确改用昵称Amadues并已发送PEER。新增单独的原生私聊身份探针，标签仅用于选择窗口，固定Qt页面规范会话键和根窗口交叉匹配；只输出哈希，原标识存本机仅观察文件。独立PrivateReaderSession/Catalog/worker固定observe，规范发送者在提交/ACK前复核；通用工作台构造层拒绝给该会话装配generator/model/sender，原filehelper发送白名单和游标不泛化。

原账本v9时间戳备份后升至schema10，reader_private_targets持久保存独立首次基线；迁移失败回滚和原始基线中断恢复均测试。LFY首个精确标记为self_echo，其对方同名回复在停止间隙陈旧，PEER等待超时；另有真实LFY非标记OBSERVED事实，但原失败／超时不改写。用户更换后停止旧目标，旧计划／游标／事实保留，旧配置在worker创建前拒绝。

Amadues已发PEER仅在固定近期时间窗、两个既定分库各最多32条规范peer文本内唯一精确匹配，登记新目标独立基线；不回退旧目标水位，不扫描联系人。规范peer、精确正文和一次落账通过，原批重放not fresh，全新读取器恢复同一游标／代际，fresh/replay0，认证HTTP目标／发送者／正文哈希一致。消息接收延迟840.738秒，原120秒策略保持stale_event，不伪造新鲜inbox。独立审计确认零新草稿/model trace/程序发送，原发送及workbench历史、filehelper游标不变，17/15/11、旧八条UNKNOWN、E7确认保持，活动0、gate0、工作进程回收、完整性ok。

基础完整回归851项／848子场景通过，目标切换及近期选择46项通过；最终分组完整覆盖859项（含1项15秒合成夹具超时后0.98秒孤立复验）／9跳过／848子场景。原异步等待、夹具超时及所有未完成轮日志保留，不称最终一次单进程全量通过。Ruff／JS／PS语法和wheel8模块字节对照通过，原生仍需完整源码。R3仅只读阶段取得证据，完整主人草稿/确认发送、分库轮转及自动回复未验收；当前目标Amadues，后续不得重复LFY或复用filehelper发送许可。见 [R3事实](docs/R3_PRIVATE_READONLY.md)、[真实最终审计](docs/evidence/r3-Amadues-final-audit-2026-11-16.json)和 [分组验证](docs/evidence/r3-Amadues-final-validation-2026-11-16.json)。


### 27.38 R3指定私聊本地草稿与最终正文预览

2026-11-16。Amadues既有只读范围保持。新增显式wechat-private启动模式和默认关闭的local-drafts能力，只接受当前规范peer的新鲜OBSERVED文本，创建本地手写、版本编辑和无发送许可的最终正文预览；生成、准备、确认入口逐次拒绝私聊只读范围。复用schema10和原草稿唯一约束，模型0、无outbox／审核／许可；关闭持久取消，存储失败仍回收接收器。普通sandbox与filehelper发送范围不扩大。

分组覆盖891项／848子场景通过、9跳过，含新增32项私聊本地反例；主组866、最终工作台55、独立异常1和UTF-8 HTTP23，原编码捕获失败日志保留，不称一次最终全量。Ruff、JS实际处理函数与语法、wheel6个源码资源对照通过。现场新LOCAL等待五分钟未收到并回收，未创建真实草稿，不能称现场预览通过。独立审计仍新草稿／模型0、17/15/11、旧UNKNOWN8及E7确认保持、活动0、gate0。下一步先处理用户已发LOCAL的回复，不重发既发文本、不复用封存输出、不放宽120秒；现场完成后再实现具名模型草稿范围，微信发送仍须单独接线及具体批准。详见 [本地草稿](docs/R3_LOCAL_DRAFTS.md)和 [验证v2](docs/evidence/r3-local-drafts-validation-v2-2026-11-16.json)。


### 27.39 直接使用已有私聊消息，取消强制测试码

2026-11-17。用户明确质疑反复发送指定代码，已按要求改为当前规范peer已保存的OBSERVED／stale_event文本都可手动选择用于本地草稿，保留原时间与状态，不要求120秒内或同读取进程；当前账号／数据库代际／目标／发送者／上下文和草稿版本仍复核，真实模型与发送关闭。已取消旧标记等待并核对自有进程退出，空输出保留。

选用一条已有Amadues消息，真实HTTP完成本地手写版本1、编辑版本2、最终正文预览及关闭重启失效，无confirmation_id和发送动作。57项相关测试通过；Node用一小时前stale_event验证实际点击流程及零效果请求，Ruff通过，不冒称本次重跑全量。独立审计原8张表／filehelper游标不变，新增本地草稿1、模型0、发送0，17/15/11、旧UNKNOWN8、E7确认、gate0和全部回收保持。后续不再要求用户发固定测试码。见 [当前行为及证据](docs/R3_LOCAL_DRAFTS.md)。


### 27.40 指定私聊后台直接发送

2026-11-17。按用户要求实现PrivateBackgroundSender、独立私聊身份guard／后台worker／当前窗口元数据管道，原filehelper守卫保持。工作台私聊加--wechat-send confirm，认证同源private-send接口与草稿后台单次发送按钮已接通；不截图、不激活、不使用全局键鼠，不自动重复请求。复用原schema10的outbox、attempt、操作及逐字符／Return日志，未新建旁路账本，不清除旧阻断。

实机首次在输入前采样停顿拒绝0/0并恢复；静态PE初始化移至观察开始前，阈值未放宽后，Cybergirl测试正文仅实际输入／提交一次，唯一原生self_echo通过。首次漏记attempt导致回显接口false，已依据实际日志修复关联，未重发；关键4项修复测试及1项最终关联／NULL清理拒绝复验通过，原8项初始关键检查通过，未全量重验。当前19操作／许可16/12，原6张发送表旧行与8条UNKNOWN及E7确认不变，新增动作仍UNCONFIRMED（本机回显不冒充对方确认），发送子进程回收、gate0。工作台已启用后台单次发送供使用，范围未最小化且被遮挡的独立Amadues窗口；持续自动回复／托盘尚未开放。见 [后台实现及证据](docs/R3_PRIVATE_BACKGROUND_SEND.md)。


### 27.41 群历史工作台和离线缓存

2026-11-18。从KG群一次性导出脚本提取正式精确群名历史读取器、隔离子进程与账号隔离缓存。网页查看／更新最近1至200条，支持JSON／文本导出，所有群消息以textContent显示。只有更新才启动只读原生子进程；不改原账本游标、不调用模型或发送。新history命令可独立运行，缓存不依赖微信进程；主工作台也增加/history页及相同认证／同源接口。

4项关键测试覆盖精确群匹配及200条边界、离线缓存和更新失败保留、账号隔离及HTTP鉴权／无发送接口。Ruff和JS语法通过，无全量复验。实际启动8766，当前进程清单无微信，已保存KG200条可读；尝试更新明确返回unavailable并显示原消息范围，发送／模型计数不变。不将旧缓存冒充新读取成功。见 [本轮事实](docs/evidence/group-history-workbench-20261118.json)。


### 27.42 Amadues账号自动回复qieehy

2026-11-18。用户更正为电脑登录Amadues，由Cybergirl对qieehy持续聊天，并明确批准新文本、最多16对本会话回复上下文及人格交给gpt-6.1-sol、模型回复仅后台发回qieehy，日限30可暂停。已实现新来源触发、串行生成／发送、来源唯一、确定request_id、防自身回显循环、暂停和失败停用、上下文续接及界面开关。

原生身份与此前Amadues目标／原qieehy账号交叉匹配，独立账号账本不改原qieehy旧历史。自动审批首次要求明确外发范围后，取得用户明确授权再启动；没有绕过拒绝。用户不在电脑旁，computer-use直接打开Qieehy独立窗口，随后专用原生探针核对与恢复。8765已启动，实际HTTP状态reading=true、auto_reply.enabled=true、phase=waiting，启动模型／发送0，未冒称本轮真实模型多轮聊天已验证。12项关键测试通过，未全量重复验收。见 [持续私聊](docs/PRIVATE_AUTO_REPLY.md)。


### 27.43 群聊与私聊订阅持续读取（2026-11-20）

完成项目进度整理及持续读取首阶段：明确加入群／私聊，首次唯一解析并固定规范标识；账号隔离缓存、后台串行轮询、失败退避／自动恢复、暂停／恢复／移除、订阅持久恢复、单账号服务互斥、认证API与history页面。原生快照工作进程沿用账号窗口核验，不调用模型或发送，不改原接收游标。当前保存最近200条快照，完整归档及重登自动绑定待后续。

11项持续读取关键测试通过；扩大Python回归71通过、1项旧图片草稿拒绝断言失败；前端5项、处理器冒烟、构建／语法及Ruff通过。无全量复验、无真实读取／模型／发送，无生产启动或实机多会话验收声明。见docs/CONTINUOUS_READS.md和更新后的docs/CURRENT_STATUS.md。
