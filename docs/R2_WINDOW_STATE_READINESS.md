# R2 最小化／托盘只读调查

2026-11-16。R2窗口状态调查已完成，不改变原发送范围。最小化下本次只读接收通过；托盘下当前可见窗口合同拒绝。用户手动恢复独立窗口后，托盘期间的原TRAY文本从保留游标接续，只落账一次、重启无重复。没有实现托盘读取或最小化/托盘发送的新支持。

## 状态结论

| 状态 | 只读接收 | 输入／发送准备 |
| --- | --- | --- |
| 非最小化、被其他应用遮挡的独立filehelper | E7/R1已验收 | 仍逐条具体审核，本轮零发送 |
| 独立filehelper最小化且HWND仍存在 | MIN接收、HTTP与读取器重启通过 | 明确standalone_window_minimized拒绝 |
| 本次收起托盘，独立HWND消失且主窗隐藏 | 当前合同拒绝，未接收TRAY | 无合格可见窗口，不放行 |
| 用户手动恢复后的新独立HWND | 从原游标补读原TRAY一次，重启fresh=0 | 本轮只读，不创建发送许可 |

## 最小化现场结果

用户明确回复已最小化并发送 `R2-20261116-MIN`。只读枚举确认原PID7872、创建代际134392115523127861、独立窗口31721126仍存在、visible=true、minimized=true；未程序恢复/显示/激活。

同一原账号及窗口绑定的只读读取器46856接收新文本，选定来源 `6832b4de441d40cf9bc51a7176713483d8800c3c83a10b9d0161c4e602c93d2f` 仅落账一条，self_echo语义保持。全新读取器26260从同一游标恢复、再次fresh=0，HTTP展示正文哈希一致；模型和发送器均未装配。读取进程均退出，完整性ok，操作/许可仍17/15/11、旧UNKNOWN8、活动操作0。

随后同一最小化窗口的现有只读准备探针在原生preflight后以 `standalone_window_minimized` 拒绝，未创建租约或执行输入/提交，gate_after=0。结论仅是“最小化下本次只读接收/HTTP/重启通过；输入和发送准备仍拒绝”，不是最小化后台发送验收。读取辅助工具保留原E7通用状态名 `e7_receive_restart_http_verified`，其实际用途以本段状态/来源/参数为准。

证据：[最小化接收/重启/HTTP](evidence/r2-minimized-receive-2026-11-16.json)、[最小化准备拒绝](evidence/r2-minimized-preparation-2026-11-16.json)、[独立账本审计](evidence/r2-minimized-audit-2026-11-16.json)。

## 托盘现场及恢复

用户回复已收起托盘、已从手机发送 `R2-20261116-TRAY` 后，只读枚举取得原PID7872和创建代际未变，原独立HWND31721126已不存在，主窗329642存在但visible=false。未程序显示、激活、恢复窗口或改绑其他目标。

按原独立窗口绑定运行现有读取器，初始化返回reader_source_failed并回收；没有登记新来源、模型或动作。独立只读NativeMemory前置检查对旧独立窗口与隐藏主窗分别复现固定诊断“selected window is not visible in target process”，与当前只接受可见窗口的合同一致。托盘阶段没有放宽preflight或继续读取密钥；原发送历史、八条UNKNOWN、17/15/11不变，活动操作/绑定0。此时没有合格窗口重新核验gate，证据明确保留“托盘时未重新读取gate”；该轮没有进程写入，先前最小化准备的gate结果为0。

用户手动恢复独立filehelper并遮挡、明确不重发TRAY。新独立HWND为14617104，原PID/创建代际保持；再次核对原账号、规范filehelper、双模式空输入、遮挡准备及gate恢复0通过。仅在新窗口取得资格后启动只读读取器47428，从先前MIN已提交游标（local_id182）接续，读到原TRAY来源 `a5cb5d1e7bdcfd48a269e810df7a6e0e011e2e71067e826cc17f14ecdff2abfa` 一条。新读取器11976恢复同一游标、fresh=0，HTTP正文哈希核对通过。该结果是“恢复可见窗口后读取保留消息”，不能记作托盘内读取成功。

最终独立只读审计核对该TRAY全账本仅一行、零新草稿/模型trace，原六张发送表的历史行及八条UNKNOWN身份列表不变，E7动作仍CONFIRMED，累计17次操作/许可15/11，活动操作/绑定0、进程回收、完整性ok；在恢复后的合格新窗口再次独立读得gate0。现有正式源码未为此次调查修改，沿用R1收尾全量812项及848子场景通过、9项预期跳过；本轮新增辅助审计仅做语法和实际只读验证。

证据：[托盘原绑定拒绝](evidence/r2-tray-bound-receive-2026-11-16.json)、[托盘窗口／前置条件独立审计](evidence/r2-tray-audit-2026-11-16.json)、[恢复窗口身份及准备](evidence/r2-restored-filehelper-readiness-2026-11-16.json)、[原TRAY补读／重启／HTTP](evidence/r2-restored-tray-marker-receive-2026-11-16.json)、[最终独立审计](evidence/r2-window-state-final-audit-2026-11-16.json)。后续新进程/窗口不得照抄当前PID/HWND；应重新取证。R3主人目标范围仍需另行明确，不借本次只读范围扩大联系人或群。

## 当前已知边界

工作台快照明确声明 `capabilities.minimized=false`。`scripts/wechat_standalone_backend.py` 的 `StandaloneCapabilities.guard` 在最小化时返回 `standalone_window_minimized`，并在原生窗口归属/线程/类过程核验中拒绝不可见窗口。`scripts/wechat_background_native.py` 的原生能力guard同样拒绝不可见或最小化窗口。没有依据把现有BG1称作BG2。

既有背景原生测试覆盖visible=false与iconic=true的拒绝；这些是合成边界证据，不能替代真实托盘或最小化状态中的接收能力。R1保持窗口非最小化，不在连续窗口中制造R2状态变化。

## R1之后的独立只读步骤

1. 记录原账号、固定程序与独立filehelper窗口代际、当前可见/最小化状态、原gate和账本基线。
2. 最小化与托盘分别由明确操作进入；不相互推断，不自动恢复/显示/激活。每个状态独立枚举HWND存在性、可见和最小化标志。
3. 仅检查新读取器能否从原游标接收新手机标记，并显示于只读工作台；零生成、零输入/提交。最小化时可读取不表示可以发送。
4. 若调查输入准备，只能使用现有只读能力检查，保留拒绝码及gate恢复/子进程退出结果；不得为“通过”而删除现有非最小化条件，也不发送测试文本。
5. 每个状态结束独立核对原发送表和模型计数未变；没有新消息或身份不可核验时明确记录未验收，不沿用旧BG1/E7结果。

如需新增“恢复可见后后台操作”，另行设计显式模式与前台事件观察，不把恢复窗口行为隐藏在发送准备中。
