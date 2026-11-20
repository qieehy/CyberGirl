# B19 后台服务接入与 B20 持续接收验收

> 当前（2026-11-11）：两条集成标记各一次、完整手机收件与独立完整 30 分钟读取/重启核验均完成，先前中断不计入通过时长并保留。一般自动回复、最小化/托盘和补充字符未开放。见 [最终限定验收](W2B20_BG1_ACCEPTANCE.md)。

2026-11-08：实现和离线核验进行中，实机新动作尚未授权/执行。COVERED 电脑端已通过，待手机确认后才可准入。同一微信实例复用必须有完整原账本证据，不把仍运行的进程写成“已退出”。

## 固定范围

- 复用原 SocialService、SendState、outbox、journal、ReaderCatalog 和游标，不另建发送账本、不改旧 UNKNOWN、不开模型。
- 显式 `WeChatBackgroundAdapter` 与 `utf16_bmp_integrated` worker，仅两条分别审核的新标记：`W2B-20261107-background-integrated-中文` 和 `W2B-20261107-background-restart-中文`；请求分别为 `w2b-background-20261107-integrated` 和 `w2b-background-20261107-restart`。既有 COVERED/RETURN 永不重放。
- 仅原测试账号、规范 filehelper、同一已验收的独立窗口；未最小化、输入为空且被其他活动应用遮住。完整单行可打印 BMP 最多 96 码元；补充字符整体在输入前拒绝。
- 沿用一次 BMP 窗口消息输入、一次 Return 按下/释放、伪焦点开始/结束，不使用剪贴板、SetValue、Invoke、鼠标消息、真实键鼠、抢前台、清草稿、自动重试或前台回退。
- 两个 Return 意图均须先持久确认再投递任一按键；每个码元/按键检查窗口/账号/会话/代码身份、遮挡、观察连续性、暂停/过期状态。

## 接入与恢复

后台适配器 20 秒整体动作期限（含基线/准备），准备最多 10 秒，清理最多 2 秒，SocialService 用已有上限内的 25 秒。父进程以原 journal 的许可计数判定是否可能输入，UI `sent` 只返回 UNKNOWN。停止并回收 worker 后才恢复 gate 和释放与前台发送共用的两把 Windows 锁。若回收失败，保留锁并禁用适配器。

原 journal 控制完成事实必须有精确阶段顺序、完整字符串、无目标前台命中、观察器结束、焦点恢复和父进程资源恢复。它不表示送达；下一次复用还必须同时具备原 outbox CONFIRMED、唯一真实 local_echo 和明确手机确认。当前代际任何未核对输入立即阻断复用；旧代际八条接受仍通过已有历史指纹和明确逐项范围审核，不作一般解除阻断。

新增审核种类 `integrated_background_bmp` / `restarted_background_bmp` 固定请求和正文，审核与消费两次检查完整准入。它们用 `prior_input_context_reconciled` 和 `background_admission_verified`，绝不伪造当前进程已经退出；其他种类仍要求原有新进程代际。

## 实机阶段（待前置核验与具体批次许可）

入口 `scripts/probe_wechat_background_service.py` 默认仅准备，不排队/输入。单次集成通过 SocialService dispatch，电脑回显和手机核对完成后，再启动新服务进程进行 1800 秒持续接收。新进程先尝试 dispatch 已确认的旧 action，必须零新增许可/操作。发送第二条 RESTART 标记仅一次，随后持续读取 30 分钟；约第 900 秒关闭读取器和服务对象，从原游标重启新读取子进程，再次 dispatch 原 action 必须无重放。循环为串行读取，不与基线/echo RPC 竞争；数据库提交后才能 ACK。

持续观察记录前台命中、覆盖间隔和鼠标位置变化，目标前台/观察断流/读取错误即停止。接收只限 filehelper，独立读取窗口状态不触发输入或重新提交。最终提供服务/读取 PID、起止游标、轮询数、真实持续秒数、去重、原许可计数和恢复证据。离线模拟时长不能替代真实 30 分钟。

B21 最小化/托盘独立验证和 BG3 锁屏不由此自动通过。工作台原有沙盒不据此显示真实后台已开放；本阶段只提供显式后端和真实服务验收入口。


## 已完成验证与当前停点

全部微信回归 632 passed（90.31 秒），Ruff 通过。新测试覆盖原 SocialService 单次原账本效果、两个固定标记、重启不重放、输入前拒绝不支持字符、锁下再次准入、准备失败、取消后先回收再还原、未回收保留锁、前台/后台共用锁、未确认代际拒绝、撤销证据后审核不可消费、结构化结果拒绝多余私密字段。

`evidence/w2b19-offline-validation-2026-11-08.json` 只读审计：原账本 14 次操作、12/8 次输入/提交许可、活动 0、完整性 ok，旧八条 UNKNOWN 与 COVERED 待手机核对，共九条 UNCONFIRMED。准入真实返回 `background_current_generation_unreconciled`，未绕过手机确认。

`evidence/w2b19-covered-readonly-preparation-2026-11-08.json`：同一固定进程/窗口、原账号/filehelper、双模式空输入、Qt 焦点 false、五点遮挡和后台 worker 准备均通过；259 次观察、无目标前台/鼠标位置变化、最大间隔 16.84ms，gate 0→1→0、子进程、观察器和锁恢复。零输入、零提交、零账本写入。

新的两次实机消息以及完整 30 分钟验收均尚未执行，已请求明确批次范围；没有预创建 outbox、消费审核或进行一次失败后的重试。
