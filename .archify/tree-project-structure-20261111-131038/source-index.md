# 本地源码索引

当前目录没有 Git 元数据；以下为本轮读取的工作目录文件，未绑定或校验提交。

## WeChatAgent

本机社交应用 · Python ≥3.11

- pyproject.toml:5–18

## 应用入口与运行时

src/wechat_agent/ 顶层模块

- src/wechat_agent/cli.py:123–148

## cli.py / bootstrap.py

命令分发、mock/live 装配与回收

- src/wechat_agent/cli.py:153–182
- src/wechat_agent/bootstrap.py:64–80

## console.py / assets/console/

本机 HTTP 工作台 · 默认离线沙盒

- src/wechat_agent/console.py:16–21
- src/wechat_agent/console.py:221–239

## chat.py / framework.py

ChatEngine → 外部 AI_agent Runtime

- src/wechat_agent/chat.py:175–191
- src/wechat_agent/framework.py:48–63

## preview.py

显式真实人格预演 · 不投递微信

- src/wechat_agent/preview.py:1–23

## 社交核心 social/

平台无关消息、人格与投递状态

- src/wechat_agent/social/service.py:16–25

## service.py / decision.py

入站、决策、outbox 与适配器回执

- src/wechat_agent/social/service.py:47–105
- src/wechat_agent/social/decision.py:20–41

## ledger.py / models.py

SQLite 账本、事务、去重与恢复

- src/wechat_agent/social/ledger.py:46–74
- src/wechat_agent/social/service.py:80–105

## persona.py / groups.py

人格组装、群缓冲与练习策略

- src/wechat_agent/social/decision.py:9–25
- src/wechat_agent/console.py:221–229

## sandbox.py

SyntheticModel + FakeAdapter 离线闭环

- src/wechat_agent/console.py:224–235

## 模型接入 providers/codex/

文本模型调用与独立认证

- src/wechat_agent/bootstrap.py:64–80

## provider / transport / sse / response

有界文本请求、HTTP 流与结果校验

- src/wechat_agent/providers/codex/provider.py:96–125
- src/wechat_agent/providers/codex/transport.py:59–88

## auth / token_manager / credentials

认证、令牌管理与 DPAPI 凭据

- src/wechat_agent/cli.py:17–36
- src/wechat_agent/providers/codex/credentials.py:1–12

## catalog / config / network

模型目录、端点与显式本机代理

- src/wechat_agent/cli.py:39–50
- src/wechat_agent/providers/codex/network.py:14–39

## 微信桥接 bridges/wechat/

版本绑定 · filehelper 限定接入

- src/wechat_agent/bridges/wechat/background_sender.py:46–74
- docs/W2B20_BG1_ACCEPTANCE.md:1–5

## receive_* / reader / snapshot

读取子进程 → 父进程提交 → ACK

- src/wechat_agent/bridges/wechat/receive_process.py:135–163
- src/wechat_agent/bridges/wechat/reader.py:51–79

## contracts / store / send_state

身份、游标、发送许可与证据账本

- src/wechat_agent/bridges/wechat/store.py:1–5
- src/wechat_agent/bridges/wechat/store.py:15–32
- src/wechat_agent/bridges/wechat/background_sender.py:66–84

## native_* / qt_* / window_proc

UIA、Qt 身份与原生输入事件路线

- scripts/probe_wechat_background_service.py:14–47

## background_*

准入、观察、锁、意图日志与恢复

- src/wechat_agent/bridges/wechat/background_sender.py:73–97

## 执行脚本与工程资料

scripts/ · tests/ · docs/ · 构建配置

- pyproject.toml:20–41
- scripts/run_wechat_receiver.py:1–17

## scripts/

版本专用入口、探针、worker 与验收

- scripts/run_wechat_receiver.py:20–52
- scripts/probe_wechat_background_service.py:1–5
- docs/W2B19_WEIXIN_42013.md:1–3

## tests/

模型、账本、控制台与微信恢复测试

- tests/test_console.py:1–19
- tests/test_wechat_receive_only_soak.py:1–16

## docs/ 与依赖构建

阶段记录、能力边界、锁定与框架打包

- docs/W2B20_BG1_ACCEPTANCE.md:1–5
- scripts/build_framework.py:18–35
- pyproject.toml:1–18
