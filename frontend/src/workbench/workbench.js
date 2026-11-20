import {api} from "../shared/api.js";
"use strict";
const $ = id => document.getElementById(id);
let state, selected, persona, confirmation, pendingGeneration, pendingConfirm, pendingCompose;
let modelCatalog=[], modelLoading=false;
const edits = new Map(), requests = new Map();
const names = {RUNNING:"正在生成",READY:"已生成，等待核对",SILENT:"保持沉默",UNKNOWN:"生成结果未知",FAILED:"失败",CANCELLED:"已取消",SUBMITTED:"已创建单次发送动作",QUEUED:"等待提交",SENDING:"正在尝试发送",UNCONFIRMED:"结果未确认",CONFIRMED:"手机已确认",EXPIRED:"已过期"};
names.API_ACCEPTED="微信接口已受理";
const errors = {stale_draft:"草稿已过期，或消息、人格、绑定发生变化。",version_conflict:"版本已变化，请刷新核对。",wechat_send_disabled:"启动时未启用逐条确认发送。",live_model_disabled:"启动时未指定真实模型。",source_generation_already_used:"这条来源已经生成过，不会重复调用；可以编辑已有草稿。",confirmation_expired_or_used:"确认已过期或消费，请查看原动作状态。",background_supplementary_character_unsupported:"包含 emoji 或补充平面字符，请自行编辑。",background_nonprinting_character_unsupported:"正文必须是非空单行可打印文本。",background_text_length_or_type_unsupported:"后台正文最多 96 个 BMP 码元。",background_current_generation_unreconciled:"当前进程有尚未核对的输入或投递，请先处理原记录。",channel_review_prior_delivery_unreconciled:"历史投递仍需具名核对。",channel_review_uncertainty_scope_invalid:"历史未知记录审核范围缺失或无效。",model_daily_budget:"今天已达到 30 次生成预算。",workbench_not_reading:"读取已停止，请检查状态并重新启动工作台。",busy:"已有任务在运行。",authentication_required:"请从终端输出的完整工作台链接打开。",phone_confirmation_echo_required:"尚无唯一新回显，不能登记手机确认。"};
Object.assign(errors,{private_peer_source_required:"请选择当前联系人的已保存入站文本；已有消息也可用于本地草稿。",private_local_draft_scope_required:"本次启动未开放私聊本地草稿。",private_readonly_scope_required:"当前私聊范围未开放模型或微信发送。",source_generation_already_used:"这条来源已有草稿记录；可以编辑已有草稿，不会另建或重复调用模型。"});
Object.assign(errors,{private_media_disabled:"尚未启用媒体识别。",image_not_cached:"图片尚未缓存到本机。",sticker_not_cached:"表情包尚未缓存到本机。",voice_not_cached:"语音尚未缓存到本机。",media_decode_failed:"本地媒体解码失败；后续文字回复继续。",voice_transcription_empty:"未识别出清晰语音。",image_format_unsupported:"当前图片格式暂不能解码。"});
Object.assign(errors,{model_unavailable:"该模型不在当前账号的可用列表中，请重新加载。",model_changed:"已切换模型，旧草稿已取消。",invalid_model:"模型名称无效。",connection_failure:"无法连接模型服务，请检查代理后重新加载。"});
Object.assign(errors,{draft_or_input_changed:"输入框有未发送内容（可能是空格），或发送时内容已变化。请清空草稿后恢复自动回复。",background_target_became_foreground:"旧版后台发送因微信切到前台而停止；更新后已支持前后台发送。"});
function element(tag, text, cls="") {const e=document.createElement(tag);e.textContent=text;e.className=cls;return e;}
function notice(text, error=false){$("notice").textContent=text;$("notice").className="notice"+(error?" error":"");}
function explain(e){return errors[e.code] || "操作未完成："+(e.code||"连接失败，请先刷新查看记录；未自动重试。");}
async function act(button, fn){button.disabled=true;try{await fn();}catch(e){notice(explain(e),true);}finally{button.disabled=false;if(state)render();}}
function button(text, fn){const b=element("button",text,"button outline");b.addEventListener("click",()=>act(b,fn));return b;}
function key(id, kind, version){const k=id+":"+kind+":"+version;if(!requests.has(k))requests.set(k,crypto.randomUUID());return requests.get(k);}
function showReview(value){confirmation=value;pendingConfirm=null;const preview=value.preview_only===true;$("confirm-title").textContent=preview?"本地最终正文预览":"核对最终发送";$("confirm-meta").textContent=(state.target_label||value.target)+" · v"+value.version+" · 有效至 "+new Date(value.expires_at*1000).toLocaleTimeString();$("confirm-text").textContent=value.text;$("confirm-note").textContent=preview?"只预览本地版本，不发送。"+(value.source_occurred_at?" 来源时间："+new Date(value.source_occurred_at*1000).toLocaleString()+" · "+(value.source_disposition==="stale_event"?"原消息已陈旧":"已有入站消息"):""):"此次只尝试一次，失败不会补发。";$("send").classList.toggle("hidden",preview);$("confirmation").classList.remove("hidden");$("confirmation").scrollIntoView({behavior:"smooth"});}
function render(){
  $("workbench-title").textContent=(state.target_label||state.target)+" 工作台";
  $("status").textContent=state.account_id+" · "+(state.target_label||state.target)+" · "+(state.reading?"正在接收":"接收已停止")+(state.read_error?" · "+state.read_error:"");
  $("capabilities").textContent=state.provider==="clawbot"?"ClawBot 接口私聊 · "+state.model+" · 不依赖电脑微信，窗口可关闭或最小化":(state.private_send_enabled?(state.capabilities?.foreground?"前台/后台发送":"后台单次发送"):state.send_enabled?"逐条确认发送":state.local_drafts_enabled?"接收与本地草稿预览":"仅接收")+" · "+(state.model||"未启用模型")+" · 最终正文限单行96个BMP字符，不支持emoji · 最小化/托盘发送未开放"+(state.send_enabled&&state.blocked_reason?" · 每条发送均需核对历史状态和最终正文":"");
  $("model-notice").classList.toggle("hidden",!state.model);
  $("model-settings").classList.toggle("hidden",!state.model_switch_enabled);
  $("model-select").disabled=modelLoading||!modelCatalog.length||!state.reading||state.model_switching;
  $("model-reload").disabled=modelLoading||!state.reading;
  $("model-apply").disabled=modelLoading||!state.reading||state.model_switching||!modelCatalog.includes($("model-select").value)||$("model-select").value===state.model;
  $("model-status").textContent=modelLoading?"正在加载当前账号的模型列表…":"正在使用："+(state.model||"未启用")+(modelCatalog.length?" · "+modelCatalog.length+" 个可选模型":"");
  $("flow-note").textContent=state.auto_reply?.enabled?"自动回复已开启：所选联系人新消息会触发模型与后台回复。":"自动回复已关闭：仅手动操作会调用模型或发送。";
  $("model-notice").textContent=state.provider==="clawbot"?"仅回复扫码者的新文字与微信提供的语音转写；最多16对本会话上下文，每天最多30次。图片、文件和群消息尚未接入。":state.auto_reply?.enabled?"仅回复当前联系人；使用新消息、最多16对本会话回复和当前人格，每天最多30次。"+(state.media_enabled?" 图片与表情包交给模型看图，语音在本机转写后交给模型。":" 当前仅处理文字。"):"点击生成会将所选消息、当前人格及本会话已确认回复历史交给当前模型，只保存为草稿。";
  const modelSource=state.messages.find(m=>m.message_id===selected);
  const previousFailure=state.auto_reply?.last_delivery?.state==="FAILED"?" · 历史失败："+({draft_or_input_changed:"当时输入框非空或内容变化",background_target_became_foreground:"旧版要求后台遮挡"}[state.auto_reply.last_delivery.reason]||state.auto_reply.last_delivery.reason)+"（旧动作不补发）":"";
  $("auto-status").textContent="自动回复："+(state.auto_reply?.enabled?"已开启":"已关闭")+" · "+(state.auto_reply?.phase||"stopped")+(state.auto_reply?.last_error?" · "+(errors[state.auto_reply.last_error]||state.auto_reply.last_error):"")+previousFailure;
  $("auto-toggle").textContent=state.auto_reply?.enabled?"停止自动回复":state.provider==="clawbot"&&state.auto_reply?.requires_consent?"授权并开启自动回复":"开启自动回复";
  $("auto-toggle").disabled=!state.reading||!state.model||!(state.auto_reply_available??state.private_send_enabled);
  $("generate").classList.toggle("hidden",state.provider==="clawbot");
  $("pause").textContent=state.provider==="clawbot"?"暂停自动回复":"暂停并断开";
  const generated=state.drafts.some(d=>d.source_message_id===selected);
  $("generate").disabled=!selected||!state.reading||!state.model||(state.local_drafts_enabled&&(!["OBSERVED","stale_event"].includes(modelSource?.disposition)||modelSource?.sender_id!==state.target))||generated||state.drafts.some(d=>d.state==="RUNNING");
  $("generate").textContent=generated?"这条消息已有生成记录":"调用真实模型生成草稿";
  $("local-compose").classList.toggle("hidden",!state.local_drafts_enabled);
  const source=state.messages.find(m=>m.message_id===selected);
  $("compose").disabled=!state.local_drafts_enabled||!state.reading||generated||!["OBSERVED","stale_event"].includes(source?.disposition)||source?.sender_id!==state.target||!$("local-text").value.trim();
  if(confirmation){const current=state.drafts.find(d=>d.draft_id===confirmation.draft_id);if(!current||current.state!=="READY"||current.version!==confirmation.version||Date.now()/1000>=confirmation.expires_at){confirmation=null;$("confirmation").classList.add("hidden");}}
  const messages=$("messages");messages.replaceChildren();
  for(const m of state.messages){const row=element("article","","event");const text=m.recognized_text||m.text||({nontext:"[暂未支持的消息]"}[m.message_type])||"历史正文未保存";row.append(element("p",text,"event-text"),element("small",m.disposition==="self_echo"?"自身消息 / 回显":m.disposition==="OBSERVED"?"对方入站 / 仅观察":m.disposition==="stale_event"?(state.local_drafts_enabled?"已陈旧 / 可用于本地草稿":"已陈旧"):m.disposition,"muted"));if(m.media)row.append(element("p",m.media.state==="FAILED"?(errors[m.media.error]||m.media.error):m.media.state==="PROCESSING"?"正在本机解析媒体":"媒体已解析" ,"muted"));if(state.provider!=="clawbot"&&m.text!=null&&(m.message_type==="text"||(state.media_enabled&&["image","voice","sticker"].includes(m.message_type))))row.append(button(selected===m.message_id?"已选中":"选择这条",async()=>{selected=m.message_id;pendingGeneration=null;pendingCompose=null;$("selection").textContent=text;render();}));messages.append(row);}
  if(!state.messages.length)messages.append(element("p","尚无消息；等待基线之后的新消息。","muted"));
  const active=document.activeElement;const focusedDraft=active?.dataset?.draft;const caret=focusedDraft?[active.selectionStart,active.selectionEnd]:null;
  if(focusedDraft)edits.set(focusedDraft,active.value);
  const drafts=$("drafts");drafts.replaceChildren();
  for(const d of state.drafts){const card=element("article","","event");card.append(element("strong",d.model==="local-operator"&&d.state==="READY"?"本地草稿，等待核对":names[d.state]||d.state));
    if(d.reason)card.append(element("p",d.reason==="authentication_required"?"模型登录需要恢复；这条草稿不会自动重跑。":errors[d.reason]||d.reason,"muted"));
    if(d.model_trace)card.append(element("p",d.model_trace.inference_calls===0?"本次在模型推理开始前停止。":"已发起模型推理；请求次数："+d.model_trace.model_http_attempts,"muted"));
    if(d.rule_trace)card.append(element("p","命中规则「"+d.rule_trace.name+"」 · 未调用模型","muted"));
    if(d.text!=null){const input=document.createElement("textarea");input.rows=3;input.maxLength=2000;input.dataset.draft=d.draft_id;input.value=edits.has(d.draft_id)?edits.get(d.draft_id):d.text;input.disabled=d.state!=="READY";input.addEventListener("input",()=>{edits.set(d.draft_id,input.value);confirmation=null;$("confirmation").classList.add("hidden");});card.append(input);
      card.append(element("p","v"+d.version+(d.state==="SUBMITTED"?" · 最终正文已锁定":" · 剩余 "+Math.max(0,Math.ceil(d.expires_at-Date.now()/1000))+" 秒")+(d.send_error?" · "+(errors[d.send_error]||d.send_error):""),"muted"));
      if(d.state==="READY"){
        card.append(button("保存编辑",async()=>{const text=edits.get(d.draft_id)??d.text;await api("/api/wechat/drafts/"+d.draft_id+"/edit",{request_id:key(d.draft_id,"edit",d.version+":"+text),expected_version:d.version,text});edits.delete(d.draft_id);confirmation=null;$("confirmation").classList.add("hidden");await refresh();}));
        if(state.send_enabled&&!d.send_error)card.append(button("准备发送并核对",async()=>{if(edits.has(d.draft_id)&&edits.get(d.draft_id)!==d.text){notice("请先保存编辑后的正文。",true);return;}showReview(await api("/api/wechat/send/prepare",{request_id:key(d.draft_id,"prepare",d.version),draft_id:d.draft_id,expected_version:d.version}));}));
        if(state.local_drafts_enabled&&!d.send_error)card.append(button("预览最终正文",async()=>{if(edits.has(d.draft_id)&&edits.get(d.draft_id)!==d.text){notice("请先保存编辑后的正文。",true);return;}showReview(await api("/api/wechat/local-drafts/preview",{draft_id:d.draft_id,expected_version:d.version}));}));
        if(state.private_send_enabled&&!d.send_error)card.append(button("后台发送一次",async()=>{if(edits.has(d.draft_id)&&edits.get(d.draft_id)!==d.text){notice("请先保存编辑。",true);return;}if(!window.confirm("向 "+state.target_label+" 发送一次：\n"+d.text))return;const result=await api("/api/wechat/private-send",{request_id:key(d.draft_id,"private-send",d.version),text:d.text});await refresh();notice(result.local_echo?"已取得本机发送回显；请核对对方接收。":"发送状态："+result.state+" · "+(result.reason||"")+"；不会自动补发。");}));
        card.append(button("取消草稿",async()=>{await api("/api/wechat/drafts/"+d.draft_id+"/cancel",{});await refresh();}));
      }
    }
    if(d.delivery){card.append(element("p",names[d.delivery.state]||d.delivery.state));if(d.local_echo)card.append(element("p","本机已回显；请独立核对手机完整正文。","muted"));if(d.local_echo&&d.delivery.state==="UNCONFIRMED"&&!state.private_send_enabled)card.append(button("手机已收到完整正文",async()=>{if(!window.confirm("请确认手机对应会话已收到上方完整正文。仅本机回显不算手机确认。"))return;await api("/api/wechat/actions/"+d.action_id+"/phone-confirmation",{confirmed_complete_text:true});await refresh();}));}
    drafts.append(card);
  }
  if(focusedDraft){const input=[...drafts.querySelectorAll("textarea")].find(e=>e.dataset.draft===focusedDraft);if(input&&!input.disabled){input.focus();input.setSelectionRange(...caret);}}
  window.studioManagement?.onState(state);
  window.clawbotPanel?.onState(state);
  window.companionPanel?.onState(state);
}
async function refresh(){state=await api("/api/wechat/state");render();}
$("refresh").addEventListener("click",()=>act($("refresh"),refresh));
$("generate").addEventListener("click",()=>act($("generate"),async()=>{pendingGeneration=pendingGeneration||{request_id:crypto.randomUUID(),source_message_id:selected,expected_message_revision:state.message_revision,expected_persona_version:state.persona_version};await api("/api/wechat/drafts",pendingGeneration);await refresh();}));
$("local-text").addEventListener("input",()=>{pendingCompose=null;render();});
$("compose").addEventListener("click",()=>act($("compose"),async()=>{pendingCompose=pendingCompose||{request_id:crypto.randomUUID(),source_message_id:selected,expected_message_revision:state.message_revision,expected_persona_version:state.persona_version,text:$("local-text").value};await api("/api/wechat/local-drafts",pendingCompose);$("local-text").value="";await refresh();}));
$("send").addEventListener("click",()=>act($("send"),async()=>{if(!confirmation||confirmation.preview_only)return;pendingConfirm=pendingConfirm||{request_id:crypto.randomUUID(),confirmation_id:confirmation.confirmation_id};await api("/api/wechat/send/confirm",pendingConfirm);$("confirmation").classList.add("hidden");confirmation=null;await refresh();}));
$("dismiss").addEventListener("click",()=>{$("confirmation").classList.add("hidden");confirmation=null;});
$("pause").addEventListener("click",()=>act($("pause"),async()=>{await api("/api/wechat/pause",{expected_version:state.policy_version,paused:true});await refresh();notice(state.provider==="clawbot"?"自动回复已暂停；可以重新开启，暂停期间消息不会补回复。":"已暂停并回收；需要恢复时重新启动工作台。旧任务不会重放。");}));
$("persona-form").addEventListener("submit",e=>{e.preventDefault();act(e.submitter,async()=>{persona=await api("/api/persona",{expected_version:persona.version,name:$("name").value,style:$("style").value,notes:$("notes").value});confirmation=null;$("confirmation").classList.add("hidden");await refresh();});});
async function loadModels(){modelLoading=true;render();try{const data=await api("/api/wechat/models");modelCatalog=data.models;$("model-select").replaceChildren(...modelCatalog.map(name=>{const option=element("option",name);option.value=name;return option;}));$("model-select").value=data.selected;}finally{modelLoading=false;render();}}
$("model-select").addEventListener("change",render);
$("model-reload").addEventListener("click",()=>act($("model-reload"),loadModels));
$("model-apply").addEventListener("click",()=>act($("model-apply"),async()=>{const model=$("model-select").value;modelLoading=true;render();try{await api("/api/wechat/model",{model,expected_model:state.model});confirmation=null;pendingGeneration=null;await refresh();notice("回复模型已切换为 "+model+"。后续新消息使用新模型。");}finally{modelLoading=false;render();}}));
async function boot(){persona=await api("/api/persona");$("name").value=persona.name;$("style").value=persona.style;$("notes").value=persona.notes;await refresh();setInterval(()=>refresh().catch(e=>notice(explain(e),true)),2000);if(state.model_switch_enabled)loadModels().catch(e=>notice(explain(e),true));}
boot().catch(e=>notice(explain(e),true));

$("auto-toggle").addEventListener("click",()=>act($("auto-toggle"),async()=>{const data={enabled:!state.auto_reply?.enabled};if(state.provider==="clawbot"){data.consent=$("clawbot-consent").checked;if(data.enabled&&!data.consent){notice("请先勾选扫码者私聊的数据使用说明。",true);return;}}await api("/api/wechat/auto-reply",data);await refresh();}));

export {$, state, persona, api, act, element, notice, explain, errors, refresh, render};
