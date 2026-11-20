# W2-B10：单消息键盘提交资格核查

2026-11-03。**固定 DLL 已复现输入框的回车分支、send 信号到 Send 槽的静态连接，以及原生字符/按键消息的状态处理。所查三种单消息键盘方案均未取得提交资格，没有新增发送器或放开发送。12 项新增静态验收与 2 项既有拒绝回归共 14 项通过，Ruff 通过。**

## 已核实的输入路径

新增 `scripts/inspect_wechat_keyboard_route.py`，仅解析固定 SHA256 的磁盘 DLL。历史 B4 证据用于绑定 ChatInputField 类及 vtable；不是本轮实时身份或输入状态证明。本轮没有访问微信进程、临时启用可访问性或操作 UI。

固定字节确认：

- ChatInputField 的 eventFilter 虚表槽 5 指向 RVA `0x21B1FB0`。其中 QEvent::KeyPress（6）调用 `0x21B0980`，释放事件（7）走另一分支。
- `Qt::Key_Return (0x1000004)` 和 `Qt::Key_Enter (0x1000005)` 的跳表项均指向回车处理 `0x21B0B90`。该处理读取修饰键、发送偏好及界面状态；存在分别以参数 1、2 发出 `send(SendActionType)` 的分支，也有不发出 send 的分支。
- 输入框 send 信号 RVA 为 `0x8BA080`。静态连接设置将它连接到 ChatInputView 的 `Send(SendActionType)`，目标 RVA `0x1C36EB0`；成员函数槽对象将信号参数传递给该目标。按钮的 Send(3) 和键盘的参数 1/2 因而不能混作同一种触发来源。

没有观察当前 eventFilter 是否注册、当前输入框的实时信号连接、发送偏好或相关界面状态。以上证明限定为固定类的条件分支及连接设置代码，不宣称当前按一下回车就必然发送。

## 三种单消息方案的结果

| 方案 | 固定程序中观察到的行为 | 资格结论 |
| --- | --- | --- |
| WM_KEYDOWN | 存在向 Qt 按键记录器写入按键的路径；原生接收者也可能被键盘抓取对象重定向 | 没有证明单独按下后的记录、队列和接收者状态能够收敛，不能作为无残留的单事件方案 |
| WM_CHAR | 所查转换分支形成 KeyPress/KeyRelease 两个 Qt 事件，键值均为 0，文本另行携带 | 不匹配已追踪的 Return/Enter 键值分支，不能当成可靠提交入口；也没有证明其他文本处理不会改变草稿 |
| WM_KEYUP | 进入既有按键记录的查找/移除路径 | 不是独立的按下提交，不得借用用户可能留下的记录或补发释放来试验 |

关键依据均在本机字节中复现：字符消息分支 `0x26E6B63`、键值为 0 的事件调用 `0x26E6C0F / 0x26E6C46`、按键记录写入路径 `0x26E78E3 → 0x26E7E20`、释放记录查找 `0x26E6FCA → 0x26E7A30`。没有读取运行中的按键记录器或任何聊天正文。

Microsoft 的 [WM_KEYDOWN](https://learn.microsoft.com/en-us/windows/win32/inputdev/wm-keydown) 定义包含重复次数、扫描码和前次状态；[WM_CHAR](https://learn.microsoft.com/en-us/windows/win32/inputdev/wm-char) 传递字符，不等同于虚拟键提交。Qt 官方 [Windows 键盘映射参考](https://github.com/qt/qtbase/blob/5.15/src/plugins/platforms/windows/qwindowskeymapper.cpp) 帮助解释按键记录与字符事件，参考分支不等于微信发行版源码。核查没有调用这些接口或发送消息。

结论是**所查候选未通过资格检查**，不是证明所有可能的单事件入口都不存在。没有枚举或盲试其他消息，也没有以 WM_CHAR 充当回车回退。

## 可复现验证

见 [静态证据](evidence/w2b10-static-keyboard-route-2026-11-03.json)。脚本对 vtable、MOC、分支跳表、调用目标、参数转发和按键状态指令逐项断言，再输出代码窗口哈希。诊断文件不授予输入或提交许可，始终为 `submission_enabled=false`。

```powershell
& '.\.venv\Scripts\python.exe' scripts/inspect_wechat_keyboard_route.py `
  --match-input-evidence docs/evidence/w2b4-native-identity-final-2026-11-02.json `
  --output <新的证据文件路径>
```

新增 12 项静态验收，包括语义路径、历史绑定拒绝、隐私投影，以及对虚表、回车跳表、信号调用、参数转发、字符键值和按键记录指令的定点变更拒绝。测试只读本机固定 DLL；未安装该 DLL 的环境跳过依赖它的静态集成场景，历史证据拒绝场景仍可执行。另复验原生后端在聚焦/输入前拒绝无效及未知提交路径的 2 项场景，合计 **14 passed**，Ruff 通过。

发件账本仅 mode=ro 查询：仍为 3 个历史尝试、3 条操作记录、各 1 次输入/提交许可、1 条人工确认，CONFIRMED 1 / FAILED 2，通道仍为 `submission_or_draft_requires_review`。本轮没有读取当前微信草稿来声称新的实时哈希证明，也没有操作草稿、打开消息数据库、读取密钥、请求模型或修改收藏。汇总见 [本轮记录](evidence/w2b10-result-2026-11-03.json)。

## 下一步决策

不再把“一个 Win32 消息”当作完整性保证。下一项 W2-B11 按 [有界完整手势离线合同](W2B11_BOUNDED_GESTURE_CONTRACT.md) 准备可审阅实现：复用已有定向点击候选，补齐持久意图记录、原窗口校验、超时 UNKNOWN 和人工对账；取消只作尽力清理，不声称能撤销已接受的远端事件。

这是一项离线实现待办，不是对真实鼠标路线的放行。实际窗口路由、命中和当前状态仍须资格核查；当前草稿与通道复核未完成前，不进行现场输入、提交、清理或补发。其他联系人和群继续禁止发送。
