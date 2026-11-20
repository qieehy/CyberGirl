// Exercise actual page handlers with a minimal DOM and a synthetic HTTP boundary.
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
class Element {
  constructor(tag = "div") {
    this.tag = tag; this.children = []; this.listeners = {}; this.dataset = {};
    this.value = ""; this.textContent = ""; this.className = ""; this.disabled = false;
    this.classList = {
      add: c => { if (!this.className.split(" ").includes(c)) this.className += " " + c; },
      remove: c => { this.className = this.className.split(" ").filter(x => x !== c).join(" "); },
      toggle: (c, hidden) => hidden ? this.classList.add(c) : this.classList.remove(c),
      contains: c => this.className.split(" ").includes(c)
    };
  }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(type, handler) { this.listeners[type] = handler; }
  async click() { await this.listeners.click?.({}); }
  scrollIntoView() {}
  querySelectorAll(tag) { return this.children.flatMap(c => [c, ...c.querySelectorAll(tag)]).filter(c => c.tag === tag); }
}
const html = fs.readFileSync(process.argv[3], "utf8");
const elements = Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(m => [m[1], new Element()]));
const now = Date.now() / 1000;
const state = {account_id: "fixture-account", target: "wxid_fixture", target_label: "Amadues fixture",
  reading: true, model: null, send_enabled: false, local_drafts_enabled: true, message_revision: 1,
  persona_version: 1, drafts: [], messages: [{message_id: "source", text: "fixture inbound", message_type: "text",
    sender_id: "wxid_fixture", disposition: "stale_event", occurred_at: now - 3600, received_at: now - 3600}]};
const requests = [];
async function fetch(path, options) {
  requests.push([path, options.method]);
  const body = options.body && JSON.parse(options.body);
  let result;
  if (path === "/api/persona") result = {version: 1, name: "fixture", style: "clear", notes: ""};
  else if (path === "/api/wechat/state") result = structuredClone(state);
  else if (path === "/api/wechat/local-drafts") {
    assert.equal(body.source_message_id, "source");
    state.drafts = [{draft_id: "local", source_message_id: "source", model: "local-operator", state: "READY",
      version: 1, text: body.text, expires_at: now + 120, send_error: null}];
    result = state.drafts[0];
  } else if (path === "/api/wechat/local-drafts/preview") {
    assert.equal(body.expected_version, state.drafts[0].version);
    result = {...state.drafts[0], target: state.target, preview_only: true, send_enabled: false};
  } else if (path === "/api/wechat/drafts/local/edit") {
    state.drafts[0].version++; state.drafts[0].text = body.text; result = state.drafts[0];
  } else throw Error("Unexpected effect endpoint: " + path);
  return {ok: true, json: async () => result};
}
const context = vm.createContext({document: {getElementById: id => {
    assert.ok(elements[id], "Missing HTML element " + id); return elements[id];
  }, createElement: tag => new Element(tag), activeElement: null},
  location: {hash: "", pathname: "/"}, history: {}, sessionStorage: {getItem() {return "fixture-token";}, setItem() {}},
  URLSearchParams, Date, Map, crypto: require("node:crypto").webcrypto, fetch, setInterval() {},
  window: {confirm() {throw Error("Unexpected delivery confirmation");}}});
const path = require("node:path");
const modules = new Map();
async function loadModule(filename) {
  filename=path.resolve(filename);
  if(modules.has(filename)) return modules.get(filename);
  const module=new vm.SourceTextModule(fs.readFileSync(filename,"utf8"),{context,identifier:filename});
  modules.set(filename,module);
  await module.link((specifier,parent)=>loadModule(path.resolve(path.dirname(parent.identifier),specifier)));
  return module;
}
async function flush() { await new Promise(resolve => setImmediate(resolve)); }
(async () => {
  await (await loadModule(process.argv[2])).evaluate();
  await flush();
  assert.equal(elements["workbench-title"].textContent, "Amadues fixture 工作台");
  await elements.messages.children[0].children.find(e => e.tag === "button").click();
  elements["local-text"].value = "Local operator text";
  elements["local-text"].listeners.input();
  assert.equal(elements.compose.disabled, false);
  await elements.compose.click();
  assert.equal(elements.compose.disabled, true); // source consumed after save
  const previewButton = () => elements.drafts.children[0].children.find(e => e.textContent === "预览最终正文");
  await previewButton().click();
  assert.equal(elements["confirm-text"].textContent, "Local operator text");
  assert.ok(elements.send.classList.contains("hidden"));
  const beforeSendClick = requests.length;
  await elements.send.click(); // even a forced click on hidden send is inert
  assert.equal(requests.length, beforeSendClick);
  const textarea = elements.drafts.children[0].children.find(e => e.tag === "textarea");
  textarea.value = "Edited local text"; textarea.listeners.input();
  assert.ok(elements.confirmation.classList.contains("hidden"));
  await previewButton().click();
  assert.ok(elements.confirmation.classList.contains("hidden")); // unsaved edit cannot preview
  await elements.drafts.children[0].children.find(e => e.textContent === "保存编辑").click();
  await previewButton().click();
  assert.equal(elements["confirm-text"].textContent, "Edited local text");
  state.drafts[0].state = "CANCELLED";
  await elements.refresh.click();
  assert.ok(elements.confirmation.classList.contains("hidden"));
  assert.ok(!requests.some(([path]) => path.startsWith("/api/wechat/send/") || path === "/api/wechat/drafts"));
  console.log("local compose/edit/preview handlers passed; model/send requests=0");
})().catch(error => {console.error(error); process.exitCode = 1;});
