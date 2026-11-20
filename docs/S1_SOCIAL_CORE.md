# S1：平台无关消息闭环

日期：2026-10-27。实现执行计划 §5.1 的首个离线切片：合成主人私聊 → policy/inbox → 自有 AI_agent Runtime + Mock LLM → decision/outbox → FakeAdapter → 确认与重启恢复。

## 运行

在项目根目录执行：

```powershell
& '.\.venv\Scripts\wechat-agent.exe' social-demo
```

默认数据库为 `.state/social/sandbox.sqlite3`，可以用 `--database` 指定新的沙盒文件。每次演示创建一组新的合成事件，并在同一组内重复提交相同事件，验证不会重复调用或投递。已有数据继续保留；不会自动删除旧库。需要全新演示时使用新的数据库文件名。

当前演示结果：普通回复 `CONFIRMED`，重复入站保持同一动作，沉默 `SILENT` 且无 outbox；关闭再打开数据库后仍为 `CONFIRMED`。两次合成模型调用（回复与沉默各一次），一次 FakeAdapter 投递。真实模型请求与真实消息发送均为 0。

这不是微信接入或群聊验收。CLI 不提供真实 adapter、真实订阅或真实身份的启动选项。

## 实现合同

| 模块 | 已实现内容 |
| --- | --- |
| `social/models.py` | 稳定 scope/sender/message ID、时间和来源字段；来源默认不可信；严格 SILENT/REPLY，拒绝模型传入收件人或多余字段 |
| `social/ledger.py` | SQLite schema 1 / application ID；单进程 Windows 所有权锁；inbox 去重、决策与 outbox 同事务、发送尝试先落盘 |
| `social/service.py` | 最多 4 个进行中/排队任务；决策串行；120 秒回合时限、10 秒 adapter 时限；停止时取消并等待所持任务 |
| `social/decision.py` | 使用既有 ChatEngine 和实际 AI_agent Runtime；工具注册为空，无额外 Agent 循环；只从账本装入当前 scope 已确认的往返 |
| `social/sandbox.py` | 无网络的确定性模型、FakeAdapter 和可执行演示 |

入站身份键包含 platform、account、conversation type、conversation ID 和原始 message ID。同 ID 的不同内容视为冲突；后续接收时间变化不会绕过重复识别。未知 adapter 账号、未配置会话、非主人、来源不明、自身回声和过期事件不调用模型、不创建 outbox。首个切片只支持配置过主人的私聊，群和主动私聊动作尚未开放。

权限由调用方的受信配置 API 写入。模型只有两种建议：`SILENT` 与 `REPLY`，目标始终从入站 scope 派生。推理前、提交决策及提交发送前都核对策略与上下文版本；暂停、撤销、重新授权或新消息使旧候选失效。不会通过“撤销后再恢复”复活旧草稿。

草稿在接收事件 120 秒后失效。生成完成前的候选只在内存中，决策通过检查后直接原子提交为 QUEUED；本切片没有独立的持久 DRAFT 编辑流程。

## 投递与恢复

`QUEUED → SENDING → CONFIRMED / UNCONFIRMED / FAILED`。发送前不再符合策略时进入 CANCELLED，超过 TTL 时进入 EXPIRED。发送尝试在调用 adapter 前持久提交，随后才执行外部动作。

- CONFIRMED 要求回执绑定同一 action、完整 scope 和非空 receipt ID。本次仅证明 FakeAdapter 的确认语义；真实桥回执可信度和接收端证据仍需独立验证。
- 返回已知未提交时记 FAILED；超时、取消、异常、错配回执或提交结果未知均记 UNCONFIRMED。
- 进程崩溃后，SENDING 恢复为 UNCONFIRMED；未完成的入站决策取消；不会重放推理或自动重发。仍有效的 QUEUED 可由受信调用方显式 dispatch，再次校验策略、上下文和 TTL。
- 撤销阻止之后的提交；已经开始提交的消息不能撤回。即使提交期间撤销，后来确认送达也应记录为已发生的事实。
- 仅 CONFIRMED 往返进入后续模型上下文。未发草稿、失败、沉默或未确认发送都不会冒充已说过的话。

不宣称端到端 exactly-once。当前也没有人工对账入口、自动查询回执或有限重试；UNCONFIRMED 保留待后续实现处理。

## 验证

最终 5 个关键场景通过，耗时 3.00 秒：

1. 并发重复入站与重复投递只产生一个动作和发送尝试；重开数据库不重复调用，已确认历史可恢复。
2. 未知身份、来源及自身回声在推理前拒绝；SILENT 无 outbox；撤销、暂停/恢复及新上下文阻断旧草稿。
3. 两账号及不同私聊不串上下文；模型不能改目标；注入 outbox 写入失败时，决策与 outbox 一起回滚。
4. 子进程模拟外部提交后 `os._exit(37)`，真实进程退出释放数据库所有权；重开得到 UNCONFIRMED，零重发、零重复推理。
5. 过期草稿不提交；错配回执不确认；已知未提交为 FAILED；取消发送后保留 UNCONFIRMED。

首轮同一组场景通过后补齐来源持久字段和数据库身份检查，才复验受影响的该组；未重跑 H1–H4 或其他无变化场景。Ruff 通过，安装后的 `social-demo` 命令已实际运行。证据见 [S1 记录](evidence/s1-result.json)。

## 范围与下一步

这是 F-02/03/04/05/08/09 的一个可运行子集，不表示这些任务全部完成。数据库只用于合成沙盒，保留最多 10000 条入站，满额后停止新增。历史装配最多 16 对、10000 字符；原始消息与回复持久化在业务账本中，不写入 CLI 诊断输出。

尚未实现真实数据的保留/删除流程、日预算与冷却、群消息缓冲、定时主动任务、完整许可管理、人工对账、人格版本管理和 UI。后续先补人格版本及回放/管理界面，再逐步接入其余社交策略；真实微信和群回复仍须独立 PoC。H4b 真实刷新继续在自然生命周期中观察。
