# W2-B6：提交后诊断与无效按钮接口定位

2026-11-02。**已定位到原提交路径的接口缺陷：实际发送按钮使用通用 QAccessibleWidget 动作接口，只响应 SetFocus，不响应 Press。原 Invoke 路径现已在聚焦/输入/提交之前关闭。W2-B / V0 仍未通过，既有人工确认和发送阻断均保留。**

本轮仅做静态分析、限定范围的原生/UIA 读取和两个既定分片的内存快照；没有聚焦、输入、提交、清理草稿、解除阻断或调用模型。

## 输入状态：两个读取接口一致

新增 `probe_wechat_post_submit.py --mode ui`，使用原生会话/账号检查，再比较同一输入框的 ValuePattern 与 TextPattern.DocumentRange。前后再次核对账号、控件和内容哈希；读取有界，证据只保留长度、空值和哈希，不输出正文。

实机两个接口均返回 **24 字符**，与既有 W2B 固定标记哈希完全一致；按钮仍启用。因此没有依据把本次残留单独解释为 ValuePattern 缓存差异，也没有依据宣称输入已清空。见 [双模式结果](evidence/w2b6-input-patterns-2026-11-02.json)。本轮没有修改该内容。

UIA 子进程增加 `-ReadOnly`，Python 传输层与 PowerShell 命令入口同时拒绝聚焦、输入和提交。新增诊断命令不会产生可用于输入的 WorkerSession 初始状态。

## 数据库：排除两项具体假设

只重新认证和打开原来的 `message_0.db`、`message_1.db`，从发送操作已经持久化的基线读取 `filehelper`。发件账本使用 SQLite `mode=ro`，不迁移、不追加证据或改变投递状态。

两个分片仍为 0 条新增回显。当前账号的消息目录元数据中也只有这两个消息分片，没有新增分片候选；未打开其他分片正文。见 [限定回显与文件元数据](evidence/w2b6-selected-echo-2026-11-02.json)。

进一步只查询同一会话的本地编号/排序元数据：最大 local_id 仍分别为 **312、143**，没有本地编号已增长但排序水位尚未增长的行，因而本次缺失不是该水位过滤条件漏掉新 local_id。快照完成页认证，当前有效/已提交 WAL 帧数均为 0。此结论仅适用于这些快照，不宣称排序序号永远不会回退，也不把没有回显推导成对端绝未收到。见 [水位审计](evidence/w2b6-cursor-audit-2026-11-02.json)。

## 按钮：运行时接口与磁盘代码对应

在固定 DLL 的 Qt 元数据中，发送按钮类继承链为：

`mmui::XOutlineButton → mmui::XSkiaWidget → QWidget → QObject`

它不继承 QAbstractButton，不能套用普通 QPushButton 的提交假设。新增 `inspect_wechat_button.py` 可复现该静态继承链，且先核对整个 DLL 指纹。见 [静态继承](evidence/w2b6-button-static-lineage-2026-11-02.json)。

随后从已核实页面的按钮 RuntimeId 有界映射到原生对象和接口，不自行调用这些动作方法。最终读取 **689 字节**并回读稳定，只输出模块相对位置与代码哈希，没有保存指针或 RuntimeId。见 [最终接口定位](evidence/w2b6-button-dispatch-final-2026-11-02.json)。

静态复现将这些运行时字节逐项与磁盘 DLL 比较，验证：

- `interface_cast(ActionInterface)` 返回对象偏移 `+16`，与实际观察的次级 vtable 一致。
- Qt 5.15 的动作接口布局中，`doAction` 位于该表 `+32`，实际目标 RVA 为 `0x9EA590`。
- 该函数比较传入动作与 `SetFocus`；不相等时直接返回。相等分支只处理窗口激活与聚焦，没有按下/点击分支。
- 对应字符串访问器和静态 QString 常量已核对，`Press` 与 `SetFocus` 是不同动作。

Qt 5.15 的 UIA Invoke 参考实现将调用转发为 Press。结合本机已观察的 focus-only 接口，原方案不能把 Invoke 返回成功当作可用的真实提交出口。这里没有执行该函数来验证，也没有改用回车或坐标点击作补发。见 [可复现的字节/分派核对](evidence/w2b6-focus-only-dispatch-2026-11-02.json)。

参考依据为 Qt 官方 [动作接口布局](https://github.com/qt/qtbase/blob/5.15/src/gui/accessible/qaccessible.h)、[UIA Invoke 实现](https://github.com/qt/qtbase/blob/5.15/src/plugins/platforms/windows/uiautomation/qwindowsuiainvokeprovider.cpp)、[通用 QWidget 动作实现](https://github.com/qt/qtbase/blob/5.15/src/widgets/accessible/qaccessiblewidget.cpp)。参考分支不等于微信源码；本机接口类型与具体分支由上述版本锁定字节核对提供，没有复制 Qt 源码或新增运行依赖。

## 已修改的行为

`NativeSendBackend` 现在在聚焦、输入及提交之前要求已验证的提交路径：本次 focus-only 实现报 `native_button_press_not_implemented`，其他未知实现报 `native_button_submission_path_unverified`。目前没有新的真实提交方式放行。只读诊断继续可用，普通工作台仍使用原有模拟后端。

同时补充提交后的有界只读检查，供后续经验证的路径使用：同一控件必须在最多 0.6 秒的观察期内由 ValuePattern 和 TextPattern 一致确认空输入，才接受该次 UI 操作的后置状态；否则记录明确未知原因。此观察不会再 Invoke，不会清草稿，也不将空输入当作送达确认。没有用新代码再发送消息验收；当前真实路径在前置检查中已经被拒绝。

## 历史确认与收尾

W2-B5 的手机人工反馈作为 manual_confirmation 保留，outbox 仍为 CONFIRMED，原 attempt 的不确定状态仍保留。这份反馈不能替代本轮发现缺陷的原生提交链路验证。没有撤销用户反馈，也没有将不存在的数据库回显补成 local_echo。

专用账本仍为 **3 个历史尝试、1 次输入许可、1 次提交许可、1 条人工确认**，本轮新增发送尝试及账本写入均为 0。通道继续为 `submission_or_draft_requires_review`，没有自动恢复。见 [本轮汇总](evidence/w2b6-result-2026-11-02.json)。

新增场景覆盖匿名双模式投影、原生前后核对和中途修改、只读传输拒绝动作、目录元数据上限、水位回退审计、调用后草稿未空不二次提交、延迟清空的只读观察，以及已知无效/未知按钮在输入前被拒绝。新增及受影响测试最终 **72 passed**，Ruff 和 PowerShell 语法检查通过。

最后独立复查开关为 0、外层 3 节点，当前账号仍与 W2-A 相同；见 [恢复复查](evidence/w2b6-final-restored-2026-11-02.json)和 [账号复查](evidence/w2b6-account-final-2026-11-02.json)。本轮无新消息、草稿变更、收藏修改、模型请求、密钥落盘、明文数据库落盘或新依赖。

下一项 W2-B7：为自绘按钮设计并离线验证可定向、可单次执行的真实提交方式，保持规范身份/窗口/草稿/焦点/持久许可约束；在正确路径和草稿处理条件具备之前，不启用真实发送，不复用原来已确认的动作补发。

后续更新：W2-B7 候选逻辑及隐藏自有窗口消息/超时实验已通过，并修正 Windows venv 的直接工作进程启动；没有接入微信或解除阻断。下一项转为只读几何、命中和取消验证，详见 [W2-B7](W2B7_DIRECTED_CLICK_CANDIDATE.md)。
