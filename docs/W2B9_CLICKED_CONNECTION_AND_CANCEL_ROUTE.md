# W2-B9：当前按钮连接与取消默认路径

2026-11-02。**当前发送按钮的唯一 clicked 连接已核实，回调进入当前 ChatInputView 的 `Send(SendActionType)`，参数值为 3。固定 DLL 的取消默认路线已复现，但真实 HWND 入口查询被 Windows 拒绝，原生过滤器和自绘状态清理未验证；鼠标候选继续关闭。没有发送、聚焦、输入或清理草稿，W2-B / V0 未通过。**

## 当前按钮的实际连接

新增 `qt_connections.py`，通过既有只读工作进程的 `connection_diagnostics` 使用。它在原生账号/filehelper、窗口代际和草稿的前后核验之间，只检查当前按钮的 clicked 连接：

- 从原有 UIA → Qt 对象映射取得按钮与输入框，要求两条 QObject 父链完整、共享同一主窗和唯一的 ChatInputView，并固定类、metaobject 和 vtable。
- 按钮的当前原生字段也须与该输入区域的子按钮字段对应，避免仅靠继承或相似控件建立连接。
- 固定指令证明 QObjectPrivate、SignalVector、Connection、QSlotObjectBase 的字段布局；再读父类信号计数，得出 clicked 的全局索引 7。不是直接套用 Qt 参考结构。
- 只跟随这一个信号的连接链，最多 16 条、总读取上限 64 KiB。存在通配信号连接、未知接收者、回调、捕获对象、类型、循环或代际变化时停止；不读取其他接收对象内容，也不枚举其他信号。
- 必须只有一条连接，接收者与回调捕获对象都精确等于当前输入区域，同线程 AutoConnection 或显式 DirectConnection 才接受为已观察路线。报告不返回对象地址、线程指针或 RuntimeId。

参考结构来自 Qt 官方 [QObjectPrivate 连接结构](https://github.com/qt/qtbase/blob/5.15/src/corelib/kernel/qobject_p.h) 和 [槽对象实现](https://github.com/qt/qtbase/blob/5.15/src/corelib/kernel/qobjectdefs_impl.h)。它们只帮助解释结构，字段依据仍是本机固定 DLL 指令与实际回读；未复制这些源码或增加运行依赖。

第一次读取在类型校验处停止并恢复。静态复核发现固定 DLL 在连接类型存储中先写 `0xFFFF`，设置有效字段时保留高位；原解析器错误地要求所有高位为 0。修正为只解析低 5 位，继续严格校验类型和槽对象位；新增填充位场景通过后才进行了第二次读取。没有把首次失败当作连接不兼容结论，没有重发或改变草稿。

第二次现场读取及回读通过：首次累计读取 **8,537 字节**，唯一连接为同线程 AutoConnection，接收者与捕获对象均为当前 ChatInputView，按钮按下位为 false。当前草稿非空，前后哈希仍为既有标记哈希；工作进程退出及可访问性恢复均确认。见 [首次停止](evidence/w2b9-clicked-connection-2026-11-02.json)与 [最终连接观察](evidence/w2b9-clicked-connection-final-2026-11-02.json)。两个租约均按写前记录、同一进程句柄和恢复回读收尾。

该诊断不建立初始空输入快照，不生成聚焦、输入或提交许可，始终报告 `submission_enabled=false`。没有执行 clicked、回调或 Send 方法。

## 从连接到 Send 槽的固定字节复现

新增 `wechat_pinned_image.py` 和 `inspect_wechat_submit_route.py`，只读磁盘 DLL 和上述连接证据。复现确认：

| 层次 | 固定依据 |
| --- | --- |
| 当前按钮 | 实时 clicked 连接的回调实现 RVA `0x1C49180`，捕获当前输入区域 |
| 连接设置 | 从输入区域 `+0x238`、子对象 `+0x1D8` 取得按钮，连接 clicked 到上述回调，并捕获该输入区域 |
| 槽对象调用分支 | 取得捕获对象，传入整数值 3，尾调用 RVA `0x1C36EB0` |
| 真实类型名称 | ChatInputView MOC 的第 8 个方法名为 Send，单参数类型名为 SendActionType |
| MOC 分派 | 第 8 个方法的跳表分支也指向 `0x1C36EB0`，与回调目标一致 |

现场证据生成时使用 ABI 层的 `Send(int)` 标签；随后 MOC 解析将类型名明确为 `SendActionType`，当前代码报告已修正，历史证据未覆盖。参数值 3 和目标 RVA 一直一致。这是提交槽的连接证明，**没有执行该槽，也没有继续宣称其内部网络发送、返回或投递结果已验证**。

见 [静态复现证据](evidence/w2b9-static-submit-route-final-2026-11-02.json)。命令使用新的输出路径，避免覆盖旧证据：

```powershell
& '.\.venv\Scripts\python.exe' scripts/inspect_wechat_submit_route.py `
  --match-connection-evidence docs/evidence/w2b9-clicked-connection-final-2026-11-02.json `
  --output <新的证据文件路径>
```

## HWND 入口和取消路线的限制

新增原绑定窗口的只读入口探针 `probe_wechat_window_proc.py`，只查询窗口过程信息，校验结果属于固定模块的可执行区并与磁盘字节一致；对于不透明句柄、模块外值或变更会拒绝，绝不调用返回值。该探针没有可访问性租约或任何窗口消息。Microsoft 说明 [GetWindowLongPtrW](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getwindowlongptrw) 的返回值也可能是代表过程的句柄，因此不能把任意结果都当作地址解引用。

首次查询返回 0；补齐固定数值错误诊断后复查，明确得到 **Win32 错误 5（访问被拒绝）**，停在窗口过程查询，尚未执行类过程查询或读取目标代码。没有提升权限、改写窗口过程或注入代码。见 [初次查询](evidence/w2b9-window-procedures-2026-11-02.json)与 [错误细节](evidence/w2b9-window-procedure-error-2026-11-02.json)。因此仍未把静态注册入口绑定为当前 HWND 的实际入口。

磁盘 DLL 的类注册和取消默认路线进一步确认：

- 类注册将 RVA `0x26B04F0` 放入 WNDCLASSEX 的过程字段，再调用导入表中已核实的 RegisterClassExW；这个过程调用消息分类器 `0x26B2080` 和调度器 `0x26B22D0`。
- `WM_CANCELMODE (0x1F)` 和 `WM_CAPTURECHANGED (0x215)` 的固定跳表项均进入默认分类，结果为 `0x21E`。
- 在原生事件过滤器未提前处理消息时，这个分类进入调度器的未处理返回分支，类过程再调用已核实的 DefWindowProcW；所查默认分支没有专门清除 XOutlineButton 状态位或 Qt 待处理事件的动作。

**这里限定的是固定程序的默认路径。原生过滤器是否安装、是否清理状态和当前窗口是否被替换过程都未观察，不能断言实际运行中绝无其他处理。** Microsoft 对 [WM_CANCELMODE](https://learn.microsoft.com/en-us/windows/win32/winmsg/wm-cancelmode) 的默认处理说明也不足以证明自绘按钮清理。因此不把取消调用返回、释放捕获或按钮当前未按下当作半个手势已经可撤销的证明。

结合 B8 中左键内部释放可进入 clicked 的结果，现有鼠标候选仍不具备真实启用条件。本轮没有发送 DOWN、UP、CANCELMODE、键盘消息、回车或未知替代事件。

## 验证、收尾和下一步

新增 **34 项**场景，覆盖连接所有者/子按钮/接收者/捕获变化，信号索引、类型及填充位，链循环/数量/稳定性，代码与信号继承布局变化，不同主窗/输入区域，诊断零许可，以及窗口过程不透明值、非执行区、字节或入口变化。与受影响的既有场景合计 **108 passed**，Ruff 通过；静态复现脚本通过。

两次连接租约最终均恢复。独立复查为可访问性 0、外层 3 节点；无密钥账号仍匹配 W2-A。见 [恢复复查](evidence/w2b9-restored-2026-11-02.json)与 [账号复查](evidence/w2b9-account-2026-11-02.json)。账本只以 mode=ro 查询，仍为 3 个历史尝试、3 条操作记录、1 次输入许可、1 次提交许可、1 条人工确认，CONFIRMED 1 / FAILED 2，通道仍为 `submission_or_draft_requires_review`。

没有写入发件账本、密钥或明文数据库，没有打开消息数据库、调用模型、修改收藏、清理草稿或增加投递。汇总见 [本轮记录](evidence/w2b9-result-2026-11-02.json)。

下一项 W2-B10：优先只读调查固定 DLL 中不产生鼠标半手势的单事件定向提交入口及其目标/焦点/事件队列条件，先证实输入事件到 Send 槽的连接，再设计离线合同。若仍需要成对事件，不能将它当作取消问题已解决；不盲试键盘或未知消息，不重复尝试被拒绝的窗口过程读取来假定成功。真实提交继续关闭，现有草稿保护、旧动作不重放和仅 filehelper 的范围保持。
