import {api} from "../shared/api.js";
"use strict";
const $=id=>document.getElementById(id);
let record=null;
let selectedRead=null;
const errors={read_manager_in_use:"同一账号已有另一服务管理持续读取，请先关闭原服务。",subscription_limit:"最多加入20个持续读取会话。",group_history_scope_mismatch:"账号或会话身份不符，已拒绝更新。",group_history_not_cached:"尚无保存记录，请点击从本地库更新。",group_name_not_found:"未找到对应会话，请核对完整名称。",group_name_ambiguous:"存在同名会话，无法唯一确定，未读取消息。",invalid_group_history_request:"请输入完整群名和1至200的数量。",group_history_unavailable:"无法更新：请确认原账号微信进程正在运行。",group_history_read_failed:"本地库读取未完成，已保存记录仍可查看。",authentication_required:"请使用终端提供的完整工作台链接。"};
function add(tag,text,cls=""){const e=document.createElement(tag);e.textContent=text;e.className=cls;return e;}
async function load(refresh){
  selectedRead=null;
  const group=$("group").value.trim(),limit=Number($("limit").value);
  if(!group||!Number.isInteger(limit)||limit<1||limit>200){$("history-status").textContent=errors.invalid_group_history_request;return;}
  $("load").disabled=$("update").disabled=true;
  $("history-status").textContent=refresh?"正在从本地库读取…":"正在打开保存记录…";
  try{
    const data=await api("/api/wechat/group-history",{group,limit,refresh});
    display(data);
  }catch(e){$("history-status").textContent=errors[e.message]||"读取未完成："+e.message;}
  finally{$("load").disabled=$("update").disabled=false;}
}
function display(data){
    record=data;$("history-messages").replaceChildren();
    for(const m of data.messages){const row=add("article","","event");row.append(add("small",m.number+" · "+m.time+" · "+m.sender,"muted"),add("p",m.text??"[无法解码："+m.decode_error+"]","event-text"));$("history-messages").append(row);}
    $("history-status").textContent=data.group+" · "+data.count+"条 · "+(data.cached?"已保存记录":"已从微信本地库更新")+(data.messages.length?" · 消息范围 "+data.messages[0].time+" 至 "+data.messages[data.messages.length-1].time:"")+" · 保存于 "+new Date(data.cached_at*1000).toLocaleString()+(data.refresh_error?"；更新未完成，显示保存记录（"+(errors[data.refresh_error]||data.refresh_error)+"）":"");
    $("export-json").disabled=$("export-text").disabled=false;
}
function download(kind){if(!record)return;const text=kind==="json"?JSON.stringify(record,null,2):record.messages.map(m=>m.number+" · "+m.time+" · "+m.sender+"\n"+(m.text??"[无法解码]")).join("\n\n");const url=URL.createObjectURL(new Blob([text],{type:kind==="json"?"application/json;charset=utf-8":"text/plain;charset=utf-8"}));const a=document.createElement("a");a.href=url;a.download=record.group+"-最近"+record.count+"条."+(kind==="json"?"json":"txt");a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
$("history-form").addEventListener("submit",e=>{e.preventDefault();load(false);});
$("update").addEventListener("click",()=>load(true));
$("export-json").addEventListener("click",()=>download("json"));
$("export-text").addEventListener("click",()=>download("text"));
load(false);


async function refreshReads(){
  try{
    const data=await api("/api/wechat/reads");
    $("reads-list").replaceChildren();
    for(const item of data.subscriptions){
      const row=add("article","","event");
      const phase=item.enabled?(item.phase||"等待读取"):"已暂停";
      row.append(add("strong",(item.kind==="group"?"群聊 · ":"私聊 · ")+item.name),add("p",phase+(item.last_success?" · 最近成功 "+new Date(item.last_success*1000).toLocaleString():"")+(item.last_error?" · "+item.last_error:"")));
      for(const [label,action] of [["查看",async()=>{selectedRead=item.id;display(await api("/api/wechat/reads/messages",{id:item.id}));}],[item.enabled?"暂停":"恢复",async()=>{await api("/api/wechat/reads/control",{id:item.id,enabled:!item.enabled,remove:false});}],["移除",async()=>{await api("/api/wechat/reads/control",{id:item.id,enabled:false,remove:true});if(selectedRead===item.id)selectedRead=null;}]]){
        const button=add("button",label,"button outline");button.type="button";
        button.addEventListener("click",async()=>{button.disabled=true;try{await action();await refreshReads();}catch(e){$("reads-status").textContent=errors[e.message]||e.message;}finally{button.disabled=false;}});row.append(button);
      }
      $("reads-list").append(row);
    }
    if(selectedRead&&data.subscriptions.some(i=>i.id===selectedRead))display(await api("/api/wechat/reads/messages",{id:selectedRead}));
  }catch(e){$("reads-status").textContent=errors[e.message]||e.message;}
}
$("reads-form").addEventListener("submit",async e=>{
  e.preventDefault();$("read-add").disabled=true;$("reads-status").textContent="正在核验会话并读取…";
  try{await api("/api/wechat/reads/add",{kind:$("read-kind").value,name:$("read-name").value.trim()});$("reads-status").textContent="已加入持续读取";await refreshReads();}
  catch(error){$("reads-status").textContent=errors[error.message]||error.message;}
  finally{$("read-add").disabled=false;}
});
refreshReads();
setInterval(()=>{if(!document.hidden)refreshReads();},5000);
