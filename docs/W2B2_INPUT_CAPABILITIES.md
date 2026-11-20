# W2-B2：真实输入能力与身份取证

2026-11-02。**已完成有界原生能力取证：输入框及发送按钮可定位，当前所查 UIA 字段没有建立规范会话身份。真实 UI 后端和单次发送尚未验收，W2-B / V0 未通过。**

## 代码与验证

新增 `scripts/probe_wechat_input.py`、`probe_wechat_input.ps1`、`wechat_input_metadata.ps1`。这是项目原生能力测试工具，不接工作台默认启动，不具备点击、聚焦、输入或提交功能。

Python 父进程复用固定版本/窗口指纹预检、同句柄启用恢复租约和 kill-on-close Job。原生读取在独立 PowerShell 子进程内，最长 25 秒，退出回收另限 2 秒；未证实退出时不恢复提供方租约，报告异常并停止。恢复日志先持久写入，恢复后回读。

树上限 32 层 / 800 节点。只保存控件类、角色、接口能力、限定相等比较、哈希和形态计数。草稿只在内存读取并转成空值状态、长度及 SHA-256，不写入正文。未保存原始 AutomationId、Name、HelpText、ItemStatus、Value、RuntimeId、联系人名称或聊天正文。

新增 3 个综合离线场景，覆盖实际 PowerShell 投影与语法、显示名称和规范标识提示不授予发送权限、错误窗口/截断/异常、超时回收及无法确认退出。加 1 个既有启用恢复回归，最终 **4 passed**，Ruff 通过。

## 真实结果

目标仍为 PID 34044、HWND 68894、创建 FILETIME 134380654003431728，固定 EXE/DLL 指纹匹配。这里核对的是进程/窗口，不把它当成当前账号事实。

1. 未启用时仍只有外层 3 节点。见 [只读基线](evidence/w2b2-input-readonly-2026-11-02.json)。
2. 临时启用后 147 节点，无截断和采集异常。唯一 `mmui::ChatInputField` 暴露 ValuePattern、TextPattern，ValuePattern.IsReadOnly=false；本次读到空草稿及正确的空字符串哈希，未聚焦。发送按钮候选为 `mmui::XOutlineButton`，暴露 InvokePattern，空草稿时处于禁用状态。接口暴露不等于实际写入或发送成功。见 [基础能力](evidence/w2b2-input-activated-2026-11-02.json)。
3. 输入框显示名称为文件传输助手，选中会话行也含该标签。但输入框、限定页面容器及选中行的 AutomationId、HelpText、ItemStatus、ItemType、Name/Value 相等检查均没有给出规范 `filehelper`；因此没有建立原生 username 到当前输入框的证据链。见 [扩展属性](evidence/w2b2-input-properties-2026-11-02.json)。
4. 按“聊天信息/详细信息/查看资料/聊天详情”等已知静态标签查找资料入口，没有匹配候选。这只说明该组已查标签没有命中，不说明客户端不存在资料入口；本次没有猜测并调用其他按钮。见 [资料入口候选](evidence/w2b2-profile-candidates-2026-11-02.json)。
5. 检查当前消息控件的 AutomationId 形态，未得到可供平台消息 ID 对照的明确数字型标识。没有读取或导出消息正文，也没有把 RuntimeId 充作平台消息 ID。见 [消息身份形态](evidence/w2b2-message-identity-shapes-2026-11-02.json)。

上述 4 次临时启用均为 0 → 1 → 0 并回读通过，各次采集进程已退出。最后由独立只读运行确认原值 0、外层 3 节点。见 [最终恢复复查](evidence/w2b2-final-restored-2026-11-02.json)及各记录同名 journal。

本轮微信消息发送、UI 输入动作、真实模型调用和收藏修改均为 0；没有读取密钥或解密数据库，也没有迁移用户账本。汇总见 [取证结果](evidence/w2b2-result-2026-11-02.json)。

## 后续路线

当前继续保持 `native_send_enabled=false`。单凭输入框显示名称，不满足 [操作范围](WECHAT_OPERATION_SCOPE.md)的“文件传输助手必须由已核实的目标身份绑定，不能只凭相似显示名称发送”。没有为完成演示降低原生身份检查。

可继续调查新的只读原生来源，将账号、当前会话规范 username 和 UI 页面关联起来；已检查的字段不再无依据重复扫描。找到具体来源后再接定向 UI 后端、限定内存快照基线及回显，进行一次固定标记验收。

另一条需要用户选择的路线是**人工核实当前内置文件传输助手的单次 PoC**：身份依据明确记录为人工核验，固定本次进程/窗口/输入控件与唯一标记，许可短期有效且只能提交一次，禁止自动导航和重试；不得冒充原生 username 验证，也不能用于联系人、群或无人值守模式。该路线当前只是提案，没有实现或开启，也没有从既有“允许向文件传输助手发送”推断用户已经接受这种身份核验方式。

UIA 语义依据：[AutomationId 的作用范围](https://learn.microsoft.com/en-us/dotnet/framework/ui-automation/using-ui-automation-for-automated-testing)、[控件 Pattern](https://learn.microsoft.com/en-us/dotnet/framework/ui-automation/ui-automation-control-patterns-overview)、[TextPattern 的只读接口](https://learn.microsoft.com/en-us/dotnet/framework/ui-automation/ui-automation-textpattern-overview)。这些是接口资料，本机能力和限制以本轮证据为准。

后续按既有原生身份路线继续，未启用人工替代模式。W2-B3 已完成无密钥账号复核和选中行原生对象映射，业务会话键仍待查明。见 [W2-B3](W2B3_NATIVE_IDENTITY.md)。
