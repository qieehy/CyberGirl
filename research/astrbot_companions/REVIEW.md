# AstrBot 陪伴与记忆插件源码调查

阅读日期：2026-11-18。来源为作者 menglimi 的 GitHub main 源码快照；只下载并静态阅读，没有安装、导入或执行插件，没有导入当前聊天记录或修改正在运行的回复链。GitHub main 可变化，本目录保留本次阅读文件。

- 我会永远陪着你：astrbot_plugin_private_companion，metadata.yaml 7.2.4。
- 我会牢牢记住你：astrbot_plugin_memory_companion，metadata.yaml 2.6.6。用户所说“我会永远记得你”按该配套插件理解。

## 陪伴核心

依赖方向是 main.py → astrbot_host → companion_v2。普通回复继续由 AstrBot 核心组织模型、人格、历史、工具和平台发送。插件通过事件钩子加入角色状态及相关资料；主动联系单独调度。

重点源码：

- private_companion/astrbot_host/core_reply_context.py：只读取本轮获准的本地背景，不另起模型；组织关系、生活、记忆、状态及未完成事项。
- private_companion/companion_v2/role_work/state/service.py：精力、心情、睡眠、忙碌、位置等角色模拟状态及临时条件。状态影响语气和节奏，计划不等于实际执行。
- private_companion/companion_v2/proactive/scheduling.py，_plan：先核对主动权限、启用、睡眠宽限、空闲间隔、投递历史和预算，再从关系、纪念日、未完成事项、回忆等来源构造候选。
- private_companion/companion_v2/proactive/rhythm.py：主动节奏、时段曲线、间隔、连续短消息；不等于每次定时扫描都发消息。
- private_companion/companion_v2/proactive/service.py，ProactiveContacts：主动规划／生成／可选人格复核与持久投递任务。按配置可能产生多次模型调用，不能把全部主动成本当成一次聊天。
- private_companion/companion_v2/proactive/platform_circuit.py：平台失败熔断。

调度显式处理连续未回应后的放缓或暂停、刚收尾后的静默、同一时机去重。普通聊天和主动任务共用实际账号及发送层，插件自身不解决桌面微信最小化输入。

## 长期记忆核心

重点源码：

- memory_companion/main.py，on_llm_request（281行起）：请求前进入 handle_llm_request；生命周期启动捕获、总结和维护后台任务。
- memory_companion/core/service.py，handle_llm_request（1189行起）：清除上一轮临时注入、解析规范身份、捕获当前来源、处理关系和称呼，并组织召回。maybe_summarize_session（6331行起）负责阶段总结；inject_memories（10835行起）负责本轮注入。
- memory_companion/core/store.py：SQLite 的 memories、timeline、person_weights 等表及 FTS5 索引。
- memory_companion/core/summarizer.py：阶段性总结、结构化事实和来源校验；总结不等同于保存所有原话。
- memory_companion/core/person_card.py：把姓名、称呼、关系、重要事情组成简短人物卡。
- memory_companion/core/person_weight.py：人物分量0..100、六档及记忆衰减系数；它是可维护的数据和规则，并非模型训练。
- memory_companion/core/retrieval.py：关键词／全文、时间和图谱候选；可选向量补充召回、可选重排，随后去冗余与筛选。向量库不是基础必需项。
- memory_companion/core/injection.py：按预算组合临时记忆资料，默认1800字符，硬上限4000字符。区分可以提及、仅影响语气和不确定记忆，提供人物化表达。

总体顺序：原始消息及来源 → 短期时间线 → 分批总结／明确记忆写入 → 结构化长期记忆 → 当前身份和会话权限过滤 → 相关性召回 → 人物卡＋有限记忆包 → 当前模型回复。用户纠正与当前事实优先；人物关系和私聊／群聊可见范围不能仅凭昵称推断。

## 两者联动

陪伴插件的公开 companion.external-memory.v1 接口允许注册 read/write/summary 回调并声明是否接管模型侧记忆；还有 MemoryCompanion 兼容桥。核心作用是统一记忆归属，避免两套插件重复注入。接口存在并不代表任意记忆插件安装即连通。

## 当前项目可采用的最小范围

当前 Cybergirl 主要依赖人格和最近最多16对已回复上下文。要实现跨会话连续性，可先在既有收发桥上加入：

1. 以账号＋规范联系人身份隔离的事实／事件表，保留时间、原始来源、置信度和更正关系。
2. 简短人物卡，以及关键词／FTS召回；先不引入向量服务或多步 Agent。
3. 每轮有限相关记忆注入，明确“确定事实／待确认／仅影响语气”。
4. 批次摘要需独立计入模型预算，不在每条消息后再调用一次模型。
5. 确实需要主动联系时，再增加低频调度、免打扰、未回应降频及单次投递。

以上为移植建议，尚未实施。记忆和主动联系功能与最小化发送是不同层次；新增记忆不会改变当前微信桥的窗口限制。

## 上游来源

- https://github.com/menglimi/astrbot_plugin_private_companion
- https://github.com/menglimi/astrbot_plugin_private_companion/blob/main/docs/V2_CORE_REPLY_PATH_DESIGN.md
- https://github.com/menglimi/astrbot_plugin_private_companion/blob/main/docs/external-memory-provider.md
- https://github.com/menglimi/astrbot_plugin_memory_companion
- https://github.com/menglimi/astrbot_plugin_memory_companion/blob/main/docs/PLUGIN_STRUCTURE.md
