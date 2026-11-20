# B18 常用中文 + 固定窗口回车提交

2026-11-07。状态：具体许可已获批并消费；唯一新回显和用户手机全文确认通过，投递已单独记 CONFIRMED。收尾只读一致性检查失败保留，遮挡场景尚未验收，后台发送流程未通过。

## 仅替换提交方式

上一条 BMP 正文完整，但窗口鼠标提交段触发前台事件守卫且没有唯一新回显，草稿保留，结果 UNKNOWN。只读调查还表明不能仅因 UIA 报告 InvokePattern 可用就调用按钮：本机按钮实际 action 路径只处理焦点动作，没有可用的 press 分支。没有试过 Invoke，也不以另一接口补交旧草稿。

本候选保持原已固定上游的初始化、伪窗口焦点开始/结束、逐码元 BMP 正文和全文核对，**仅将 click_screen 提交位置替换为同一 HWND 的一次回车按下/释放**。这是明确补丁，不算未经修改原版成功。不是全局按键，不调用 SendKeys、SetFocus、SetForegroundWindow、UIA SetValue/Invoke、剪贴板或真实键鼠。

正文准入为单行可打印 BMP、至多 96 码元，补充平面字符/代理项/控制字符/空白正文/超长文本在效果前整体拒绝，不截断、不删除后发送。用户对该受限范围的选择不授权本条新消息。

## 唯一动作范围

| 项目 | 固定值 |
| --- | --- |
| 请求 | `w2b-background-20261107-return` |
| 正文 | `W2B-20261107-background-return-中文` |
| 目标 | 原认证测试账号、规范 filehelper、固定独立窗口 |
| 新准备实例 | PID 40604、创建代际 134385356084925593、HWND 136907112 |
| 入口 | `--send-reviewed-once --return-mode`，不能与其他提交/诊断模式合用 |
| 提交 | WM_KEYDOWN(0x100)/WM_KEYUP(0x101)，VK_RETURN(0x0D)，lParam 固定为 0x001C0001/0xC01C0001 |
| 次数 | 一次完整消息输入；一次回车按下及释放；窗口鼠标消息零 |
| 限制 | 不抢前台、不动真实键鼠、不清草稿、不补发或改路线 |
| 时限 | 原 15s 准备、20s 控制核心、25s 父等待、90s 外层期限 |

完整正文与同一编辑器经两模式核对后，先在原账本持久化 RETURN_DOWN/UP 两项意图，才投递任一回车消息；按下/释放之间没有 UIA 或父确认往返。每条实际入队前仍核验原生进程/线程/窗口类代码、原账号/filehelper/编辑器及前台观察。按住 Shift/Ctrl/Alt/Win 时拒绝回车，防止组合键改变行为；只查询这些修饰键状态，不记录按键内容。回车参数之外的按键消息拒绝。

原账本、控制与传输对提交方式分别限界：Return 标记不能创建鼠标提交意图，普通鼠标标记不能申请回车意图；Return worker 的 SetValue 写入端保持关闭，原生传输也拒绝该模式的鼠标消息。检测正文变化/时效/暂停/新消息/账号或目标变化/前台事件/超时即停止，按原 UNKNOWN 规则保留现场，不重试。

## 新登录只读准备与历史

用户已明确自行处理旧 BMP 草稿并完整重登。本机已核验该新进程/独立窗口、原认证账号、原生祖先链与 filehelper、双模式空输入、同一编辑器/按钮、按钮释放和后台 Return worker。262 次观察、最大间隔 23.188ms，目标前台事件及指针位置变化均为零；gate 0→1→0、子进程/观察器/锁均恢复。[准备证据](evidence/w2b18-return-new-session-readiness-2026-11-07.json)。动作前重查同一实例，不自动换窗口。

新动作须具名接受八条旧不确定性：directed、posted、sequence、background-control、background-value、background-deferred、background-source、background-bmp；各原状态不变。新增 BMP 识别仅接受精确旧正文、输入/提交许可 1/1、完整 CHAR 意图和 MOVE/DOWN/UP、精确前台拒绝原因，以及原有的结束/回收/恢复和旧进程退出要求；不是对任意中文提交 UNKNOWN 的豁免。

## 验收与检查

仍须完整正文、原账号/filehelper/本人方向、基线后唯一新数据库回显、手机全文确认、全程前台观察、空输入/按钮释放/伪焦点结束、子进程退出与 gate 恢复；API 返回、消息入队或空框不证明送达。任何失败提前停止，B18b 未通过前不开放 B19–B21。

最终全部微信专项 **581 passed（87.54 秒）**；相关控制/原账本/独立窗口/原生和进程专项 111 passed（10.09 秒）。Ruff、PowerShell 语法检查通过；4 种互斥模式/缺少许可的 CLI 组合在效果前拒绝。最后加入回车按下后前台拒绝仍保留原始原因、停止释放/后续消息且不重试的覆盖。

原账本只读审计仍 12 次操作、累计许可 10/6、八条 UNKNOWN、活动操作 0、完整性 ok，原阻断保持；所有旧输入进程代际已退出，没有 RETURN action。[最终验证与代码摘要](evidence/w2b18-return-validation-2026-11-07.json)、[八条历史与新代际核对](evidence/w2b18-return-history-readonly-2026-11-07.json)。上述准备、代码和测试均未产生新的输入/提交许可。

## 实机结果与送达确认

用户批准单次回车试验及旧八条接受。动作 `218b7db1a667c73019a6d2c913e37dde46ccbb1347a65b78fdc5468e821fc6c7`，操作 `3034214689324a1ca0529f8891ae08fb`。33 个正文码元完整核对后，已投递一次 Return 按下/释放；累计窗口消息 36，输入/提交许可 1/1。全程前台事件 0、指针位置变化 0，203 次采样、最大间隔 111.158ms。没有鼠标消息、SetValue、真实键鼠或补发。

随后原生一致性读取触发 `native_object_changed`，伪焦点结束未投递，focus_cleanup_verified=false；保留原 UNKNOWN 控制结果。worker/读取器/观察器退出，gate 与锁恢复，worker_local_cleanup_verified=true。父进程已核对原账号/filehelper/本人方向/基线后唯一完整回显；事后只读两模式均空、按钮禁用/释放、目标非前台，但 Qt 输入焦点仍 true，不能把该时点当作恢复已通过。

用户明确确认“手机收到完整标记，电脑输入框已空”。独立 `--confirm-delivery-only` 入口还核对原账本中的唯一 local_echo 证据及其 operation、正文摘要、绑定代际，再追加 manual_confirmation：outbox 记 CONFIRMED，attempt 原 UNCONFIRMED 和原收尾错误不变，`background_acceptance=false`。这只确认送达，不豁免 BG1 恢复条件。[单次结果](evidence/w2b18-standalone-return-once-2026-11-07.json)、[空输入但 Qt 焦点未恢复的只读检查](evidence/w2b18-return-after-echo-readonly-2026-11-07.json)、[手机送达确认](evidence/w2b18-return-phone-delivery-only-2026-11-07.json)。旧八条不确定性均不变。

用户随后表示微信一直放在前面，并直接要求“再测一次”。虽然该次系统前台采样没有命中微信，本轮不凭该差异认定已完成遮挡验收；改为新标记并在代码中持续核验其他活动应用遮住聊天区四角和发送点。用户已重新登录并确认遮挡准备。见 [明确遮挡重测](W2B18_COVERED_CONTROL.md)。
