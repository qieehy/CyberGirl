# B18 独立 filehelper 窗口后台对照

2026-11-07。最新状态：**该单次许可已获批并消费；正文丢失 emoji，在提交前停止。没有发送手势，不复用许可或补发。**

## 这次获批动作的实际结果

用户明确批准该标记一次及三条旧 UNKNOWN 的接受后，仅执行一次：action `1b72f580238f6f27751f496f17c44ba8dd13b06dc9a5ce9a564f7d2cd7d3973a`，operation `979ca0ae908742a3ae3ba81e9fc19351`。原账本输入许可 1、提交许可 0；36 个码元及伪焦点开始/结束共尝试 38 次窗口投递。没有 MOVE/DOWN/UP 意图，没有清框或第二次输入。

结果 UNKNOWN/input_dirty；只读后检确认 Value/Text 两模式均为 34 个 UTF-16 码元，精确哈希 `2cf3fa90d7cc0ae004d1933f43aba856c5bb8e2f90e091fc84dae1d1a89fbcf7` 等于批准正文去掉末尾 🙂。这证明本次模式未保留 emoji，不将“原版声称支持代理对”当作本机支持，也不假定仅调整间隔即可修复。

前台目标未出现、207 次指针采样未见位置变化，最大采样间隔约 70.615ms；伪焦点清理、子进程/UI 后代退出、gate 恢复和写锁释放已核查。原账本现 8 次操作、输入/提交累计 6/5 次，四条 UNCONFIRMED，完整性 ok；旧三条记录保持原状态。没有提交许可，因此没有请求手机肯定确认。原输出的通用 `phone_confirmation_required=true` 不代表发生提交，该显示逻辑后已改为按提交许可判断，原证据不覆盖。

证据：`w2b18-standalone-control-once-2026-11-07.json`、`w2b18-standalone-after-input-readonly-2026-11-07.json`。本轮批次在此结束。新的完整值输入候选另见 [值输入单次方案](W2B18_VALUE_CONTROL.md)，不得继承这次已消费许可。

## 目标与路线变更

保留主窗口的原不准入结论。本次显式绑定用户手动打开的独立 `mmui::ChatSingleWindow`，不把主窗口当前活动输入框作为消息路由。能力如实标为“已登录、已打开独立 filehelper 窗口、未最小化时的后台发送对照”；不宣称已支持主窗口、托盘或锁屏发送。

固定来源 vendor/wechatauto 1.2.9 的 uia_driver.py:1198–1207 明确独立窗口只接受自身会话，不提供跨会话搜索。该报告仅作来源，另已在本机核验：独立窗口 AutomationId 精确为 `ChatSingleWindowfilehelper`，原生 ChatInputField → ChatMessagePage → ChatSingleWindow 祖先链、规范页面字段精确为 filehelper、原数据库账号和两种空输入读回均通过。根静态元数据 RVA 152341752、vtable RVA 160048360 固定到现有已校验 DLL；Qt 静态元数据本类新增方法数为 0，不把这一点单独当作全部行为证明。

用户明确完成主窗口切换另一会话后，独立窗口再次通过相同身份检查。仅检查独立窗口，没有读取另一会话正文。实际证据：`w2b18a-standalone-readiness-2026-11-07.json`、`w2b18a-standalone-confirmed-main-switch-2026-11-07.json`。早一份 `standalone-main-switched` 文件生成时用户答复仍待到达，不能单独用它证明主窗口已经切换。

隐藏测试窗口实测补足操作系统部分：投递给固定 HWND 的字符在延后泵送且另一窗口路由变化后仍只进入固定窗口；销毁该窗口后，旧字符没有投给新建的替代窗口。该测试只创建测试进程自己的隐藏窗口，不对微信制造故障；它不单独证明微信窗口业务身份。与本机独立窗口原生证据合并，限定准入这个固定窗口实例，不把结论推广回主窗口。

## 一次真实动作的固定范围

| 项目 | 固定值 |
| --- | --- |
| 请求 | `w2b-background-20261107-control` |
| 正文 | `W2B-20261107-background-control-中文🙂` |
| 账号 | 原经数据库头认证并核对过的测试账号 |
| 目标 | 该账号的规范 filehelper，独立窗口实例 |
| 当前准备实例 | PID 39072，创建代际 134385125945511854，HWND 46599512；实际运行重新核对，不自动换实例 |
| 输入 | 上游 UTF-16 码元窗口消息，仅一次正文；中文/emoji 不丢弃 |
| 提交 | 相同窗口的 MOVE → DOWN → 70ms → UP 一次手势 |
| 真实键鼠/抢前台 | 不调用 SendInput、SetForegroundWindow、真实 SetFocus、SetCursorPos、Click/SendKeys |
| 初始化 | 已有受审 gate 租约，先日志后启用，完成回收后恢复；不开系统 SPI 读屏 |
| 截止 | 准备 15 秒、完整控制核心 20 秒、父进程控制等待 25 秒、整次宿主外层 90 秒；不修改旧 7 秒发送器 |
| 超时/变更/失败 | 立即停止本批次，不清草稿、不换编码、不补发、不自动切主窗口或前台 |

动作使用原 `.state/social/wechat-sender-poc.sqlite3`、原 outbox/action/attempt 和 SendState。父进程持有账号/进程写锁及上游锁；子进程和 UI 读取后代属于 kill-on-close Job。每次效果请求由父进程在原账本持久许可后应答；MOVE/DOWN/UP 的意图全部在 MOVE 前应答，手势中不做父进程往返。GUI/原生身份复核不得通过猜测显示名放行。

原后台审核分支要求真实的独立窗口、后台观察、无捕获等事实，不能伪造 `foreground_hit_and_focus=True`。旧前台审核字段及默认行为保留。原版源码仍未准入；本方案是明确替换初始化、读取和效果边界后的受约束对照。

## 旧不确定性

本次如获许可，还须仅为本次新动作明确接受以下三条历史不确定性；不把它们改写为送达/失败，不给后续未列明动作无限许可：

| 历史正文 | action_id |
| --- | --- |
| `W2B-20261104-directed-once` | `1a456235d82b299c920e453512d90bec90f583286f2ed17055dc527884bc7afd` |
| `W2B-20261104-posted-once` | `e8b2e295fb357ddf33b6df8be33b82e07aae55c124c9e3a3b1388258757ed7aa` |
| `W2B-20261107-sequence-once` | `e3917a17916f4156c3ed706951c4ec63adb0b5092ce9ea13eeda8643a2bb6cc1` |

## 结果判定和恢复

上游 sent、空框、消息入队不单独算送达。须匹配原账号、规范 filehelper、本人发出方向、完整正文和基线后唯一新数据库记录；再请用户核对手机完整标记。结束后核对原生目标、双模式草稿、Qt 伪焦点、按钮释放/捕获、子进程退出、前台/鼠标观察覆盖及 gate 原值恢复。读不到回显、部分效果或恢复不确定均保留 UNKNOWN/阻断，不重放。

实机只读宿主 `wechat_standalone_worker.py` 默认拒绝 run_background；已在本机通过该 worker 的 prepare 并回收。真实入口为 `probe_wechat_background_trial.py --send-reviewed-once`，必须带具体操作批准、三条具名不确定性及原账号/窗口证据。无新许可不运行该入口。手机确认使用同入口的独立 `--confirm-phone-evidence` 模式，不能再次发送。

## 验证

实现期间全量 611 passed、848 subtests passed（126.58 秒）；其后加入的两项隐藏 HWND 队列/销毁测试包含在随后通过的 18 项 standalone 专项中。另有真实子进程许可往返、暂停、断流/超时与重启阻断测试。准备证据 `w2b18a-standalone-worker-ready-2026-11-07.json` 显示没有输入/提交/前台激活或真实键鼠调用，gate 原值恢复、观察器/写锁均释放。

B18b 通过后才推进 B19 正式发送器接线、B20 30 分钟接收/服务重启验收；本单条方案不代替后两项完成，也不授权联系人或群消息。
