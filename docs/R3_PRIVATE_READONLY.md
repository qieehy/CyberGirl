# R3 指定私聊：身份及只读接收（当前Amadues）

2026-11-16。R1/R2之后按计划推进。用户先指定昵称LFY，随后明确改用昵称Amadues，并已发送精确PEER。本期限定只读身份／接收／展示和恢复，零模型、零程序发送。当前Amadues的规范发送者、精确文本、一次落账、幂等重放、读取器重启和HTTP展示均已取得证据；该已发消息陈旧，原stale_event保留，不宣称新鲜入站候选或R3完整草稿／确认发送已通过。

## 当前Amadues结果

Amadues新独立窗口21168522取得同一原账号、固定4.2.0.13、原生规范私聊键及根窗口交叉匹配。规范目标SHA-256 `e1f54edafa19e1d8d3ebb893387fd13c4e31f79c715127350b4dd86539ed1734`，原标识只存 `.state/social/r3-owner-Amadues-readonly.json`。该文件仍仅observe，model_allowed／send_allowed均false。

用户明确更换后，旧LFY的bridge binding持久标为private_target_replaced；原计划、游标与已落账事实保留，不删除或改写。旧配置在新worker创建前即拒绝，当前只允许新的未停用目标。此变更使用schema10已有表，不另建账本绕过原发送历史，也没有通用解除发送限制。

因用户已发PEER，使用其新目标的限定近期首次接收，而不要求重发或倒退旧目标游标：以选择轮固定时间窗（Asia/Shanghai 2026-11-16 16:57:51至17:28:21）和精确文本 `R3-20261116-PEER`，只在两个既定分库、每库最多32条所选peer文本内匹配，超界／重复／非该peer均拒绝；只读取该目标，不扫描联系人。匹配后只为新目标登记该消息前的独立基线，其他分库使用尾水位；这不是既有cursor回退，也不变更LFY/filehelper水位。

真实读取进程22712取得唯一peer来源 `6173bcd772b0ab2dbd9752048d18fb7c00092bf18f2359c4f4887d7f6d6c2571`，正文哈希 `c287b0175956df7612f8235382b3188e3adcc1cd44b528cbc6a3fb6abac913e6`，只有一行。接收延迟840.738秒，依原120秒策略保持stale_event、没有伪造OBSERVED或新鲜inbox。原批幂等重放全为not fresh；首进程退出，全新读取器43360使用通常只读worker恢复相同游标／代际，fresh/replay均0，无需继续启用近期查找。认证工作台HTTP对target／规范发送者／正文哈希逐项一致，模型／发送器均未装配。

独立最终审计核对零新草稿／model_trace、原发送六表和workbench历史、账号及物理分库目录不变，filehelper游标不变、LFY计划／游标保留；仍17次操作、累计许可15/11，八条旧UNKNOWN身份列表及E7确认保持，活动操作／绑定0、所有读取进程回收、完整性ok，合格Amadues窗口独立gate0。

证据：[Amadues身份](evidence/r3-Amadues-selected-identity-2026-11-16.json)、[精确peer接收／重放／重启／HTTP](evidence/r3-Amadues-recent-peer-receive-2026-11-16.json)、[最终独立审计](evidence/r3-Amadues-final-audit-2026-11-16.json)。近期接收的一次性入口为 `.build/r3_amadues_recent_receive.py` 及其自有worker，不是普通console或普遍历史查找功能。独立窗口／PID均须重新核对，不能照抄本轮数值。

## LFY先前身份事实（已停用，保留）

原微信4.2.0.13、PID7872／创建代际保持；候选独立窗口591142。只查询这个窗口的根标签与单个输入控件RuntimeId，不获取消息文本或草稿值，不枚举联系人。先比较用户选定标签（仅作窗口选择），再由固定Qt布局读取同一ChatMessagePage的规范会话键，并与同一根窗口AutomationId交叉匹配；前后UIA快照及无密钥账号读取一致。

规范目标SHA-256为 `0fdd4a5512d739fac24024c4695615c6a8a631b4e2f5dd7944d5a72c825e2723`。原标识只保存在 `.state/social/r3-owner-LFY-readonly-v2.json`，不复制到evidence。该文件限定kind=dm、mode=observe、model_allowed=false、send_allowed=false；它不是发送许可。首次身份证据保留，v2补齐显式账号指纹，目标哈希不变。两轮均未读正文或写账本，子进程／租约／锁恢复及gate0通过。

证据：[首次身份](evidence/r3-LFY-selected-identity-2026-11-16.json)、[账号绑定身份v2](evidence/r3-LFY-selected-identity-v2-2026-11-16.json)。昵称不作为规范路由键，filehelper、自身及@chatroom在本探针被拒绝。既有page_session_key仍只披露filehelper等值，不向旧探针泄露其他会话标识。

## 本期源码

- 独立身份探针 `scripts/probe_wechat_private_identity.py` 与只读UIA元数据脚本；不具备ValuePattern读取／设置、聚焦、点击或发送路径。
- 独立PrivateReaderSession／Catalog及worker，目标文件必须字节哈希一致；账号指纹、固定构建、原数据库根、精确目标／物理分库集合逐项复核。
- 父进程在落账及ACK之前限制发送者只能为所选规范peer或本账号；第三发送者、目标变化、群与错误账号均拒绝。
- 原filehelper读取默认、发送白名单和游标不泛化。私聊cursor的source_key包含单独规范会话；首次baseline计划先持久化，基线登记中断后仍使用原计划，不用新水位跳过消息。
- 工作台构造层对PrivateReaderSession明确拒绝装配generator、model、sender；HTTP状态的target来自绑定，观察不会生成或投递。

首个R3切片只支持原账号已注册的精确物理分库集合，新增／轮转分库直接拒绝，不冒充已完成私聊轮转验收。当前只允许一个未停用的已选定私聊目标；显式更换必须核对原目标哈希、旧观察器已退出和用户引用，保留旧记录并持久阻止旧范围重新启动；不提供通用解除限制或替换主人按钮。

## schema10与迁移

新增reader_private_targets，保存单目标及各分库的首次基线计划；原reader_accounts／reader_shards保留。Ledger升级9→10之前创建.v9时间戳备份，迁移同事务提交，失败回滚，未来schema11拒绝。原filehelper历史／许可／UNKNOWN不重写。回退必须保留schema10账本及新增私聊事实，不覆盖抹去新记录。

已在851项与848子场景全量通过／9项预期跳过后，原真实账本备份为 `wechat-sender-poc.sqlite3.v9-1794820111353099200.bak` 并升至schema10。LFY首次两个分库基线登记成功，不回扫历史正文。独立备份／升级后完整性和原发送等10张表／filehelper游标逐行比较通过，见 [迁移审计](evidence/r3-schema10-migration-audit-2026-11-16.json)。新模块wheel构建及源码字节对照通过，但原生运行仍依赖完整源码，不把wheel称为独立原生发行包。

之后用户更换Amadues，新增显式停用旧范围和有界近期精确peer选择，46项相关场景通过；收尾加强单条正文SQL投影上限及超长反例。最终完整分组覆盖为 **859项通过（含1项孤立超时复验）、9项跳过、848子场景通过**，不是一次未中断全量通过：主组835通过／1失败／9跳过／821子场景，HTTP组23通过／27子场景；唯一失败是既有合成UI树PowerShell夹具超过15秒，单独复验1项在0.98秒通过，未修改生产超时。原全量轮曾在既有HTTP取消测试之后异步等待超过60秒并中断，日志保留，不记作通过。成功路径夹具曾缺少应用自有worker脚本，补齐临时夹具后通过，未放宽生产归属检查；更早全量因新增构造层只读限制主动中断，也不记作通过。

最终Ruff、JS与身份PS语法通过。新wheel与8个变更应用模块源码字节一致，原生运行仍需完整源码checkout。分组日志、原失败、孤立复验及wheel摘要见 [最终验证](evidence/r3-Amadues-final-validation-2026-11-16.json)；原851项单进程全量及原wheel阶段证据保留于 [基础验证](evidence/r3-readonly-validation-2026-11-16.json)，不覆盖或混称为最终一次成功。

### LFY实际输入及未通过的标记验收

第一条精确INBOUND记录规范发送者为本账号，正确保持self_echo，因此验收在“必须由所选peer入站”处停止；LFY随后对方回复的同名文本在停止间隙超过原新鲜度门槛，保留stale_event。另有非标记LFY新消息已正确OBSERVED，非文本仍unsupported_message_type，不转成候选。PEER精确标记的300秒等待未收到后退出；不能将其写为通过。详见 [首标记事实](evidence/r3-LFY-inbound-classification-2026-11-16.json)、[新记录安全事实](evidence/r3-LFY-new-record-facts-2026-11-16.json)、[PEER等待结果](evidence/r3-LFY-private-peer-inbound-2026-11-16.json)。曾有辅助脚本尝试复核已落账LFY来源，在原指纹一致性断言处停止且清理完成，未修改原指纹；后续用户改用Amadues，不再沿旧目标继续诊断／读取。所有失败／超时均保持零模型和程序发送。

## 现场顺序

完整离线通过 → 原账本备份迁移 → `--initialize-only` 只登记LFY尾水位且不读历史正文 → 启动仅观察接收器 → 请LFY从对方账号发送新唯一标记 → 核对规范发送者、OBSERVED、正文只落账一次 → 同批幂等重放 → 全新读取进程恢复同一游标 → 认证HTTP展示及零模型／发送 → 独立账本／开关审计。

现场仅使用 `scripts/probe_wechat_private_receive.py`，要求显式当前绑定、目标文件、新输出及二选一的初始化／限定新文本模式。不要提前发标记；读取器开始等待后再发送，不重放旧来源。普通console默认fake不变，本期没有给真实私聊装配模型或发送器，也不把HTTP接收验收称为已完成主人助手UI和确认发送。
