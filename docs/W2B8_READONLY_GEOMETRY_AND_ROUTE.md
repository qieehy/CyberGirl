# W2-B8：真实几何只读观察与鼠标事件静态核查

2026-11-02。**真实按钮的几何与 DPI 观察通过，控件事件到 clicked 信号的静态路径已复现；前台/命中条件当前不满足，真实窗口消息路由和取消行为仍未验证。没有发送、点击、聚焦或清理草稿，原发送路径和通道继续关闭，W2-B / V0 未通过。**

## 只读观察器

新增 `scripts/wechat_click_geometry.ps1` 和 `bridges/wechat/click_geometry.py`，通过既有可终止工作进程的 `geometry_diagnostics` 接口使用。UI 子进程以 `-ReadOnly` 启动，只允许 observe、draft_patterns 和 click_geometry；聚焦、输入、提交命令仍在写入管道前拒绝，PowerShell 侧也再次拒绝。

观察前后均复核当前账号、原生 filehelper 输入页、窗口代际、输入控件标识和草稿哈希。已有非空草稿可以接受只读观察，不能伪造一个初始空草稿去获得动作许可。诊断不设置 WorkerSession.initial，不消费或生成聚焦/输入/提交许可，报告始终为 `submission_enabled=false`。

几何查询仅在线程内临时设置每显示器 DPI v2 上下文，并在 finally 中恢复、回读。观察 UIA 物理屏幕边界、客户区到屏幕的转换、按钮中心 ScreenToClient 往返、接收线程、前台、命中根窗口与进程、同一按钮的 UIA 祖先、目标线程捕获和当前硬件按键状态。没有移动鼠标、激活窗口、设置捕获或发送鼠标/键盘消息。

先以 WindowFromPoint 判断归属；若中心被其他进程遮挡，不对那个进程执行 UIA FromPoint 或读取其控件。原始坐标、HWND/PID/线程号、RuntimeId 和控件令牌仅在诊断工作进程中流转，几何报告只保留尺寸、DPI、布尔检查和草稿哈希。目标线程的“无捕获”观察也不被当作整个桌面的输入授权。

API 语义依据 Microsoft 官方 [UIA 屏幕缩放](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-screenscaling)、[线程 DPI 上下文](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setthreaddpiawarenesscontext)、[ScreenToClient](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-screentoclient) 和 [GetGUIThreadInfo](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getguithreadinfo)。上下文恢复通过不代表提交路线可用。

## 本机观察结果

使用原绑定的 Weixin 4.2.0.9 进程/窗口和 W2-A 认证账号证据，完成一次短时启用与恢复。见 [几何证据](evidence/w2b8-geometry-2026-11-02.json)及对应 [租约记录](evidence/w2b8-geometry-2026-11-02.journal.jsonl)。

| 检查 | 结果 |
| --- | --- |
| 原生账号及 filehelper 输入页 | 前后核验通过 |
| 按钮尺寸 / 客户区尺寸 | 72×40 / 1276×798 物理像素 |
| DPI | 144；线程上下文恢复并确认 |
| 按钮完整位于客户区、中心余量、转换一致、坐标可编码 | 全部通过 |
| 可见、启用、目标线程无捕获、观察时无按键干扰 | 通过 |
| 微信在前台、中心命中原窗口/进程/按钮、输入焦点 | 不满足；保持当前窗口状态，没有自动调整；报告不保留其他窗口身份 |
| 当前输入 | 非空；前后哈希一致，仍为旧标记的既有哈希 |
| 工作进程退出 / 可访问性恢复 | 均确认 |

因此本轮证明了真实缩放和几何读取，**没有证明一次无遮挡的真实按钮命中**。中心的窗口归属不匹配后，没有进入同按钮 UIA 命中分支，不能把该分支称为现场通过；也不能据此打开真实提交。

独立只读复查确认可访问性为原值 0、仅外层 3 节点；无密钥账号复查仍匹配 W2-A。见 [恢复复查](evidence/w2b8-restored-2026-11-02.json)和 [账号复查](evidence/w2b8-account-2026-11-02.json)。

## 固定 DLL 的按钮事件路径

新增 `scripts/inspect_wechat_button_route.py`，不加载或执行 DLL，不访问进程、不发窗口消息。它以 B6 的历史实时按钮 vtable 证据为起点，复核固定 SHA256 的磁盘 DLL，解析类/信号 MOC 元数据、事件跳表和相应代码字节。**这是历史 vtable 绑定加静态分析，不是本轮实时事件字节重读，也不是实际鼠标消息实验。**

可重复执行：

```powershell
& '.\.venv\Scripts\python.exe' scripts/inspect_wechat_button_route.py `
  --match-button-evidence docs/evidence/w2b6-button-dispatch-final-2026-11-02.json `
  --output <新的证据文件路径>
```

固定字节检查确认：

- XOutlineButton 的 QObject 主 vtable 中，event 槽 4 指向 RVA `0x5D1580`；其类型 2、3 事件分支分别调用虚表槽 18、19，而不是仅根据参考头文件猜测槽号。
- 两个处理函数的 RVA 为 `0x1EA2A50`、`0x1EA2BB0`。前者在左键且点在内部时设置对象 `+0x140` 的 `0x02` 位，再发出 pressed。
- 后者在左键且点在内部的分支清除同一位，依次调用 released、stateChanged、clicked 信号函数。MOC 确认相应信号名称与索引；这里的代码路径没有先检查 `0x02` 位是否已设置。
- clicked 信号函数 RVA `0x9239C0` 通过该类 metaobject 的索引 0 分派。确认到信号这一层，尚未追到当前按钮连接的真实发送槽，也未确认 Win32 消息到该 QWidget 的完整路线。

见 [静态复现证据](evidence/w2b8-static-mouse-route-2026-11-02.json)，包含公开模块 RVA、代码窗口哈希与明确的未验证标志。没有调用这些信号或改写对象状态。

## 取消缺口及结论

Microsoft 对 [WM_CANCELMODE](https://learn.microsoft.com/en-us/windows/win32/winmsg/wm-cancelmode) 的说明覆盖标准内部模式和释放鼠标捕获，不能代替自绘按钮状态、Qt 接收目标或待处理事件的清理证明。

Qt 官方参考 [Windows 鼠标处理](https://github.com/qt/qtbase/blob/5.15/src/plugins/platforms/windows/qwindowsmousehandler.cpp) 会从客户区消息参数形成鼠标事件，并管理自动捕获。参考 [窗口系统事件接口](https://github.com/qt/qtbase/blob/5.15/src/gui/kernel/qwindowsysteminterface.cpp) 同时存在同步及异步投递，默认异步队列说明了为什么 Win32 调用返回不能直接等同于控件事件完成。参考 [QWidgetWindow](https://github.com/qt/qtbase/blob/5.15/src/widgets/kernel/qwidgetwindow.cpp) 还会选择子控件，并维护按下时的接收者。这些是路线研究依据，**不把 Qt 5.15 参考分支当作微信发行版源码，也不据此断言本机实际配置或取消效果**。

结合固定 DLL 中释放分支的行为，目前不能证明 WM_CANCELMODE 能取消微信的半个点击。B7 的自有隐藏窗口测试只证明消息边界和回收，不能升级为自绘控件兼容结论；本轮没有向微信发送 DOWN、UP 或 CANCELMODE，也没有增加发送器或自动回退。

## 验证与后续

27 项新增校验覆盖非空草稿无许可、输出隐私、异常/越界坐标、负屏幕原点、小数边界、错误缩放、窗口变化、遮挡/焦点/捕获、观察中草稿变化、协议零许可、PowerShell 编译与线程 DPI 恢复，以及退出/恢复失败时不能报告成功。原相关回归加前 24 项为 **132 passed**；补齐收尾保护后新增 3 项并复跑本文件 **27 passed**，合计 **135 个不同用例通过**。Ruff 通过，静态核查脚本实际运行通过，没有新增依赖。

发件账本仅以 SQLite mode=ro 打开：仍为 3 个历史尝试、3 条操作记录、1 次输入许可、1 次提交许可、1 条人工确认，outbox 为 CONFIRMED 1 / FAILED 2。通道仍为 `submission_or_draft_requires_review`。没有重发旧标记、清理草稿、修改历史人工确认、模型调用、收藏修改或账本写入；测试接收进程残留为 0。汇总见 [本轮记录](evidence/w2b8-result-2026-11-02.json)。

下一项 W2-B9：继续只读追踪固定 DLL 的窗口到 Qt 调度、clicked 到当前发送槽及取消/状态重置路径，证明或否决现有候选。若鼠标半手势不能可靠收敛，先评估不产生半手势的单次定向提交路线，不能盲试回车、未知消息或新增回退。真实动作仍须处理现有草稿保护条件、使用新幂等动作和持久许可，不能重放已人工确认的旧动作；其他联系人和群继续禁止发送。
