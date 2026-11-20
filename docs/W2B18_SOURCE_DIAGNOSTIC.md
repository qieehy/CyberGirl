# B18 前台来源诊断：只输入，不发送

2026-11-07。状态：该单次输入诊断已获批并消费；值写入时确实观察到微信处于前台，完整草稿保留，提交为零。它不构成 B18b/BG1 发送验收，不再次调用此标记。

## 要解决的问题

value 和 deferred 两次单次输入被同一前台守卫停止。deferred 尚未投递伪焦点或鼠标消息；但旧报告合并了事件 HWND 和 GetForegroundWindow 结果，不能复原具体触发来源。原失败和六条 UNCONFIRMED 保留，不能凭推测继续发送。

Microsoft 的[事件常量定义](https://learn.microsoft.com/en-us/windows/win32/winauto/event-constants)规定 EVENT_SYSTEM_FOREGROUND 对应窗口自身（OBJID_WINDOW/CHILDID_SELF）；[官方观察器示例](https://devblogs.microsoft.com/oldnewthing/20131202-00/?p=2503)核对这两个字段，并说明事件是异步通知，处理时窗口可能已经消失。因此仅在稍后的查询中看到其他应用，不能否定此前发生过短暂切换；也不能把缺少字段的旧通知当成已证明的程序激活。

观察器现记录固定阶段、来源（event/event_current/sample/guard_current）、目标事件的 object/child ID、事件窗口是否等于当时当前前台、当时当前前台是否属目标进程，以及事件以外的直接命中。最多 16 条目标事件，额外只计丢弃数量；没有其他应用标题、正文、按键、鼠标坐标或截图。**没有过滤/忽略原目标事件，也没有削弱前台停止条件。** 即使后来的当前查询不同，原目标通知仍触发停止，诊断结果再人工审阅。

两种对象标识的自建隐藏窗口 NotifyWinEvent 测试已证明：确实可能收到事件但当前前台始终属于别处。该测试只对自建窗口发通知，不调用任何应用激活函数，不接触微信；它证明新证据能区分两类观察，不证明旧微信通知属于哪一类。

## 唯一拟议动作

| 项目 | 固定范围 |
| --- | --- |
| 请求 | `w2b-background-20261107-source` |
| 正文 | `W2B-20261107-background-source-中文🙂` |
| 目标 | 原认证测试账号的新登录代际、规范 filehelper 的独立窗口 |
| 已核验实例 | PID 6684、创建代际 134385311807932727、HWND 3605350；动作前重新核对，不换窗口 |
| 方法 | 一次完整 UIA 值写入；前后目标/空输入/正文核对及前台来源观察 |
| 次数 | 输入至多一次；伪焦点、窗口鼠标消息、提交均为零 |
| 入口 | `probe_wechat_background_trial.py --diagnose-reviewed-once`，不能与发送模式合用 |
| 禁止 | 抢前台、真实键鼠、剪贴板、清草稿、改写/重试、切会话、失败改用发送通道 |
| 期限 | 原 15s 准备、20s 控制、25s 父等待、90s 外层；有界回收与 gate 恢复 |

先由用户自行处理 deferred 草稿、完整退出并重登，打开独立 filehelper，留空且未最小化并切回当前应用；再做只读准备，固定新 PID/创建代际/HWND。准备通过后才能请求这一项具体许可；不自动换目标，不继承旧许可。短暂诊断期间保持当前应用前台，不主动切微信；观察到目标前台仍立即停止。

用户已明确完成上述准备。21:22（Asia/Shanghai）只读检查核验原认证账号、独立窗口原生祖先链/filehelper、同一编辑器/按钮、Value/Text 双模式空输入、按钮释放和诊断 worker 准备。272 次采样、最大间隔 20.255ms，没有目标前台事件；鼠标位置变化仅计数、不归因。gate 0→1→0、观察器/子进程/锁均回收。新窗口证据见[独立窗口准备](evidence/w2b18-source-standalone-readiness-2026-11-07.json)。最初只读枚举中的另一窗口 5574146 未通过独立根匹配，在读取草稿前拒绝，保留该拒绝证据，不把主窗口误认为用户未准备好。

后续只读审计确认所有旧输入进程代际已退出、当前代码摘要与 535 项回归版本一致；原账本仍 10 次操作、累计许可 8/5、六条 UNCONFIRMED、活动操作 0、完整性 ok，无 SOURCE 动作。见[新代际与原账本核对](evidence/w2b18-source-new-generation-audit-2026-11-07.json)。这项准备没有产生新输入/发送许可。

本动作不调用上游 send。控制类、窗口传输和原账本三个位置独立禁止伪焦点/字符/鼠标/提交，原 SendState 的 SUBMIT_PERMITTED 同样拒绝此固定正文。仅 `BG_VALUE_INPUT_INTENT` 可以持久化且一次性，输入前重新核对暂停、时效、原账号/filehelper/编辑器及双模式空输入。正文不完整、超时或前台拒绝后均保留草稿。完成诊断也不声明发送成功、不收集新发送回显、不接受手机确认，不自动进入真正发送。

## 六条旧不确定性

这项新动作须单独具名接受原 directed/posted/sequence、background-control、background-value、background-deferred 六条不确定性。它们的状态一律不改。新增历史识别仅接受 DEFERRED 精确正文、原因为 `background_target_became_foreground`、无提交许可且唯一 BG 阶段为 VALUE_INPUT 的情形；旧操作结束/回收/恢复和新登录代际仍必须通过。

原账本只读核对已能计算六条具名历史摘要，没有创建 review、输入或提交许可。见 [历史只读核对](evidence/w2b18-source-history-readonly-2026-11-07.json)。当前仍 10 次操作、累计许可 8/5、六条 UNCONFIRMED、活动操作 0，原阻断保持。

## 已完成的检查与替代路线结论

最终全部微信专项 **535 passed（250.38 秒）**，含新诊断、观察来源、原账本审核、发送及接收回归；诊断专项 22 passed（6.94 秒）。Ruff 与 PowerShell 语法检查通过；5 种缺少许可/互斥模式组合在效果前拒绝。见[最终验证、代码摘要与原账本复核](evidence/w2b18-source-diagnostic-validation-2026-11-07.json)。不能用离线结果代替这项新的实机许可。

[当前草稿只读复查](evidence/w2b18-observer-source-readonly-2026-11-07.json)确认原账号/独立 filehelper 和完整 deferred 正文未变，期间没有目标前台事件，采样最大间隔 16.839ms，gate/观察器/锁均恢复。它没有重新写值，因此不能解决写值时来源问题，也不是新诊断的空输入准备。

Qt 5.15.14 [键盘映射参考源码](https://raw.githubusercontent.com/qt/qtbase/v5.15.14-lts-lgpl/src/plugins/platforms/windows/qwindowskeymapper.cpp)对直接 WM_CHAR 走单字符路径，而在 KEYDOWN 的队列预取分支另有代理项合并逻辑。这纠正了“整个版本均无代理项处理”的过度概括。目标线程可能在后续字符入队前就处理 KEYDOWN；没有原子队列/处理绑定证据前，不把增加虚拟键消息或缩短延时作为可执行修复。源码参考也不等于本机微信已走该分支。

原生 setText/interface_cast 的只读位置调查不包含原生方法调用或注入；IMM 跨线程、共享剪贴板及原生注入也未获本方案授权，均没有实机尝试。下一步以这项**无提交诊断**查明现有失败来源，再选择有证据的修复，避免反复以新发送名义试错。

## 获批单次实机结果

用户批准单次输入诊断并接受旧六条不确定性。动作 `9c48e9d7cf1aa63f33be69bc8f65661cb9af1cc534bc01955838706869d17758`，操作 `f2cb96e5d0ed4e1bab15d137976c5440`。原账本输入/提交许可 1/0，唯一细分阶段 `BG_VALUE_INPUT_INTENT`，窗口消息为零；不含伪焦点、鼠标或提交。

值写入阶段同时记录了目标的窗口自身事件（object_id=0、child_id=0）、回调时当前前台属于目标且与事件窗口相同，以及直接采样命中。来源为 event_current/event/sample，51 次采样、最大间隔 114.571ms；观察期间鼠标位置变化为零。**此次证据确认微信确实处于前台，不能用“仅通知、不是真前台”的解释放行。** 它仍不单独证明具体激活调用栈或人为/程序归因；前台守卫正确停止，原结果保留 UNKNOWN。

事后只读检查仍见微信前台、Qt 输入焦点为 true；双模式完整正文相等，长度 35，SHA256 `8cd0a33ab93455170aa808c034d7f3ef2ccce1d625cfd0d6925385ca865294e5`。没有提交，也没有人工清理/程序清理草稿。worker、读取器、gate 原值、观察器和锁回收通过；当次 worker_local_cleanup_verified=false 保留，不追改。

原账本现 **11 次操作、累计许可 9/5、七条 UNCONFIRMED、活动操作 0、完整性 ok**，原 `submission_or_draft_requires_review` 阻断保持。原六条状态均不变，新 SOURCE 许可已消费，不加入后续自动试发。证据：[本次诊断](evidence/w2b18-standalone-source-once-2026-11-07.json)、[现场只读保全](evidence/w2b18-source-after-foreground-readonly-2026-11-07.json)、[原账本审计](evidence/w2b18-source-result-ledger-audit-2026-11-07.json)。

后续判断：现有 UIA 值写入不能按 BG1 放行，不能靠过滤通知或放宽前台守卫继续。Microsoft 的 [WS_EX_NOACTIVATE 说明](https://learn.microsoft.com/en-us/windows/win32/winmsg/extended-window-styles)仍允许程序显式调用 SetForegroundWindow/SetActiveWindow，不能把添加此标志当成已证明的禁止激活修复；[EnableWindow](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-enablewindow)还会禁用用户输入并同步发送系统消息，尚未证明本机 UIA 仍可写，也未实施这些改动。原完整字符目标保持未完成。已向用户询问是否接受先按常用中文/BMP 范围验收，补充平面字符在任何输入前拒绝；在用户接受变更前不降低原验收标准、不创建该范围的新实机动作。
