# 指定私聊后台发送

2026-11-17。当前已实现原账号／Amadues规范目标的后台单次发送，已实际发送“Cybergirl：这是一条自动发送测试消息。”并取得唯一原生self_echo。没有截图、前台激活或全局键鼠输入。支持固定微信4.2.0.13、未最小化且被其他应用遮挡的独立聊天窗口；未开放最小化／托盘或持续自动回复。

启动现有私聊工作台时加`--wechat-send confirm`，显式装配PrivateBackgroundSender。草稿保存后点击“后台发送一次”并核对正文，或通过已认证／同源的POST `/api/wechat/private-send` 提供request_id、text。请求不接受客户端目标；目标来自接收器的规范身份绑定。同请求同正文只返回原结果，不再次触碰微信；同请求改正文拒绝。模型和后台发送独立，收消息、刷新、重启不自动发送。

原filehelper require_send_target白名单保持。新私聊专用guard核对账号、规范peer、独立窗口及原生证据哈希；每个字符和提交前均核对目标、进程、上下文／暂停／人格、空输入或当前完整正文。子进程只通过目标窗口PostMessage发送BMP单行≤96码元，使用目标窗口Return按下／抬起；没有SetForegroundWindow、SendInput、全局键鼠或前台回退。原始UIA元数据仅查询当前窗口编辑器／按钮／几何及正文哈希，不扫描聊天历史或读取截图。

复用原schema10、outbox／attempts／wechat_send_operations／wechat_send_journal／delivery_evidence；专用operator事件与peer入站分开。输入／提交意图先提交SQLite再ACK给隔离子进程。未完成进程或恢复未知拒绝；同一私聊存在已输入但没有唯一回显的操作时拒绝后续新发送。保留原通道阻断和历史，不给filehelper解除阻断，不重试未知结果。HTTP断开不取消／重启已开始的单次操作。

## 本次结果

首次操作在输入前被background_observer_sampling_stalled拒绝，输入／提交0且恢复完成，记录保留。将耗时的静态PE初始化移到观察启动之前后，再执行同一正文的首次实际输入／提交；没有放宽250ms采样条件。实际动作`9dafec8ca5f45a1e912af1a38313febec583ee2cc757d9da4fe486c3ced821dc`：23个字符意图、Return按下／抬起各1，输入／提交许可各1，唯一self_echo `9aa7500f11573a9d545378d3b878f904cdb857e3de0b5af669e764d9ccb82733`。子进程回收、恢复及gate0通过。

首次装配漏记attempts，导致接口未关联已经存在的回显；已修复新发送的attempt事务，并依据两条结束操作的原持久日志补录该两条attempt。POST `/api/wechat/private-send/reconcile` 只关联已落账且晚于原发送基线的唯一self_echo，绝不输入或重发；重复关联幂等。补录与回显关联已独立测试，原始失败输出保留。

总发送操作19、输入／提交许可16/12，其中本轮新增2条操作但实际输入／提交只有1次。原6张发送表所有旧行完全不变，旧8条UNKNOWN及E7确认保留。当前新动作仍UNCONFIRMED：本机回显不冒充对方／手机确认。没有为此要求用户重复发测试码或核对多轮消息。

只做关键验证：初始8项通过（目标／原白名单隔离、单次Return、请求幂等、模型关键边界和原filehelper流程），修复后4项私聊关键检查通过；Ruff、PS解析和Node处理函数通过。未重跑全量，也不把旧全量结果用于本轮新代码。

证据：[输入前拒绝](evidence/r3-Amadues-background-send-2026-11-17.json)、[实际提交原结果](evidence/r3-Amadues-background-send-v2-2026-11-17.json)、[只关联回显](evidence/r3-Amadues-background-echo-reconciliation-2026-11-17.json)、[独立收尾审计](evidence/r3-Amadues-background-final-audit-2026-11-17.json)。工作台保持运行供用户直接使用；原生发送子进程均已回收。

## 最小化检查（2026-11-17）

用户要求验证时，当前Amadues独立窗口已处于最小化状态。未恢复或激活窗口；在真实窗口上调用现行StandaloneCapabilities.guard，明确以standalone_window_minimized拒绝。最小化期间两次HTTP检查reading=true、read_error=null，已有4条消息展示正常；本轮没有新消息到达，不能据此声称完成新的入站验收。前台句柄始终未变，观察器无目标前台事件且已退出，gate前后0；原发送历史和模型trace计数未变，输入／发送／模型均0。窗口保持原来的最小化状态。本结果确认“最小化发送仍不支持”，不是放开最小化保护后的试发。见 [最小化事实](evidence/r3-Amadues-minimized-check-2026-11-17.json)。
