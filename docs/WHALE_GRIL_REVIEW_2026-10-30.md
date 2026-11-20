# whale_gril 微信接入评估与改造计划

核验日期：2026-10-30。用户更正参考仓库为 [Banan1cee/whale_gril][repo]。固定提交 `e14a8c2fbf0b751ae3a9478f94688ea954d974db`；149 份文件保存为 `.source.txt`，逐一核对 Git blob，未安装或执行上游程序。GPT-Niang 不再作为这次微信评估的对象。

## 结论

**把 whale_gril 作为微信业务桥接的主要参考，保留本项目 AI_agent、Hermes/Codex provider、SQLite inbox/outbox 和工作台。** 它补足了 `wechatauto-replica` 之上的窗口识别、会话定位、消息转发和故障诊断经验；不适合将约 3,500 行桥接主循环整体搬入本项目。

它走的是“数据库读取消息 + UIA 操作窗口发送”，底层仍为 `wechatauto-replica`。因此可以改变后续收消息和身份核验的方案，但不能消除当前 UIA 初始化及 4.2.0.9 兼容性验证。其文档记录本机为 **4.1.13.65**，源码诊断还建议 4.1.15.53；本次未找到精确 **4.2.0.9** 的运行证据。不能据此断言 4.2 不可用或要求降级。[微信说明][wechat-doc]、[依赖声明][requirements]

本项目 W1-F 的事实继续成立：锁定的 4.2.0.9 原主窗临时启用后出现 51 个节点，深度 8 截断，恢复后回到 3 个节点。下一步应使用上游提供的控件线索改善采集，而不是重复同一次浅层探测。

## 值得采用的部分

| 部分 | 源码证据 | 本项目采用方式 |
| --- | --- | --- |
| Qt 外层窗口与深层控件识别 | `uia_compat.py:257–282` 识别 `session_list`、`chat_input_field`、`mmui::MainTabBar`；`:424–445` 容许 List/Custom/Pane，深度上限 30 | 用作结构探针的候选特征。当前仅查 Edit/List 不够：容器类型可能不同。固定进程/窗口，再做有界分支遍历 |
| 固定窗口与账号检查 | `uia_compat.py:284–317` 固定 HWND；`wechatauto_bridge.py:1018–1027` 拒绝依赖库自行换账号 | 保留“目标失效即停止”的设计；再补 PID 创建时间、模块和账号的实际关联，不能只检查 HWND 仍存在 |
| 数据库消息事件 | `wechatauto_bridge.py:2837–2848,2914–2927` 使用会话 username、sender_username、shard/local_id | 收消息优先评估数据库路线，避免把 UI RuntimeId 当持久消息 ID；所有事件加本机账号和数据库代际作用域 |
| 更审慎的单次投递 | `sticker_transport.py:148–188,373–511` 有目标重名检查、草稿检查、发送前复核、至多一次回车及 unconfirmed | 借鉴其状态边界，为纯文本重写小型传输层；其图片发送实现及确认条件不原样复制 |
| 运行状态可见 | `wechatauto_bridge.py:3490–3516` 分别报告监听、UIA、账号绑定 | 工作台区分“能接收”“控件可用”“目标已绑定”“投递待确认”，不只显示笼统在线 |

定位依据：[UIA 兼容补丁][compat]、[桥接源码][bridge]、[表情包传输][sticker]。这些是候选实现线索，尚未在本机验证控件 ID。

## 必须改写的行为

1. **普通文本可能重复提交。** `uia_send:1964–1979` 失败后再次 `send_text`，成功后又追加两次 Enter、两次 Ctrl+Enter；外层 `send_text:2504–2539` 还有重试、重新启用与键盘回退。异常可能发生在已按发送之后，无法据 False 判断“未提交”。改为先记录提交边界，尝试后不确定即 UNKNOWN，禁止换通道补发。[文本发送][text-send]

2. **键盘通道的成功证据太弱。** `keyboard_send:2135–2146` 比较目标会话最近三条消息的正文前 12 字，没有要求来自本人、晚于本次发送或全文一致；旧消息和他人同前缀消息都可能被误认。新设计要求账号/会话、基线后新记录、本人、完整内容与唯一关联同时成立。数据库出现本人新消息只算本地回显，不能直接等同接收端送达。[键盘确认][keyboard]

3. **窗口和会话的名称匹配不能作为稳定身份。** `pick_account_window:1283–1317` 会因手工 HWND 或只有一个候选窗就记 verified；多窗口时以最近会话名称打分。`uia_compat.py:511–595` 支持子串/成员名组合，完全同名行没有被唯一性检查排除。仅名称唯一仍不是 wxid 的直接 UI 证明。自动模式需要独立绑定证据；证据不足时交付人工选定会话的辅助模式，不把它升级为自主群接入。[窗口绑定][account]、[会话匹配][chat-match]

4. **不能将“探测”视为纯只读。** `_probe_wechat_windows:1184–1214` 的说明虽然写了“纯只读”，实际会对候选窗调用 `_hot_activate_accessibility`。`recover_uia:1985–2001` 还会重新启用。它没有绕开进程内存修改机制。本项目继续使用已实现的固定指纹验证和显式启用入口，不让找窗或重试自动触发写入。[探测调用][probe]

5. **超时不等于操作停止。** `uia_compat.py:488–506` 的搜索线程超时只返回 False，未终止线程；桥接 `:2561–2567` 的 OCR 线程也可能在返回后继续操作。改为受控工作进程与单一操作租约；无法证明操作已停止时隔离该通道，不开启下一次投递。已有提交可能性仍记 UNKNOWN，不能因结束进程就判未发送。

6. **事件与群语义还需补强。** `:2923` 的消息键没有账号/数据库代际；`:2873–2925` 的 @ 来自文本正则，手工输入同样文字也会匹配，不能据此设置已验证的 `mention_self`。`:653–664` 的正文去重是反刷屏规则，会吞掉合法的重复正文。接入本项目时，身份不完整的事件拒绝进入自动处理；持久去重使用事件身份；明确 @/引用必须保留结构证据。语气和人格不能承担权限判断。

7. **异步转发要先落账再确认。** 桥接 `:3032–3041` 的 Listener 回调只提交线程池便返回。所审依赖版本的 `Listener._worker_run` 在回调返回后推进水位，进程若在后续工作执行前退出，会留下漏处理窗口。我们的读取游标应在 inbox 持久接收后推进，模型与发送另行处理；不复用整个监听/回复循环。这是针对下述固定依赖版本的组合分析，不声称覆盖 whale_gril 所有可安装版本。

## 数据库路线的边界

whale_gril 仅声明 `wechatauto-replica>=1.2.7`，没有精确锁定。本次补查先前评估过的依赖提交 `6e603974bbac3bda93d88bbd6995510ae2547f5c`，其 `pyproject.toml` 版本为 **1.2.8.1**；它是参考源码，不能冒充 whale_gril 作者实际运行的 1.2.7.0 或已安装 wheel。

- `db.py:499–520` 构造器就会加载或提取数据库密钥；`:299–360,898–970` 包含进程内存读取。这不属于此前仅读取指令和一个标志字节的预检范围。
- `:1246–1278` 将密钥以十六进制 JSON 写入缓存、备份和稳定目录；`:1362` 起将解密数据库缓存至 workdir。生产接入要设计本机密钥保护和缓存生命周期，不能把这些默认文件带进项目证据或普通日志。
- `:2138–2202` 的监听事件确实提供 shard、local_id、sender_username、create_time、sort_seq；该返回结构没有 server_id。其他原始查询存在 server_id，后续要核对其有效性、唯一性和是否适合对账，不凭字段名称认定稳定。
- `:2182–2191` 按消息所在分片解析发送者，说明数字 sender_id 不能跨分片使用；这一点应保留。
- 本轮未打开用户数据库、提取密钥、读取聊天、修改微信内存或运行桥接。只保存公开源码。

依赖定位：[固定版本 DB 实现][db]。样本与哈希见 [主仓库清单](../research/wechat_bridge/2026-10-30/whale_gril/manifest.json)及[依赖清单](../research/wechat_bridge/2026-10-30/whale_gril/dependency-manifest.json)。代码许可：whale_gril 为 [MIT][license]；已核验的 wechatauto-replica 为 Apache-2.0。若后续复制代码，分别保留其版权及许可记录。

## 接下来的实施顺序

| 切片 | 实施内容 | 关键出口 |
| --- | --- | --- |
| W1-G1：完善结构探针 | 在现有探针中固定目标 PID/HWND；容许已知容器类型；适当提高深度，并限定节点、分支和超时。若读取 AutomationId，只输出已知控件角色匹配结果，不保存任意 ID、Name、Value 或聊天文本 | 能区分主窗与登录窗，确认会话列表/输入容器；截断明确标记，不能误报不存在。不重新执行已通过的浅层实验 |
| W1-G2：独立桥接合同 | 定义身份绑定、DB 事件、进程代际、提交边界和本地回显；接到既有 IncomingEvent/Receipt，不引入上游 Node 服务与自动回复循环 | 仅三组关键离线场景：同名/换号拒绝；重复事件与游标落账；提交后异常不重发 |
| W2-A：限定账号的接收验证 | 锁定实际依赖版本及密钥/缓存方案后，对指定账号与测试会话验证一条新消息；先只接收，不调用模型 | 核验会话/发送者/消息身份、自身回声及重启去重；缺字段不假造 source_verified |
| W2-B：单次文本投递 | 基于单次传输状态机，只对明确测试目标发送唯一标记；核对草稿、焦点、身份及回显 | 一次提交；本地回显与接收端证据分别记录；不确定不补发 |
| W3/W4：真实闭环 | 身份/投递合同通过后，再接主人私聊与现有群策略 | 沿用既有权限、限频、暂停、过期和模型路线验收，不以桥接启动成功替代 |

下一开发项为 **W1-G1**。再次临时启用不在上一轮一次性授权内；先把新的探针和采集范围准备完成，再处理真实实验。W1-G2 的纯离线合同设计不依赖微信处于启用状态。

本轮验证是固定源码/依赖审查与内容哈希核对，未运行上游测试。部分上游桥接测试只是对源码做正则断言（如 `tests/bridge-recovery.test.js:21–40`），不足以证明本机实际发送、恢复或投递确认正确。当前不新增细碎测试、不重复 H4 或 S1–S4。

[repo]: https://github.com/Banan1cee/whale_gril/tree/e14a8c2fbf0b751ae3a9478f94688ea954d974db
[wechat-doc]: https://github.com/Banan1cee/whale_gril/blob/e14a8c2fbf0b751ae3a9478f94688ea954d974db/docs/%E6%8E%A5%E5%85%A5%E5%BE%AE%E4%BF%A1.md
[requirements]: https://github.com/Banan1cee/whale_gril/blob/e14a8c2fbf0b751ae3a9478f94688ea954d974db/requirements.txt
[compat]: https://github.com/Banan1cee/whale_gril/blob/e14a8c2fbf0b751ae3a9478f94688ea954d974db/examples/uia_compat.py
[bridge]: https://github.com/Banan1cee/whale_gril/blob/e14a8c2fbf0b751ae3a9478f94688ea954d974db/examples/wechatauto_bridge.py
[sticker]: https://github.com/Banan1cee/whale_gril/blob/e14a8c2fbf0b751ae3a9478f94688ea954d974db/examples/sticker_transport.py
[text-send]: https://github.com/Banan1cee/whale_gril/blob/e14a8c2fbf0b751ae3a9478f94688ea954d974db/examples/wechatauto_bridge.py#L1910-L1983
[keyboard]: https://github.com/Banan1cee/whale_gril/blob/e14a8c2fbf0b751ae3a9478f94688ea954d974db/examples/wechatauto_bridge.py#L2135-L2146
[account]: https://github.com/Banan1cee/whale_gril/blob/e14a8c2fbf0b751ae3a9478f94688ea954d974db/examples/wechatauto_bridge.py#L1278-L1414
[chat-match]: https://github.com/Banan1cee/whale_gril/blob/e14a8c2fbf0b751ae3a9478f94688ea954d974db/examples/uia_compat.py#L511-L595
[probe]: https://github.com/Banan1cee/whale_gril/blob/e14a8c2fbf0b751ae3a9478f94688ea954d974db/examples/wechatauto_bridge.py#L1184-L1214
[db]: https://github.com/fanyuantaier/wechatauto-replica/blob/6e603974bbac3bda93d88bbd6995510ae2547f5c/wechatauto/db.py
[license]: https://github.com/Banan1cee/whale_gril/blob/e14a8c2fbf0b751ae3a9478f94688ea954d974db/LICENSE
