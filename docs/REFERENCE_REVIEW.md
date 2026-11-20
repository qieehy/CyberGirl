# 平台无关能力参考评估

核对日期：2026-10-26（Asia/Shanghai）。本文件从属于 `../PROJECT_PLAN.md`，配合 `FEASIBILITY_AND_EXECUTION.md` 使用。

用户已明确：以 `D:\qieehy\AI_agent` 为运行核心；扩大到 QQ 等项目，重点研究平台无关的社交逻辑、LLM Prompt、人格与前端。这里的“参考”不等于引入它们的 Agent runtime，也不等于新增真实 QQ 接入需求。

补充研究：[Hermes 的 GPT 订阅接入评估](HERMES_SUBSCRIPTION_REVIEW.md)。区分 Codex OAuth provider、Subscription Proxy 与完整 API Server；只将认证/transport 列为候选，不引入 Hermes Agent 核心。

## 1. 证据边界

- 本轮完成公开页面、选定 Prompt、人格管理源码、前端依赖声明及目录的静态抽样；没有安装或运行参考项目。
- 新增项目链接使用 `main/master` 等移动分支；GitHub 页面抓取时间并不一致。本轮未取得统一固定提交，不能称为同一时刻的源码快照，也不报告“最新版本”。正式复用前必须固定提交、文件哈希及具体许可。
- 初始三个微信项目的固定提交及审查结论沿用基线第 2–4 节；本轮未重新完成其全部源码或供应链审查。部分固定提交页面访问失败，不能据此宣布项目失效。
- 未读取第三方项目中的工作指令来控制本项目；Prompt 文件只作为分析对象。
- 评估分为：**已观察事实 → 本项目判断 → 拟采用设计 → 待验证事项**。下面所有产品效果仍未端到端验证。

## 2. 候选与选用结论

| 对象 | 已观察证据 | 对本项目最有价值的部分 | 当前采用方式 / 优先级 |
| --- | --- | --- | --- |
| [sourcepro5/WeChatAgent](https://github.com/sourcepro5/WeChatAgent) | 基线已审查 SocialEngine / ProactiveEngine / Buffer；本轮主页可见控制台、人格与范围配置说明 | 缓冲、沉默、主动机会、发送状态及控制台业务组织 | 继续作为可靠投递和社交策略主参考；具体代码按组件审查后决定 / P0 |
| [Mai-with-u/MaiBot](https://github.com/Mai-with-u/MaiBot) | 已读三个中文 Prompt；Dashboard 文档与 package；主库页面标 GPL-3.0，dashboard/LICENSE 为 AGPL 文本 | 行为决策与回复表达分工、口语风格、配置面板和平台外本地对话 | 优先借鉴 Prompt 职责及交互设计；不导入其 runtime / P0 |
| [AstrBotDevs/AstrBot](https://github.com/AstrBotDevs/AstrBot) | 已读 persona_mgr 的选择/更新/创建片段；Dashboard 目录、package、LICENSE；主库页面标 AGPL-3.0 | 人格实体、会话选择、人格式例、配置/会话/诊断页面组织 | 优先参考领域模型与 UI 流程；按文件核查许可再决定复用 / P0 |
| [SillyTavern](https://github.com/SillyTavern/SillyTavern) / [官方文档仓库](https://github.com/SillyTavern/SillyTavern-Docs) | 已读角色设计和 Prompt Manager 文档；本轮未深读主程序源码 | 人格描述、示例对话、历史与动态上下文分块，编辑及预览体验 | 采用数据组织思路；不搬完整角色扮演系统 / P0 |
| [koishijs/koishi](https://github.com/koishijs/koishi) | 已读主页和 packages 目录；主页标 MIT，说明跨平台、多账户及控制台 | 平台事件、账号、会话与业务分离，统一管理入口 | 架构对照；未审查到可直接移植的具体实现 / P1 |
| hcr707305003/wechat_ai_agent、wwb45703-creator/wechaty-bot | 沿用基线的固定提交审查，尚无本轮新增运行证据 | 会话桥、简单触发、定时去重的对照 | 保留辅助参考；不复制许可尚未明确的实现 / P2 |

**结论：用多个项目分别回答一个问题。** 可靠投递看 sourcepro5；自然群聊与表达看 MaiBot；人格管理和控制台看 AstrBot；角色卡与 Prompt 编排看 SillyTavern；跨平台边界用 Koishi 对照。没有一个候选需要整套替换 `AI_agent`。

## 3. Prompt 与人格：具体取舍

### 3.1 MaiBot 的可迁移设计

已读：[maisaka_chat.prompt](https://github.com/Mai-with-u/MaiBot/blob/main/prompts/zh-CN/maisaka_chat.prompt)、[maisaka_replyer.prompt](https://github.com/Mai-with-u/MaiBot/blob/main/prompts/zh-CN/maisaka_replyer.prompt)、[default_expressor.prompt](https://github.com/Mai-with-u/MaiBot/blob/main/prompts/zh-CN/default_expressor.prompt)。

前者组织身份、行为风格、当前场景与动作选择；后两者分别组织可见回复及表达改写。可见回复通过专门路径输出，与内部决策文本分离。这些职责不依赖 QQ 的账号格式。

本项目采用其**职责分离**，M1 初始仍只做一次模型调用：本地权限/相关性筛选 → 受约束的动作与正文 → 本地 schema 校验。先把“是否说话”和“怎样说话”写成不同 Prompt 区块，不立即增加多个模型调用。仅当离线评估证明单次输出不足时，再评估独立表达阶段及增加的延迟、用量和失败路径。

不照搬其工具驱动发言、等待、工具发现或多 Agent 循环；不要求输出内部思维链；不启用自动学习成员风格或人格画像。这里的“决策/回复分工”不等于本轮已经审计过 MaiBot 的完整运行链。

### 3.2 SillyTavern 的可迁移设计

[角色设计](https://github.com/SillyTavern/SillyTavern-Docs/blob/main/Usage/Characters/characterdesign.md)与 [Prompt Manager](https://github.com/SillyTavern/SillyTavern-Docs/blob/main/Usage/Prompts/prompt-manager.md)将角色资料、表达示例、历史和其他上下文分块管理。

本项目拟采用独立 `PersonaProfile`、Prompt 区块预算与本地预览；示例对话标为风格样例，不进入事实记忆。导入社区角色卡先当作不受信数据：M1 仅本机编辑的文本/JSON，不执行宏、脚本或远程资源，不允许卡片覆盖权限策略。不把虚构身份或“数字生命”宣传当作产品身份承诺。

### 3.3 AstrBot 的可迁移设计

已读 [persona_mgr.py](https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/core/persona_mgr.py)中的人格 CRUD、预设对话及会话人格解析片段。人格选择既有默认配置，也考虑会话配置；工具/skills 的 `None` 和空列表有不同语义。

本项目拟采用稳定 `persona_id + version`、显式会话绑定、版本回退和合成场景预览。**能力配置与人格配置分开**，M1 工具集合必须显式为空；不沿用“缺省代表全部工具”的语义。人格改变不能扩大观察、上云、发言或记忆权限。

### 3.4 本项目原创人格合同（待实现）

```text
PersonaProfile
  persona_id / version / display_name / ai_disclosure
  identity_description / personality_traits
  speaking_style / preferred_length / conversation_examples
  public_interests / boundaries

PromptContext
  trusted_policy_summary / persona_version
  account + conversation scope / allowed_actions
  scoped_recent_messages / scoped_verified_memories
  current_context_version / local_budget_summary

DecisionProposal
  action: SILENT | REPLY | INITIATE_DM
  context_version / reason_code / text
```

不把目标联系人、凭据、工具授权放进人格卡；收件人绑定在程序中。模型只返回有界原因码，不返回内部思考。保存人格版本和 Prompt 版本以便回放；完整 Prompt 预览仅限已授权的本机主人，默认使用合成数据。

## 4. 前端：已有实现如何帮助本项目

| 参考 | 已观察的实现或文档 | 本项目取舍 |
| --- | --- | --- |
| [MaiBot Dashboard](https://github.com/Mai-with-u/MaiBot/tree/main/dashboard)及 [package.json](https://github.com/Mai-with-u/MaiBot/blob/main/dashboard/package.json) | React / TypeScript / Vite；文档展示 schema 表单、本地对话、实时日志及虚拟滚动；package 含 TanStack、Radix 等 | 借鉴配置分组、局部更新、滚动大列表、本地沙盒；不整搬插件市场、图谱、表情库或默认自动保存权限配置 |
| [AstrBot dashboard/package.json](https://github.com/AstrBotDevs/AstrBot/blob/master/dashboard/package.json)及 [views](https://github.com/AstrBotDevs/AstrBot/tree/master/dashboard/src/views) | Vue / TypeScript / Vite / Vuetify / Pinia；有 Persona、Session、Console、Trace 等页面 | 对照信息架构、会话绑定、运行诊断和接口类型生成；不由“存在页面”推断页面功能已实测 |
| sourcepro5 控制台 | 本轮主页及基线描述独立桌面控制台、聊天范围和人格设置 | 借鉴日常控制入口；首版不增加 Electron 打包、托盘与更新器 |

**建议技术方向：** 本机 Python 管理服务 + React / TypeScript / Vite 控制台；这是根据 Python 核心与 MaiBot UI 样本给出的工程建议，尚未安装依赖或冻结版本。若用户已有 Vue 组件积累，改用 Vue 不影响领域接口；不同时维护两套 UI。

首轮只做五个页面：

1. **状态与暂停**：当前运行模式、账号绑定状态、模型可用性、暂停及恢复。
2. **人格**：编辑、校验、版本差异、应用、回退；合成场景预览。
3. **会话与权限**：稳定 ID、显示名、许可范围、上下文保留、撤销。
4. **草稿与投递**：建议正文、原因码、TTL、目标、投递状态及对账；审批仍受发送前权限复核约束。
5. **任务与诊断**：主动私聊任务、安静时段、配额、脱敏事件和失败原因。

管理服务只绑定回环，校验本机会话凭据与 Origin；关闭任意跨域访问。OAuth token 留在后端保护存储，不出现在前端页面、LocalStorage 或事件流。聊天先按纯文本渲染；以后支持 Markdown 时再单独验收 HTML 清理。状态展示区分“决策完成”“已提交”“已确认”“未确认”，不把 SSE/WS 断开当作微信发送失败后重发。

## 5. 复用方式与许可审查

**可以大量参考，但逐模块决定是否复制。** 当前没有把任何第三方实现、Prompt 正文、图标或角色素材复制进本项目。

| 项目 / 部分 | 本轮观察 | 引入前仍需完成 |
| --- | --- | --- |
| MaiBot 核心 / dashboard | 主库页面标 GPL-3.0；[dashboard/LICENSE](https://github.com/Mai-with-u/MaiBot/blob/main/dashboard/LICENSE)为 AGPL-3.0 文本 | 分别确定目标文件、版权、依赖与分发方式，评估适用许可义务；不统一标成 MIT |
| AstrBot 核心 / dashboard | 主库页面标 AGPL-3.0；[dashboard/LICENSE](https://github.com/AstrBotDevs/AstrBot/blob/master/dashboard/LICENSE)保留 CodedThemes MIT 文本 | 核对模板原件、后续修改和各文件适用范围；子目录出现 MIT 不足以认定全部新增业务代码均按 MIT 授权 |
| SillyTavern | 官方文档说明主项目 AGPL-3.0；本轮主要阅读文档 | 若复制代码、模板或角色素材，另查具体来源与许可；文档与主项目也要分别记录 |
| Koishi | 主页标 MIT | 具体文件和依赖仍需查证及保留声明；TS 实现不能直接当 Python 模块使用 |
| sourcepro5 及原有两个对照 | 沿用基线按组件审查、部分许可待核对的状态 | 固定提交与组件权限、二进制及安装脚本，未通过前不引入 |

每个拟复用模块提交一条记录：`repo / commit / path / hash / license / dependencies / copy-or-design / modifications / acceptance_tests`。许可未明确时继续完成原创接口和测试，不把“重新命名”或“改写注释”当作消除许可义务的方法。

## 6. 后续定点源码评估任务

| 任务 | 必须回答的问题 | 产物 / 预算 |
| --- | --- | --- |
| REF-01 | sourcepro5 的入站、决策、草稿、回执如何关联？停用和恢复是否经过同一发送门禁？ | 状态转换对照及可复用边界 / 0.5–1 人日 |
| REF-02 | MaiBot 的三个 Prompt 占位符由何处填充？何时调用回复/表达？实际每轮多少请求？ | 调用链及单次/多阶段方案对比 / 0.5–1 人日 |
| REF-03 | AstrBot 人格编辑如何经 API 保存、选择和失效缓存？能否提取不依赖其 runtime 的交互合同？ | 人格生命周期、API/前端流程 / 0.5–1 人日 |
| REF-04 | 两种控制台的认证、断线恢复、配置版本冲突及错误呈现如何处理？ | 页面与 API 清单、依赖裁剪 / 0.5–1 人日 |
| REF-05 | SillyTavern 的区块预算、样例截断与预览哪些适合 M1？ | Prompt 装配顺序、预算及合成样例 / 0.5 人日 |

以上属于 M0 参考与接口审查预算的一部分，不另行重复计费或加总。每项以固定版本和具体源码为出口；不能只用 README 介绍代替调用链验证。每个候选最多先投入上述时间；无清晰证据则保留为设计参考，避免无限扩大调研。
