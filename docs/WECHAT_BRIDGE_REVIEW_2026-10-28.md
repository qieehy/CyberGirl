# 微信桥接兼容性核验与下一步计划

核验日期：2026-10-28。范围：本机安装元数据、上游版本与选定源码静态审查。未安装或运行候选桥、修改微信、读取聊天正文、调用模型或发送消息。本次完成 E-01 的环境与候选调查，**M1-A 真实收发仍未通过**。

## 1. 结论

本机为 **Weixin 4.2.0.9 x64**，启动器和核心 DLL 版本一致，签名有效。当前核验的三个版本绑定桥没有覆盖该版本的证据，不能直接照旧版教程加载。UI Automation 路线可继续探测；优先参考 `Hello-Mr-Crab/pywechat` 中的新模块 **pyweixin**，但其“4.1+”声明不能替代 4.2.0.9 实测。

先保持现有微信版本，做一次独立的只读控件能力探测。即便窗口可操作，稳定账号/会话/成员身份、消息去重和发送回执仍需单独核验。当前状态为“候选已筛选，真实兼容性未验证”，不将整个项目记为 BLOCKED。

## 2. 本机证据

| 项目 | 结果 |
| --- | --- |
| 系统 | Windows 11 家庭中文版，10.0.26200，64 位 |
| 启动器 | `C:\Program Files\Tencent\Weixin\Weixin.exe`，4.2.0.9，PE machine 0x8664 |
| 核心 | `C:\Program Files\Tencent\Weixin\4.2.0.9\Weixin.dll`，4.2.0.9，PE machine 0x8664 |
| 签名 | 两文件均 Valid，Tencent Technology (Shenzhen) Company Limited |
| 运行状态 | 采样时 5 个 Weixin 进程；未据此推断登录账号 |

完整文件大小、SHA-256、签名和架构见 [本机证据](evidence/wechat-environment-2026-10-28.json)。窗口可见性、当前登录身份和控件树尚未探测。

## 3. 候选比较

| 候选与固定源码 | 上游证据 | 对本机的判断 | 本次处置 |
| --- | --- | --- | --- |
| [aixed/WeChat-Hook](https://github.com/aixed/WeChat-Hook/tree/1003f3e48d99711d6cfe8d12813049ac832490d6) | 当前 README 针对 4.1.10.27 x64；version.dll 方式；HTTP 默认 0.0.0.0:30001 | 4.2.0.9 未覆盖；发送接口返回值也不构成投递确认 | 保留接口参考，不加载 DLL |
| [WeChatFerry](https://github.com/lich0821/WeChatFerry/tree/0f5c60a034fcac234cabd000b49c9200defa7f7d) | `WeChatFerry/spy/spy.h` 固定 SUPPORT_VERSION=3.9.12.51；仓库 LICENSE 为 MIT | 与本机版本不匹配 | 不作为当前直接接入项 |
| [wechaty/puppet-xp](https://github.com/wechaty/puppet-xp/tree/1ee1e7d314a29d91dc2260db380013809c006294) | README 版本表 2.1.1 对应微信 3.9.10.27；Apache-2.0 文本 | 与本机版本不匹配 | 仅参考适配器设计 |
| [wxauto4 官方文档](https://docs.wxauto.org/docs/install.html) | 免费版最新支持到 4.1.8.107；Plus 表列 4.1 并要求激活；[原仓库](https://github.com/cluic/wxauto4/tree/d0ae14d3e435807b4dead393af9d014529bbac78) README 为停止更新 | 两者均没有本机版本的实测证据 | 购买/激活不是当前前提；不据此转用 Plus |
| [FreeWisdom/wxauto-4.0](https://github.com/FreeWisdom/wxauto-4.0/tree/bd7c5233e79c0a185638a325bf6d30607244dfa8) | UI Automation，README 泛称 4.0；消息 ID 来自控件 runtimeid | 可参考结构，不能视为稳定微信消息 ID | 次选研究对象；不复制生产实现 |
| [Hello-Mr-Crab/pywechat](https://github.com/Hello-Mr-Crab/pywechat/tree/8589baa049bb91d3a500602c167f07b2f8397a13) | 新模块 pyweixin 的 README 声明 4.1+/4.1.6+；基于 pywinauto | 有继续探测价值；未找到精确 4.2.0.9 验收 | 优先参考控件定位与基础操作，不直接调用自动回复循环 |

这张表记录本次所见的公开版本范围，不能证明所有未列版本必然不可用。固定提交和资料哈希见 [采集清单](../research/wechat_bridge/2026-10-28/manifest.json)。

许可证据按原文保留：WeChat-Hook 完整树仅找到 `minhook/LICENSE.txt`，未找到项目自身的许可证；wxauto-4.0 树未找到 LICENSE/COPYING/NOTICE，GitHub 许可元数据均为空。pywechat 的 LICENSE 标题为 LGPL 2.1，但正文将通用 Library 替换为 pywechat library，GitHub 标记 Other/NOASSERTION，因此不自动归类为未经修改的标准 LGPL。上述是来源记录，不是要求个人使用申请商业资格；本次没有把候选代码并入产品或依赖。

## 4. 影响当前账本的源码发现

1. **UI 消息 ID 不足以持久去重。** wxauto-4.0 `wxauto4/msgs/base.py:162` 使用控件 runtimeid；其 hash 由控件宽高和正文构成。同样文字/布局可能碰撞，缩放/布局变化可能改变值，均缺少服务端消息身份。pyweixin 的自动回复示例也比较 `element_info.runtime_id`。不能把这些值直接塞入现有稳定 `message_id` 合同。
2. **发送操作返回不等于送达。** wxauto-4.0 `ui/chatbox.py:128–155` 粘贴并点击发送，输入框清空后返回 success；pyweixin `send_messages_to_friend` 返回 None。WeChat-Hook `src/SendTextMsg.cpp` 调用 SendText 后直接返回 ret=0，没有在该路由检查可关联的远端回执。这些信号最多表示本地操作结果，不能直接映射 CONFIRMED。
3. **群身份和目标路由仍有缺口。** pyweixin 发送参数使用好友/群备注，群 @ 使用昵称；监听方法文档要求提供群成员来解析发送人。虽有 `check_my_info` 的 wxid 字段，底层获取方式及本机表现尚未验证，不能据此推断稳定群 ID、成员 ID 或结构化 @/引用事件已经具备。
4. **不直接采用第三方自动回复/重试循环。** 本项目已经有队列、暂停、预算和 UNKNOWN 不补发语义。候选仅承担收发适配；模型调用和是否重试继续由本项目控制。UI 测试需显式禁止关闭微信、清空未提交草稿、批量回复等默认副作用。

对应源码已作为 `.source.txt` 静态材料保存；从未 import、构建或运行。资料下载有 4 个失败项（TLS EOF/握手超时），清单保留具体 URL；不把失败文件记为已审查。Hook 的版本依据在线 README，发送判断依据成功保存的固定源码；pyweixin 的底层 WeChatTools 未保存成功，相关身份能力保持未验证。

## 5. 后续切片与关键验收

| 顺序 | 工作 | 必要输出与通过条件 |
| --- | --- | --- |
| W1：只读 UI 探测 | 现有 4.2.0.9 上探测微信窗口及有限层级的 UIA 控件；参考 pyweixin 定位方式，先不安装整套 RPA | 记录微信 PID/架构、窗口类、控件类型/数量和可用模式；不落盘聊天正文/昵称/编辑框值；不点击、输入、切换会话或调用模型。不可访问时区分最小化、未登录、权限和控件变更，不能只报“不兼容” |
| W2：固定会话无 LLM 收发 PoC | 在确认可定位后准备单次文本发送与只读观察工具；实际发送前取得用户指定的测试会话及内容 | 优先文件传输助手或明确测试会话；一次唯一标记消息；记录操作已提交、本地回显、对端确认的不同证据。超时/身份不确定停止，不自动重试；不从名称相同推断同一会话 |
| W3：适配器合同验证 | 验证稳定账号、私聊/群目标、发送者、消息与引用标识，并核验切换会话/重开窗口后的身份 | 至少覆盖重复正文、同名对象、重开窗口后的重复观察，以及提交后失联。不满足稳定身份时，只交付显式人工确认的辅助模式，不接入自主群循环 |
| W4：真实群闭环 | W3 通过且测试群和触发范围明确后，将真实群事件接入现有 S4 策略 | 一组普通消息应零模型；一组明确 @ 应只产生一份候选；暂停/撤销阻止提交；未知投递不补发。模型调用和外部发送分别计数 |

**下一项是 W1。** 预计 W1 约半天，W2 约半至一天；W3/W4 取决于可取得的身份和回执能力，暂不承诺直接自动化工期。每个切片仅做上表对应的关键验收，不重跑 H4a 或既有 S1–S4 成功用例。

如果只能取得 UI 临时身份，可以评估独立的人工辅助会话模式：每次重新确认目标，进程重开即失效，不自动回放历史；需要另设会话级合同，不能假装它满足现有稳定微信适配器。这仍可帮助个人使用，但不是无人值守群机器人。

## 6. 当前项目状态

H4a 订阅文本、S1 消息闭环、S2 人格/UI、S3 单轮真实预演、S4 合成群已完成各自切片；本次不改变这些结果。H4b 真实刷新继续自然观察。新增完成的是版本与来源调查；微信真实收发、真实群自动回复、长期稳定性仍待各自证据。
