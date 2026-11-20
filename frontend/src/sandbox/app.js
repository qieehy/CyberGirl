import {api} from "../shared/api.js";
"use strict";
const $ = id => document.getElementById(id);
const labels = {BUFFERED:"缓冲中",MERGED:"已合并",IGNORED:"未触发",QUEUED:"等待确认",CONFIRMED:"已确认投递",SILENT:"保持沉默",CANCELLED:"已取消",EXPIRED:"已过期",UNCONFIRMED:"结果未确认",FAILED:"失败",PROCESSING:"正在生成",RECEIVED:"已接收",SENDING:"正在提交",REJECTED:"已拒绝",DECIDED:"已决策"};
const reasons = {no_group_trigger:"普通群聊，没有触发回复",merged_into_batch:"已合入本窗口的最后一条消息",group_buffer_full:"本轮缓冲已满，请缩短文本或等待处理",model_cooldown:"模型调用仍在 30 秒冷却中",send_cooldown:"模拟发送仍在 30 秒冷却中",model_window_limit:"已达到每 10 分钟 3 次模型调用上限",send_window_limit:"已达到每 10 分钟 3 次发送上限",model_daily_limit:"已达到今天 10 次模型调用上限",send_daily_limit:"已达到今天 10 次发送上限",clock_rollback:"检测到时间回退，暂缓处理",interrupted_decision:"处理已中断，未自动补发",persona_changed:"人格已更新，旧草稿已取消",policy_changed:"权限已变更",global_policy_changed:"沙盒状态已变更",new_context:"有更新的消息，旧草稿已取消",stale_context_or_policy:"上下文或权限已过期",draft_expired:"草稿已超过有效期",interrupted_submission:"提交过程中断，结果待确认",submission_unknown:"提交结果未知",submission_cancelled:"提交被中断",receipt_mismatch:"回执不匹配",not_submitted:"已确认未提交",decision_interrupted:"本轮生成未完成",policy_denied:"沙盒已暂停或不允许此消息",stale_event:"消息已过期"};
let state = null, dirty = false, editingVersion = null, pendingMessage = null, lastActionId = null, pendingPreview = null, previewRunning = false, pendingGroup = null, groupTimer = null;
const time = value => value == null ? "—" : new Date(value * 1000).toLocaleString("zh-CN", {month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"});
function notice(message, error=false) { $("notice").textContent = message; $("notice").className = "notice" + (error ? " error" : ""); }
function node(tag, className, text) { const e=document.createElement(tag); if(className)e.className=className; if(text!=null)e.textContent=text; return e; }
function explain(e){return ({no_confirmed_group_reply:"请先模拟投递一条群回复，再练习引用回复。",group_sandbox_disabled:"此服务未启用群沙盒。",live_preview_disabled:"启动服务时尚未启用真实预演。",policy_denied:"沙盒已暂停，请先恢复。",model_unavailable:"当前账号目录中没有该模型。",stale_preview:"人格或暂停状态已变化，本次结果已作废。",credential_storage_failure:"登录存储不可用或正被另一个聊天进程占用。请先关闭占用该登录的终端会话。",connection_failure:"连接失败，请检查 Hiddify 是否运行；未自动重发。",timeout:"请求超时，请查看记录；未自动重发。",rate_limited:"订阅服务限流，请稍后手动再试。",incompatible_protocol:"模型响应格式不兼容，本次未产生预演内容。",invalid_decision:"模型未返回规定的回复格式，本次未产生预演内容。",interrupted_preview:"请求已中断，调用结果未知；不会自动重发。",preview_capacity:"预演记录已达容量上限。",version_conflict:"设置已在其他窗口更新。当前输入已保留，请载入最新版本后再保存。",authentication_required:"访问尚未授权，请使用启动终端显示的完整链接打开工作台。",invalid_origin:"访问来源不匹配，请从本机工作台打开。",invalid_persona:"人格内容不符合要求，请检查长度和填写内容。",invalid_text:"内容不符合要求，请检查长度或特殊字符。",busy:"沙盒正在处理其他任务，请稍后操作。",request_timeout:"本次操作等待超时。请先刷新查看状态，避免重复提交。",event_identity_conflict:"这条消息的编号已存在但内容不同，请刷新后再试。"})[e.code]||"操作未完成。请检查本地服务是否运行，再刷新查看状态。";}
async function busy(button, action){button.disabled=true;try{await action();}catch(e){notice(explain(e),true);}finally{button.disabled=false;}}
function view(name){document.querySelectorAll(".view").forEach(e=>e.classList.toggle("hidden",e.id!==name));document.querySelectorAll(".nav-item").forEach(e=>{const selected=e.dataset.view===name;e.classList.toggle("active",selected);e.setAttribute("aria-pressed",String(selected));});$("page-label").textContent=({desk:"工作台",persona:"人格设定",replay:"消息回放",groups:"群沙盒"})[name];}
document.querySelectorAll("[data-view]").forEach(e=>e.addEventListener("click",()=>view(e.dataset.view)));
function eventState(e){return e.delivery_state||(e.action==="SILENT"?"SILENT":e.input_state);}
function fillPersona(p){$("persona-name").value=p.name;$("persona-style").value=p.style;$("persona-notes").value=p.notes;editingVersion=state.persona.version;$("editing-version").textContent="基于 v"+editingVersion;}
function eventCard(e,detailed,group=false){const status=eventState(e),root=node("article","event"),top=node("div","event-top"),meta=node("div","event-meta");meta.append(node("span","",time(e.received_at)),node("span","",e.persona_version?"人格 v"+e.persona_version:"旧版记录"));if(group)meta.append(node("span","",({"member-a":"成员 A","member-b":"成员 B"})[e.sender_id]||e.sender_id));top.append(meta,node("span","pill "+status,labels[status]||status));root.append(top,node("p","event-text",e.text));if(e.reply_text)root.append(node("div","reply",e.reply_text));if(e.reason)root.append(node("p","muted",reasons[e.reason]||"本轮未继续执行"));const actions=node("div","event-actions");if(status==="QUEUED"){const send=node("button","button primary","模拟投递");send.addEventListener("click",()=>busy(send,async()=>{const result=await api(group?"/api/group/dispatch":"/api/dispatch",{action_id:e.action_id});await refresh();notice("投递状态："+(labels[result.state]||result.state));}));actions.append(send);}if(detailed){const duplicate=node("button","button outline","验证去重");duplicate.addEventListener("click",()=>busy(duplicate,async()=>{const result=await api("/api/replay",{event_id:e.event_id});await refresh();notice("已识别为重复消息，未重新生成或发送。当前状态："+(labels[result.state]||result.state));}));if(!group)actions.append(duplicate);const details=node("details","details");details.append(node("summary","","查看处理依据"));details.append(node("p","","事件："+e.event_id),node("p","","动作："+(e.action||"尚无决策")),node("p","","提交时间："+time(e.started_at)),node("p","","完成时间："+time(e.finished_at)),node("p","","回执："+(e.receipt_id||"尚无回执")),node("p","","提示词指纹："+(e.prompt_sha256||"旧版记录未保存")));if(group&&e.batch_id)details.append(node("p","","合并批次："+e.batch_id));root.append(details);}if(actions.children.length)root.append(actions);return root;}
function renderEvents(container,events,detailed,group=false){container.replaceChildren();if(!events.length){container.append(node("div","empty","这里还没有记录。先开始一段练习吧。"));return;}events.forEach(e=>container.append(eventCard(e,detailed,group)));}
function showResult(status){$("last-result").classList.remove("hidden");$("last-result").textContent="本轮状态："+(labels[status]||status)+(status==="QUEUED"?"。草稿已在下方，尚未投递。":status==="CONFIRMED"?"。已取得本机模拟回执。":"。没有自动重发或补发。");}
function render(){renderGroups();renderPreviews();const last=state.events.find(e=>e.action_id===lastActionId&&lastActionId);if(last)showResult(eventState(last));const p=state.persona;$("connection").textContent=state.paused?"沙盒已暂停":"本地服务已连接";$("pause").textContent=state.paused?"恢复沙盒":"暂停沙盒";$("count-events").textContent=state.events.length;$("count-queued").textContent=state.events.filter(e=>eventState(e)==="QUEUED").length;$("count-confirmed").textContent=state.events.filter(e=>eventState(e)==="CONFIRMED").length;$("preview-name").textContent=p.name;$("preview-version").textContent="当前版本 v"+p.version;$("preview-style").textContent=p.style;$("preview-greeting").textContent="你好，我是"+p.name+"，一个 AI 助手。";if(!dirty)fillPersona(p);renderEvents($("recent"),state.events.slice(0,4),false);const filter=$("state-filter").value;renderEvents($("replay-list"),state.events.filter(e=>filter==="all"||eventState(e)===filter),true);$("versions").replaceChildren();state.versions.forEach(p=>{const item=node("div","version"),top=node("div","version-top");top.append(node("strong","","v"+p.version+" · "+p.name));if(p.version===state.persona.version)top.append(node("span","small-tag","当前"));else{const load=node("button","text-button","载入设定");load.addEventListener("click",()=>{fillPersona(p);dirty=true;notice("已载入 v"+p.version+" 的设定。保存后会成为新版本。");});top.append(load);}item.append(top,node("small","",time(p.created_at)),node("p","",p.style));$("versions").append(item);});}
async function refresh(){state=await api("/api/state");render();}
$("refresh").addEventListener("click",()=>busy($("refresh"),refresh));
$("state-filter").addEventListener("change",()=>{if(state)render();});
$("persona-form").addEventListener("input",()=>{dirty=true;});
$("reload-persona").addEventListener("click",()=>busy($("reload-persona"),async()=>{await refresh();fillPersona(state.persona);dirty=false;notice("已载入最新人格版本。");}));
$("persona-form").addEventListener("submit",e=>{e.preventDefault();busy($("save-persona"),async()=>{if(!state)throw new Error("not_ready");const p=await api("/api/persona",{expected_version:editingVersion,name:$("persona-name").value,style:$("persona-style").value,notes:$("persona-notes").value});dirty=false;await refresh();notice("已保存人格 v"+p.version+"。旧草稿已取消，新练习会使用新版本。");});});
$("pause").addEventListener("click",()=>busy($("pause"),async()=>{if(!state)throw new Error("not_ready");await api("/api/pause",{expected_version:state.policy_version,paused:!state.paused});await refresh();notice(state.paused?"沙盒已暂停，待发草稿已取消。":"沙盒已恢复，旧草稿不会自动重发。");}));
document.querySelectorAll("[data-suggestion]").forEach(e=>e.addEventListener("click",()=>{$("message").value=e.dataset.suggestion;pendingMessage=null;$("message").focus();}));
$("message").addEventListener("input",()=>{pendingMessage=null;});
$("message-form").addEventListener("submit",e=>{e.preventDefault();busy($("generate"),async()=>{if(!state)throw new Error("not_ready");if(state.paused){notice("沙盒已暂停，请先恢复。",true);return;}pendingMessage=pendingMessage||{message_id:crypto.randomUUID(),occurred_at:Date.now()/1000,text:$("message").value};const result=await api("/api/messages",pendingMessage);pendingMessage=null;lastActionId=result.action_id;showResult(result.state);await refresh();});});
refresh().catch(e=>{$("connection").textContent="尚未连接";notice(explain(e),true);});

function renderPreviews(){
  const live=state.live_preview||{enabled:false,runs:[]};
  $("live-model").textContent=live.enabled?live.model:"尚未启用";
  $("mode-badge").textContent=live.enabled?"沙盒模拟 · 可选真实预演":"离线模拟";
  $("run-preview").disabled=!live.enabled||state.paused||previewRunning;
  $("preview-status").textContent=previewRunning?"正在请求真实模型，请勿重复提交…":!live.enabled?"启动时需指定 --live-model":state.paused?"沙盒已暂停":"使用已保存的 v"+state.persona.version+" · 点击才调用";
  const root=$("preview-runs");root.replaceChildren();
  for(const run of live.runs){
    const card=node("article","event"),top=node("div","event-top");
    const status=({COMPLETED:"预演完成",FAILED:"预演失败",UNKNOWN:"结果未知",RUNNING:"正在请求",SUPERSEDED:"已作废"})[run.state]||run.state;
    top.append(node("span","event-meta",time(run.started_at)+" · 人格 v"+run.persona_version+" · "+run.model),node("span","pill",status));card.append(top);
    if(run.reply_text)card.append(node("div","reply",run.reply_text));
    if(run.action==="SILENT")card.append(node("p","muted","模型选择保持沉默。"));
    if(run.error)card.append(node("p","muted",({authentication_required:"订阅登录需要恢复，请在终端检查 auth status。",busy:"登录正被另一个会话占用，或已有预演进行中。请结束占用会话后再试。"})[run.error]||explain({code:run.error})));
    card.append(node("p","muted","模型 HTTP 尝试："+(run.model_http_attempts==null?"未知":run.model_http_attempts)+" · 未投递"));
    root.append(card);
  }
}
$("preview-text").addEventListener("input",()=>{pendingPreview=null;});
$("preview-form").addEventListener("submit",async e=>{
  e.preventDefault();if(previewRunning||!state?.live_preview?.enabled)return;
  if(dirty){notice("请先保存人格，或载入最新版本，再开始真实预演。",true);return;}
  previewRunning=true;renderPreviews();
  try{
    if(pendingPreview&&pendingPreview.expected_version!==state.persona.version)pendingPreview=null;
    pendingPreview=pendingPreview||{request_id:crypto.randomUUID(),expected_version:state.persona.version,text:$("preview-text").value};
    const result=await api("/api/preview",pendingPreview);
    if(result.state!=="RUNNING")pendingPreview=null;
    await refresh();notice(result.state==="COMPLETED"?"真实预演完成，结果仅在本机显示。":"预演状态已记录，请查看下方结果。",result.state!=="COMPLETED");
  }catch(e){notice(explain(e),true);}
  finally{previewRunning=false;renderPreviews();}
});

function renderGroups(){
  const group=state.group_sandbox||{enabled:false,events:[]};
  $("group-submit").disabled=!group.enabled||state.paused;
  renderEvents($("group-events"),group.events,true,true);
  if(groupTimer)clearTimeout(groupTimer);
  if(group.events.some(e=>["BUFFERED","RECEIVED","PROCESSING","SENDING"].includes(eventState(e))))
    groupTimer=setTimeout(()=>refresh().catch(e=>notice(explain(e),true)),1000);
}
$("group-form").addEventListener("input",()=>{pendingGroup=null;});
$("group-form").addEventListener("submit",e=>{
  e.preventDefault();busy($("group-submit"),async()=>{
    pendingGroup=pendingGroup||{message_id:crypto.randomUUID(),occurred_at:Date.now()/1000,text:$("group-text").value,sender_id:$("group-sender").value,trigger:$("group-trigger").value};
    const result=await api("/api/group/messages",pendingGroup);pendingGroup=null;
    await refresh();notice(result.state==="BUFFERED"?"已进入群缓冲，处理结果会自动更新。":(reasons[result.reason]||labels[result.state]||result.state),result.state==="REJECTED");
  });
});
