# E7 文件助手工作台现场记录

2026-11-16。本记录为 E0–E6 之后的新实机证据；不覆盖此前 BG1 结果。

## 当前结论：E7 限定文件助手工作台闭环通过

用户恢复代理后，明确批准新来源的一次生成并沿用原单次后台发送范围。新来源 `47c36cccc426a99f33b59d5dad44e5b52ee8019ef7d42df7792369a0ac48b26c` 独立接收1条，重启fresh=0、HTTP正文核对通过。随后 gpt-6.1-sol 推理1次、推理HTTP1次、phase=complete，保留模型版本1，将已批准最终正文保存为operator版本2；HTTP准备/确认绑定最终正文，重复相同确认返回同一action，未重复投递。

实际仅一次输入许可、一次提交许可，取得唯一精确local_echo。用户明确确认手机收到完整正文且电脑输入框已空，既有确认入口追加独立manual_confirmation；action `742188c0a8723119ad2b296752315eaafc1e4aa061526fe6d5f498fa807177e0` 为CONFIRMED。确认后全新读取器18044及只读工作台启动，fresh/replay=0、无模型/发送装配，六张发送表和模型trace数在重启前后逐行/计数不变。

最后独立原生检查再次核对原账号、规范filehelper、Value/Text双模式空输入、按钮释放、非最小化遮挡条件，观察未见目标前台化，租约/锁释放且gate=0。原发送六表全部历史行与schema8备份逐行一致，八条旧UNKNOWN身份列表完全不变。最终17次操作、累计输入/提交许可15/11，活动操作/绑定0、全部读取/UI工作进程回收、完整性ok；原通道仍要求逐条审核。

验收范围为微信4.2.0.13、原账号、未最小化且被其他应用遮挡的独立文件助手窗口、显式选来源/生成/编辑/核对/单次发送。网页此前由合成边界完成视觉验收，本次真实闭环通过工作台HTTP接口执行；不把它描述成浏览器手工逐按钮实机验收。先前目录失败和首次失败均保留且未重放。R1–R6、自动回复、最小化/托盘、主人私聊和真实群仍未验收。

证据：[最终独立审计](evidence/e7-workbench-acceptance-2026-11-16.json)、[新来源接收/重启/HTTP](evidence/e7-workbench-resume2-63728-receive-2026-11-16.json)、[本次生成/编辑/发送/手机确认](evidence/e7-workbench-live-93331f692a3a05e7-2026-11-16.json)、[确认后重启无重放](evidence/e7-workbench-final-restart-2026-11-16.json)、[最终原生空输入及恢复](evidence/e7-workbench-final-covered-2026-11-16.json)。以下各节按发生顺序保留此前状态。

## 已通过：接收、展示及重启

- 原账号及微信 4.2.0.13、独立窗口原生规范 filehelper 核对通过。
- 双模式输入为空；后台遮挡、原生代码/控件与读取 worker 准备通过。两次临时启用均恢复 gate=0，零输入/提交。
- 原真实账本从 schema 8 备份升级至 9，备份 `wechat-sender-poc.sqlite3.v8-1794802088208201400.bak`。
- 读取原持久游标后的 8 个新记录，4 条为文本；选定用户刚发的测试来源，自身回声分流保留，不自动生成。
- 原读取子进程 9476 退出后，新进程 736 从同一游标恢复；再次轮询 fresh=0，选定正文仅一条。
- 通过真实工作台 HTTP /api/wechat/state 核对正文哈希一致、模型与发送均未装配。全部读取进程已回收。
- 发送统计保持原样：16 次操作、累计输入/提交许可 14/10、8 条旧 UNCONFIRMED、活动操作 0，完整性 ok。

证据：[窗口身份](evidence/e7-workbench-window-readonly-2026-11-16.json)、[后台准备](evidence/e7-workbench-covered-readiness-2026-11-16.json)、[接收/重启/HTTP](evidence/e7-workbench-receive-restart-2026-11-16.json)。

迁移独立复核：schema8 备份和 schema9 原账本完整性均 ok，outbox、attempts、发送操作、journal、审核和投递证据六张原发送表逐行一致。见 [迁移审计](evidence/e7-workbench-migration-audit-2026-11-16.json)。

## 模型尝试与登录恢复

> 最新：用户重新发送63728并明确批准新事件的一次本地草稿；本次真实模型草稿已通过，gpt-6.1-sol、推理调用1、推理HTTP请求1、phase=complete、正文已保存。新来源ID为e5732a…660f，与旧失败来源不同；旧FAILED保留。运行时未装配发送器，发送操作/许可和八条旧UNKNOWN均未变，全部读取器已回收、活动绑定0。此次只证明草稿，未编辑为发送正文、未确认或发送微信，E7完整发送验收仍未完成。见下方新来源验收及证据。

用户在本机终端完成新的登录流程并报告 ready。2026-11-16 13:08（Asia/Shanghai）独立只读核验通过：持久状态active、有效令牌已保存、按应用120秒提前量规则为ready、last_error=null，实际配置代理为 http://127.0.0.1:7897；核验零刷新、零模型请求。见 [登录恢复证据](evidence/e7-login-restored-2026-11-16.json)。当前无需再登录。此次证明新登录恢复，不计作H4b自然刷新通过。

### 新来源单次真实草稿通过

- 新手机文本独立接收1条，来源ID `e5732a42566e6fa977abe6f1e6c48a67b847c99201c34d6f83f331f18c63660f`，自身回声语义保留。读取器47812→18440，游标一致、再次fresh=0、正文仅落账一次。
- 用户批准后通过真实工作台HTTP生成接口只执行一次；使用明确的 `--draft-only`，没有发送适配器、原生UI准备、编辑或发送能力。
- 草稿ID `4b1379cc964d4aeea8b6358e05002918ae9bd16b40401b7187ebd17bd80b6dbd`，生成完成时READY，保存模型版本1；阶段complete、inference_calls=1、model_http_attempts=1。
- 正文指纹 `2dd3add442b8c914f7de241d3ae0b915e5b78eb71b6137e240e838c9861363ab`，正文只保存在原本机账本并回显给用户，不复制到evidence。
- 原旧来源 `07f103…2257` 的生成记录保持FAILED，没有重放、改写或删除。
- 与schema8备份逐行比对，原outbox、attempts、发送操作、journal、审核、投递证据六张表全部不变；16次操作、输入/提交许可14/10、UNKNOWN8，未新增action。原始运行结果的 reviewed_unknown_action_ids 字段仅是旧条目快照，本次没有新历史审核或发送许可。
- reader_reaped=true、活动绑定0、完整性ok。内容可在本地账本回看；运行结束/绑定撤销与120秒期限规则仍生效，不恢复发送许可。

证据：[新来源接收/重启](evidence/e7-workbench-new63728-receive-2026-11-16.json)、[真实草稿运行](evidence/e7-workbench-live-4d47f3c0f547721a-2026-11-16.json)、[单次及零发送独立审计](evidence/e7-workbench-model-only-audit-2026-11-16.json)。

### 先前失败和诊断历史（保留）

真实模型脚本被自动审批审查在 CreateProcess 阶段拒绝，尚未启动；原账本 workbench_drafts=0，模型请求 0。原因是旧授权未明确覆盖选定测试文本及具体模型目的地的外发。已请求用户明确允许将这条选定测试文本与人格版本1传给 OpenAI ChatGPT 订阅模型 gpt-6.1-sol，仅生成一次本地草稿。

获准后，执行一次草稿并保存编辑版本，再准备单条最终正文和具名历史审核。旧 UNKNOWN 不改为送达，未获得新一条最终正文/历史范围的审核前不输入或提交。

用户随后明确允许将测试文本 `63728` 与当前小禾人格发送至 gpt-6.1-sol，生成一次本地草稿。仅执行一次生成启动，结果 FAILED/generation_failed，没有模型回复、编辑版本、原生输入或提交。原读取进程已回收，发送账本仍为16/14/10、旧UNKNOWN8、活动0、完整性ok。原始失败见 [单次结果](evidence/e7-workbench-live-f5bfeb58434e360a-2026-11-16.json)。此版本只记录生成启动数1，未记录实际推理HTTP次数，不补造0或1。

只读 auth status 返回 reauthentication_required / connection_failure。配置检查发现项目仍指向无监听的12334；用户说明已改用 Clash Verge 7897 后，已将本地项目配置更新为 http://127.0.0.1:7897，并确认该端口有本机监听。通过新代理成功取得设备授权码，但后续登录请求 connection_failure，登录进程已退出。用户随后报告浏览器授权完成，独立同步本地复查仍为 logged_out / tokens_present=false，未保存登录。需要新的本机登录流程；设备码不写入本文或 evidence。失败的 `63728` 请求不自动重跑，不重置其源消息唯一限制。

补充的两次公共认证页 GET 诊断未执行：自动审批服务本身返回403导致审查无法完成，并非判定诊断不安全；未绕过审批。当前让用户在本机终端恢复独立登录，不重复模型请求。

已修复新工作台把 ProviderError 隐藏为 generation_failed 的诊断缺口：后续保留安全错误码，并把 open/session/catalog/model/parse/complete 阶段及实际推理/HTTP计数存入本机 model_trace。没有提示词、正文、响应、令牌或任意异常字符串。此前失败的记录保持原样，不追溯伪造阶段。最终相关回归45项及4个子场景通过（44.71秒），包括认证前零推理、模型成功/失败、请求计数、不重试；Ruff和JS语法检查通过。

新wheel构建及9个模块/页面资源字节对照通过；本轮诊断修复及原发送账本未变的摘要见 [诊断验证](evidence/e7-workbench-diagnostics-validation-2026-11-16.json)。当前已请用户从本机PowerShell启动新的 auth login 流程，并等待终端显示 ready 后再交回；不是继续使用已退出进程的旧设备码。

## 续接准备（2026-11-16）

只读复核原发送六张表与 schema8 备份逐行一致：16 次操作、输入/提交许可 14/10、8 条旧 UNCONFIRMED，活动操作/绑定均为 0，完整性 ok。原成功草稿虽仍存储为 READY，但已超过 expires_at 且未关联 action；存储状态不表示仍可发送。不能恢复旧绑定、延期或重放旧来源。见 [续接只读审计](evidence/e7-workbench-resume-preflight-2026-11-16.json)。

下一次具体方案为：新手机文本 `63728`、当前小禾人格、gpt-6.1-sol 一次生成；编辑最终正文为 `E7-20261116-工作台：已收到手机测试消息。`（26 个 UTF-16 码元，SHA-256 `161fce233787b19cf2b1712fcf13bac68d3a8dd5898a4ae93858a14478aae228`），只向规范 filehelper 后台发送一次，再独立核对回显、清理恢复和手机。此前明确许可仅覆盖本地草稿，因此已提出这一新模型/发送范围与下列八条历史不确定性的具体验收请求；未获明确回复前不启动依赖动作。

现场脚本 `.build/e7_workbench_receive.py` 与 `.build/e7_workbench_live.py` 已移除旧 PID/HWND/进程代际常量。两者必须提供当前正数绑定；接收必须明确新正文和新证据输出，模型必须明确来源证据与许可引用，且在 `--draft-only` / `--send-once` 中显式选一。发送模式仍须本次 run_id、最终正文哈希和完整旧 UNKNOWN 列表匹配的独立审批文件；参数本身不代替用户授权。现场失败退出码现为 1，不再把流程返回记作成功。仅完成命令行准入检查，未执行新原生准备、生成或发送。

续接全量离线回归 **801 passed、9 skipped、848 subtests passed（264.33 秒）**，Ruff、页面 JS 与三个现场辅助脚本的 Python 语法检查通过。初次沙盒内运行阻塞于 Python 本机 socketpair 的 accept，已中断；获准沙盒外重跑完成，合成模型/临时账本不变。见 [续接验证](evidence/e7-workbench-resume-validation-2026-11-16.json)。

用户已明确批准以上一次模型生成及单次后台发送，并接受下列八条历史不确定性。实机只读枚举取得原 PID 7872、创建代际 134392115523127861；候选独立窗口 31721126 当前为最小化。尚未启动接收、生成、原生输入或提交，许可未消费；已请用户恢复后由其他应用遮挡。批准继续有效，不能因本次前置停止另行要求重复批准或自动显示窗口。

### 恢复窗口后执行：目录连接失败，零推理／发送

用户回复已恢复并遮挡、已发新 `63728` 后，继续原已批准方案，没有重复索取发送许可。只读枚举确认原 PID/代际和独立 HWND 未变且不再最小化；原账号、规范 filehelper、双模式空输入和 covered worker readiness 全部通过。后台观察未见目标前台化，临时租约恢复 gate=0。见 [本轮后台准备](evidence/e7-workbench-resume-covered-2026-11-16.json)。

从原游标接收 2 个新文本，选定最新匹配 `63728` 来源 `259d917f3b51ed04cf436230448832e6a64cfa8bb913faae60aa409213bf81f7`；第一读取器 36156 退出，新读取器 47116 从同一游标恢复，fresh=0、正文仅一条、HTTP 展示一致。全部读取器退出，self_echo 原义保留，没有自动生成或发送。见 [本轮接收/重启](evidence/e7-workbench-resume63728-receive-2026-11-16.json)。

仅启动一次显式生成，run `c8402e2b4362165b`，draft `c393d89d7ec4f31808955301c5012df803aafb2b8cbab3123062eb6edec7abeb`。结果 FAILED/connection_failure，phase=catalog、inference_calls=0、model_http_attempts=0；model_calls=1 表示工作台已启动一次生成，不等于已发一次推理请求。未生成正文、保存编辑、原生准备或创建 action，不重试该来源、不从其他旧来源替代。见 [本轮实际结果](evidence/e7-workbench-live-c8402e2b4362165b-2026-11-16.json)。

只读诊断确认代理仍为 `http://127.0.0.1:7897` 且有本机监听；登录凭据 active、令牌已保存、last_error=null。两次不含凭据/正文的公共 GET 中，认证页返回 302，ChatGPT 公共页 ReadTimeout；此诊断只说明当次连接不稳定，未证明目录失败的完整根因，不要求重新登录。见 [连接诊断](evidence/e7-workbench-resume-connectivity-2026-11-16.json)。

独立只读审计核对原发送六张表与备份逐行一致，仍 16 次操作、许可 14/10、旧 UNKNOWN8、活动操作/绑定0、完整性ok；失败来源仅一条生成记录、无 native_preparation、读取器已回收、gate=0。见 [失败收尾审计](evidence/e7-workbench-resume-failure-audit-2026-11-16.json)。辅助审计初次仅因 scripts 导入路径缺失在读取前退出，补齐源码根路径后完成；未重复执行模型或现场动作。

下一步等待用户恢复 Clash 连通性并发送新来源；已请求新来源的一次生成范围。原已批准最终正文、单次后台发送及八条历史不确定性范围仍有效且未消费，不重复索取发送批准、不复用本轮 FAILED 来源或旧已过期草稿。E7 编辑/发送/手机确认继续未通过。

## 历史未知范围

以下八条保持 UNKNOWN/UNCONFIRMED。本次若继续，接受只适用于新 E7 单条动作；不构成确认旧送达、不重发旧动作、不一般解锁。

| 原 action_id | 原原因 |
| --- | --- |
| `1a456235d82b299c920e453512d90bec90f583286f2ed17055dc527884bc7afd` | directed_button_press_state_unexpected |
| `e8b2e295fb357ddf33b6df8be33b82e07aae55c124c9e3a3b1388258757ed7aa` | posted_button_press_not_observed |
| `e3917a17916f4156c3ed706951c4ec63adb0b5092ce9ea13eeda8643a2bb6cc1` | posted_completion_not_verified |
| `1b72f580238f6f27751f496f17c44ba8dd13b06dc9a5ce9a564f7d2cd7d3973a` | input_dirty |
| `cc9c6a179071690b704ee97d3811e09b011e9c6d26948ee651538d0ca7a3a1b7` | background_target_became_foreground |
| `6d36507fd615c85a28711e994ebf1d43d3dc1e1cf1429a172b5b50568b96d391` | background_target_became_foreground |
| `9c48e9d7cf1aa63f33be69bc8f65661cb9af1cc534bc01955838706869d17758` | background_target_became_foreground |
| `8070d04d139e6f15ce906a4631ff87471ea1c8daa2542c6aac1c91777496329a` | background_target_became_foreground |
