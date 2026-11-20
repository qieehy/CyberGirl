import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

p = Path('.archify') / ('tree-project-structure-' + datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d-%H%M%S'))
p.mkdir(parents=True, exist_ok=True)
b = 'src/wechat_agent/'
def ref(path, a, z):
    return dict(path=path, line=a, end_line=z)
nodes = []
def node(i, label, parent, sub, *sources):
    d = dict(id=i, label=label, sublabel=sub, sources=list(sources))
    if parent:
        d['parent'] = parent
    nodes.append(d)
node('root', 'WeChatAgent', None, '本机社交应用 · Python ≥3.11', ref('pyproject.toml',5,18))
node('app', '应用入口与运行时', 'root', 'src/wechat_agent/ 顶层模块', ref(b+'cli.py',123,148))
node('cli', 'cli.py / bootstrap.py', 'app', '命令分发、mock/live 装配与回收', ref(b+'cli.py',153,182), ref(b+'bootstrap.py',64,80))
node('console', 'console.py / assets/console/', 'app', '本机 HTTP 工作台 · 默认离线沙盒', ref(b+'console.py',16,21),ref(b+'console.py',221,239))
node('engine', 'chat.py / framework.py', 'app', 'ChatEngine → 外部 AI_agent Runtime', ref(b+'chat.py',175,191),ref(b+'framework.py',48,63))
node('preview', 'preview.py', 'app', '显式真实人格预演 · 不投递微信', ref(b+'preview.py',1,23))
node('social', '社交核心 social/', 'root', '平台无关消息、人格与投递状态',ref(b+'social/service.py',16,25))
node('service', 'service.py / decision.py', 'social', '入站、决策、outbox 与适配器回执',ref(b+'social/service.py',47,105),ref(b+'social/decision.py',20,41))
node('ledger', 'ledger.py / models.py', 'social', 'SQLite 账本、事务、去重与恢复',ref(b+'social/ledger.py',46,74),ref(b+'social/service.py',80,105))
node('persona', 'persona.py / groups.py', 'social', '人格组装、群缓冲与练习策略',ref(b+'social/decision.py',9,25),ref(b+'console.py',221,229))
node('sandbox', 'sandbox.py', 'social', 'SyntheticModel + FakeAdapter 离线闭环',ref(b+'console.py',224,235))
node('provider', '模型接入 providers/codex/', 'root', '文本模型调用与独立认证',ref(b+'bootstrap.py',64,80))
node('wire', 'provider / transport / sse / response', 'provider', '有界文本请求、HTTP 流与结果校验',ref(b+'providers/codex/provider.py',96,125),ref(b+'providers/codex/transport.py',59,88))
node('auth', 'auth / token_manager / credentials', 'provider', '认证、令牌管理与 DPAPI 凭据',ref(b+'cli.py',17,36),ref(b+'providers/codex/credentials.py',1,12))
node('network', 'catalog / config / network', 'provider', '模型目录、端点与显式本机代理',ref(b+'cli.py',39,50),ref(b+'providers/codex/network.py',14,39))
node('bridge', '微信桥接 bridges/wechat/', 'root', '版本绑定 · filehelper 限定接入',ref(b+'bridges/wechat/background_sender.py',46,74),ref('docs/W2B20_BG1_ACCEPTANCE.md',1,5))
node('receive', 'receive_* / reader / snapshot', 'bridge', '读取子进程 → 父进程提交 → ACK',ref(b+'bridges/wechat/receive_process.py',135,163),ref(b+'bridges/wechat/reader.py',51,79))
node('state', 'contracts / store / send_state', 'bridge', '身份、游标、发送许可与证据账本',ref(b+'bridges/wechat/store.py',1,5),ref(b+'bridges/wechat/store.py',15,32),ref(b+'bridges/wechat/background_sender.py',66,84))
node('native', 'native_* / qt_* / window_proc', 'bridge', 'UIA、Qt 身份与原生输入事件路线',ref('scripts/probe_wechat_background_service.py',14,47))
node('background', 'background_*', 'bridge', '准入、观察、锁、意图日志与恢复',ref(b+'bridges/wechat/background_sender.py',73,97))
node('engineering', '执行脚本与工程资料', 'root', 'scripts/ · tests/ · docs/ · 构建配置',ref('pyproject.toml',20,41),ref('scripts/run_wechat_receiver.py',1,17))
node('scripts', 'scripts/', 'engineering', '版本专用入口、探针、worker 与验收',ref('scripts/run_wechat_receiver.py',20,52),ref('scripts/probe_wechat_background_service.py',1,5),ref('docs/W2B19_WEIXIN_42013.md',1,3))
node('tests', 'tests/', 'engineering', '模型、账本、控制台与微信恢复测试',ref('tests/test_console.py',1,19),ref('tests/test_wechat_receive_only_soak.py',1,16))
node('docs', 'docs/ 与依赖构建', 'engineering', '阶段记录、能力边界、锁定与框架打包',ref('docs/W2B20_BG1_ACCEPTANCE.md',1,5),ref('scripts/build_framework.py',18,35),ref('pyproject.toml',1,18))
candidate = dict(schema_version=1,diagram_type='tree',meta=dict(title='WeChatAgent 项目结构与职责',locale='zh-CN',output=(p/'project-structure.html').as_posix(),quality_profile='showcase'),layout=dict(direction='right',nodeW=250,nodeMaxW=310,gapX=48,gapY=26),nodes=nodes,cards=[
dict(dot='cyan',title='三条运行链路',items=[
'本机聊天：CLI → bootstrap → ChatEngine → AI_agent Runtime → CodexTextProvider；mock 使用本机模拟后端。',
'工作台：Console → SocialService → RuntimeDecider(SyntheticModel) → outbox → FakeAdapter；真实预演为独立显式入口。',
'微信接收：专用脚本 → ReaderSession / 读取子进程 → BridgeStore / Ledger 提交 → 子进程 ACK；接收器不调用模型。']),
dict(dot='emerald',title='当前接通范围 · 2026-11-11',items=[
'最新依据为 docs/W2B20_BG1_ACCEPTANCE.md，README 保留较旧阶段描述。',
'微信 4.2.0.13、原账号、独立 filehelper、未最小化且被遮挡：两条固定标记发送及 30 分钟连续只读验收完成。',
'一般模型自动回复未开放；最小化、托盘与锁屏未验收。background_sender 拒绝固定标记之外的内容。']),
dict(dot='amber',title='阅读范围与来源限制',items=[
'树枝表示职责归属，不表示调用；紧密协作文件合并展示。',
'当前目录无 .git，无法固定提交或验证 Git 来源；sources 为本轮查看的本地文件行号。',
'AI_agent 0.3.0 为外部依赖；只说明本项目装配和调用边界，不展开框架内部实现。',
'未展开 .venv、build、缓存、egg-info 及 research 参考材料；.state 为本机账本与凭据数据，本轮未读取其内容。'])])
(p/'candidate.json').write_text(json.dumps(candidate,ensure_ascii=False,indent=2),encoding='utf-8')
Path('.archify/current-structure-path.txt').write_text(p.as_posix(),encoding='utf-8')
print(p.as_posix())
