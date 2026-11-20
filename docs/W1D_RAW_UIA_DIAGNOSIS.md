# W1-D：原始 UIA 控件暴露诊断

日期：2026-10-29（Asia/Shanghai）。**诊断已完成；当前登录状态下，原始 UIA 未提供聊天控件，微信收发出口仍未通过。**

## 结果与含义

独立于 computer-use 快照，使用 Windows 自带 .NET UIAutomationClient 分别遍历 Raw View 和 Control View。两者均返回同样的 3 个节点，无异常、无截断，最大实际深度为 1：

| 层级 | 类型 | ClassName | FrameworkId | 可用模式 |
| --- | --- | --- | --- | --- |
| 根 | Window | Qt51514QWindowIcon | Win32 | Window、Transform |
| 子节点 | Pane | Qt51514QWindowIcon | Win32 | 无 |
| 子节点 | Pane | MMUIRenderSubWindowHW | Win32 | 无 |

客户端启动器版本 4.2.0.9，主窗口 HWND=460506、PID=30376；窗口可见、未最小化。采集器为 64 位，采集器和微信均处于 Session 1。此状态下没有 Edit、List、Button 或相应文本/输入模式。

由此可排除“只因为之前的快照工具没有显示子控件”这一解释，也没有达到本次深度/节点上限。**更准确的结论是：当前桌面用户通过 UIA 得到的仅为 Win32 窗口层，尚无候选项目依赖的 mmui 聊天语义控件。**

不能据此判定 Qt 无障碍模块一定被禁用，也不能证明微信所有 4.2 小版本均不兼容。进程权限完整性级别、Qt 提供方注册/初始化和版本内部变化尚未逐一确认；同 Session 不等于相同完整性级别。未执行修改内存、注入、环境变量切换、退出重登或客户端降级。

## 诊断实现与执行边界

新增可复用的 [probe_wechat_uia.ps1](../scripts/probe_wechat_uia.ps1)。从精确安装路径匹配微信进程，仅枚举该进程可见顶层窗口，并读控件类型、类名、框架、进程号、离屏/启用状态及支持的 Pattern 标识。

脚本不查询 Name、Value、TextPattern 正文、AutomationId、RuntimeId 或窗口标题，也不实例化/调用操作模式。每个视图最多 300 节点、深度 8、最多 8 个窗口；独立子进程由 30 秒外部看门狗限制，超时只终止该诊断子进程。已有输出文件拒绝覆盖。

在正常桌面用户 PowerShell 中复现（使用新的输出文件名）：

```powershell
.\scripts\probe_wechat_uia.ps1 -OutputPath .\docs\evidence\w1d-uia-new.json
```

父进程调用系统 Windows PowerShell，以加载系统 UIAutomationClient。初次沙箱账户运行因其 PowerShell 执行策略禁止脚本而退出，UIA 尚未执行；在正常桌面用户环境运行同一脚本成功，未改变执行策略。沙箱失败记录与成功记录均保留，不把执行策略错误归因于微信。

本次无鼠标/键盘输入，无模型调用，无外部消息。UIA 查询本身可能使系统建立可访问性连接；这是读取接口所需的行为，不是人工点击或消息提交。

## 下一项：控件提供方初始化路径评估

下一项 W1-E 将核验可用的初始化方式及其 4.2.0.9 适用性，先检查来源和副作用，不再重复这份相同状态下的原始树检查。

新增研究线索：[wechatauto-replica 的上游说明](https://github.com/fanyuantaier/wechatauto-replica/blob/main/README_pypi.md)报告过 Qt 窗口外壳/控件树问题，并描述带 gate RVA 的 UIA 路线，当前页面列举的实测版本为 4.1.15.53。这只是动态 README 线索，尚未固定其源码、审查许可或验证本机 4.2.0.9；不是可直接运行的修复依据。后续需固定提交、检查初始化是否修改目标进程、明确版本匹配与退出条件，再形成具体可审阅的接入方案。不得把“UIA”名称等同于完全只读或无注入。

若无可验证的初始化方式，则另行设计人工辅助模式；图像/坐标输入不能补齐稳定身份、去重与回执合同。W2 收发仍在取得相应能力证据和明确测试会话/内容之后执行，不以能找到窗口作为放行条件。

## 证据

- [桌面用户原始 UIA 结果](evidence/w1d-uia-desktop-2026-10-29.json)：两个视图均完整遍历，3 节点，无异常。
- [沙箱执行失败](evidence/w1d-uia-sandbox-2026-10-29.json)：worker_failed，退出码 1；终端诊断为脚本执行策略限制。
- [关键验证摘要](evidence/w1d-validation-2026-10-29.json)：脚本语法、原始结果与脚本指纹。

本轮仅做脚本语法和真实诊断结果的一致性验证；未重跑 S1–S4、未重复真实订阅验收。
