# W1：微信只读窗口探测

更新日期：2026-10-29（Asia/Shanghai）。状态：**登录后探测和独立原始 UIA 诊断均已执行，聊天控件暴露不足，W1 出口未通过**。用户已确认完成登录，不再等待登录。最新结论见 [W1-D 原始诊断](W1D_RAW_UIA_DIAGNOSIS.md)。

此前 computer-use 原生管道连接失败，未取得微信控件证据。本次新会话中的窗口枚举已经恢复。首次未发现微信进程或目标窗口；通过工具打开既有 `C:\Program Files\Tencent\Weixin\Weixin.exe` 后，取得唯一微信窗口及可访问性文本快照。

登录前快照有 36 个非空行，检测到登录提示。仅输出类型关键词计数与登录标志，没有截图，没有把原始控件树、昵称、消息正文或输入框值保存到项目。工具接口会在 JS 内存中取得可访问性文本，再形成摘要；这不应描述成“采集器完全不接触文本”。登录前的类型关键词计数不是完整 UIA 控件统计。

当前证据支持“桌面工具可连接，微信窗口可访问”。登录由用户确认；聊天列表、编辑控件、UIA 模式、稳定账号/会话身份及收发能力未通过验证，因此不将 W1 或 M1-A 标为通过。两轮应用状态操作为启动客户端及登录后置前一次；未点击、输入、代登录、切换会话或发送消息。

## 登录后的实际观察

重新枚举得到唯一微信窗口，句柄从登录前的 788988 变为 460506。分别在置前之前和之后采集一次无截图的可访问性树，均只有 4 行：一行窗口描述和 3 个控件行。按本地化控件行前缀解析，得到 1 个窗口、2 个窗格，未取得按钮、列表、编辑控件，也未发现搜索、通讯录、发送按钮及候选消息/输入类标志。

微信主进程 30376 的主窗口句柄与工具一致，进程响应正常。置前后结果不变，因此不能简单归因于窗口未在前台。当前 API 不提供完整 UIA Pattern 或原始 ClassName 查询，无法据此确定是微信可访问性提供方、工具遍历范围、权限差异还是版本变化导致；也不能把负面结果扩大为“所有 UIA 库均不兼容 4.2”。

追加静态读取固定提交的 [WeChatTools.py](https://github.com/Hello-Mr-Crab/pywechat/blob/8589baa049bb91d3a500602c167f07b2f8397a13/src/pyweixin/WeChatTools.py) 与 `pyweixin/__init__.py`：连接采用 pywinauto 的 UIA backend；`open_weixin` / `connect_weixin` 依赖 `mmui::MainWindow`，已检查入口没有暴露缺失控件树的初始化步骤。其 `get_current_wxid` 从数据文件夹路径提取，源码另提示多账号缓存问题，因此不能将它直接用作实时登录身份的强证据。只保存源码文本，没有安装或执行候选程序。

## 下一步

W1-D 已完成：正常桌面用户的独立 Raw View 与 Control View 均只有 3 个节点，无异常/截断，FrameworkId 为 Win32。已排除“仅是快照工具过滤”的解释。下一项为 W1-E 控件提供方初始化路径评估，先固定候选源码和核验 4.2.0.9 适用性，详见 [原始诊断与后续边界](W1D_RAW_UIA_DIAGNOSIS.md)。

取得聊天控件证据后再进入 W2 固定会话无 LLM 收发；实际收发仍需明确测试会话及内容。若最终只有图像/坐标路线可用，应作为独立的人工辅助模式评估，不能直接接入稳定身份的自主群适配器。现阶段不需要再次登录、降级客户端或重复 H4a 验收。

当前 [computer-use 技能](C:/Users/15002/.codex/plugins/cache/openai-bundled/computer-use/26.1023.50603/skills/computer-use/SKILL.md) 引用的 [guidance.md](C:/Users/15002/.codex/plugins/cache/openai-bundled/computer-use/26.1023.50603/docs/guidance.md) 明确规定：“Do not automate user authentication dialogs.” 因此登录交给用户完成，不代点确认或扫码。

证据：[登录前摘要](evidence/w1-ui-probe-2026-10-29.json)、[登录后探测](evidence/w1-logged-in-2026-10-29.json)、[登录前进程与版本](evidence/w1-processes-2026-10-29.json)、[追加源码清单](../research/wechat_bridge/2026-10-29/manifest.json)及[初始化源码清单](../research/wechat_bridge/2026-10-29/init-manifest.json)。本轮无产品代码修改，不重复运行 S1–S4 或真实模型测试。
