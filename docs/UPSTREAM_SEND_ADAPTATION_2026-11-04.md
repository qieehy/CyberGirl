# 发送层改为基于最新上游实现适配

2026-11-04。按用户提出的“先阅读相关项目源码，直接在他们的基础上修改”重新核对上游。**建议采纳：下一步以 whale_gril 的独立后台发送模块为基础，先做最小范围的提交序列适配；保留本项目已经验收的读取器、账号/规范会话校验、主账本和工作台。** 本轮只下载、阅读和比较源码，没有运行上游模块、安装依赖、操作微信或再次发送。

## 新发现

原来的评估固定于 2026-10-30 的 whale_gril 提交 `e14a8c2…`。本次核对 main 后固定新提交 `0af4dcd5596192e0fa4d3938ea93b404771d6e02`，依赖 wechatauto-replica 固定 `bc2e6f2461330eb1a53996d2f2afc18416376b52`。这是分别取得的源码版本，不冒充 whale_gril 实际安装的 wheel 或精确依赖锁定。

最新版 `examples/wechatauto_bridge.py` 已是兼容入口，实际实现迁到 `bridge/engine.py`，并将独立的 `bridge/bg_sender.py` 作为优先后台发送通道。旧评估中“没有找到 4.2.0.9 运行证据”的结论只适用于旧快照；最新版后台发送文档已有作者标注的 **4.2.0.9 实测记录**。这是上游报告，不能替代本机验收。

| 项目 | 上游源码 | 本项目失败试验 |
| --- | --- | --- |
| 窗口消息交付 | `PostMessageW`，加入目标线程消息队列 | `SendMessageTimeoutW`，有界同步调用 |
| 点击序列 | `WM_MOUSEMOVE → WM_LBUTTONDOWN → 等待（默认 70ms）→ WM_LBUTTONUP` | `WM_LBUTTONDOWN → 立即复核按下位 → WM_LBUTTONUP`；本次复核异常，未到 UP |
| 鼠标进入状态 | 显式前置 MOVE，作者说明其用于更新 Qt 内部鼠标进入状态 | 没有前置 MOVE，物理几何/命中检查不证明这个内部状态已更新 |
| 结果 | 原代码主要检查输入框清空，部分未知读取也返回 sent_unverified | 必须独立新回显和手机确认；本次仍 UNKNOWN/UNCONFIRMED |

**缺少前置 MOVE、消息排队/处理时机不同，是本次失败的具体候选解释，尚未证实根因。** 原试验的通用错误码不能区分 DOWN 前的最终检查与 DOWN 返回后的按下位检查，不能只凭源码差异宣布已修复。

精确来源：[上游点击序列](https://github.com/Banan1cee/whale_gril/blob/0af4dcd5596192e0fa4d3938ea93b404771d6e02/bridge/bg_sender.py#L421-L446)、[后台发送文档](https://github.com/Banan1cee/whale_gril/blob/0af4dcd5596192e0fa4d3938ea93b404771d6e02/docs/%E5%BE%AE%E4%BF%A1%E6%B6%88%E6%81%AF%E5%90%8E%E5%8F%B0%E5%8F%91%E9%80%81.md)、[桥接入口](https://github.com/Banan1cee/whale_gril/blob/0af4dcd5596192e0fa4d3938ea93b404771d6e02/bridge/engine.py#L3336-L3370)。

## 直接基于上游修改的范围

第一步只提取/改写 `WeChatSender.click_screen` 的消息序列及坐标转换部分，保留来源、版权和 MIT 许可；做成可替换的提交后端，由现有父进程账本控制。已有正式接收器、真实账号/filehelper 业务身份和草稿保护不重做。先以假窗口/注入的消息接收器验证，不接通真实微信入口。

必须一并处理 MOVE 的写前意图、同一 HWND/线程/坐标、排队结果和实际处理的区别、有界观察和 UNKNOWN。不能简单增加一条 MOVE 后就把现有按下位检查删除。`PostMessageW` 返回成功只证明请求入队，结束发送子进程也不撤销目标队列中的消息。

不直接调用上游整套 `send()`：它还包含系统读屏设置、自动热启用、切会话、伪焦点、逐字符写入和编码实测/清框。这些与当前最小提交适配无关；其中编码探测会写入测试字再清理，不能用于当前保留的草稿。姓名匹配也不能替代本项目原生 filehelper 业务键。

前台 UIA 文本路线也已复读。依赖的 `send_text()` 使用点击、剪贴板、Ctrl+V 和 Enter，包含清框及相似度确认；桥接引擎有通道回退。若以后采用，必须移除清草稿、弱文本确认、异常后重试及多通道补发，另行说明全局输入的约束变化。本次优先选择不引入全局键鼠的后台点击序列。

## 当前出口

原单次新标记许可已消费，手机未收到、电脑草稿保留，旧尝试仍未对账。上游新增路径不会改变这些事实，也不会自动解除原账本阻断。本轮不创建复核/action，不发 MOVE/DOWN/UP/字符或任何其他微信消息。

下一实现顺序调整为：固定上游来源与差异（本轮完成）→ 最小点击序列适配及离线验证 → 明确旧 UNKNOWN/草稿的人工对账合同 → 准备可审核的新现场方案。先暂停继续扩展自研窗口过程逆向；只有上游适配确有无法解释的缺口时再补针对性取证。D / V0 仍未通过。

用户随后同意，已完成第二步的组件及持久协议离线适配，并明确旧尝试/草稿对账合同；388 项测试及 848 个子场景通过。尚未接线真实微信的排队发射器，没有新增发送。见 [W2-B13 实现与边界](W2B13_UPSTREAM_POSTED_ADAPTER.md)。

源码下载保存为 `.source.txt`，没有 import 或运行。清单和内容摘要见 [whale_gril](../research/wechat_bridge/2026-11-04/upstream-send-review/whale_gril/manifest.json)、[依赖](../research/wechat_bridge/2026-11-04/upstream-send-review/wechatauto-replica/manifest.json)。`examples/sticker_transport.py` 在最新路径返回 404，清单明确记录失败；旧版该文件仅作历史参考，不冒充本次新源码。
