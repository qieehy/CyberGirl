import {test} from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import {startDevServer} from "../tools/dev-server.mjs";

const close=server=>new Promise(resolve=>server.close(resolve));

test("independent frontend serves ESM and proxies authenticated API with origin isolation",async()=>{
  const received=[];
  const backend=http.createServer((req,res)=>{received.push({method:req.method,url:req.url,origin:req.headers.origin,host:req.headers.host,auth:req.headers.authorization});req.resume();res.setHeader("Content-Type","application/json");res.end('{"ok":true}');});
  await new Promise(resolve=>backend.listen(0,"127.0.0.1",resolve));
  const backendOrigin="http://127.0.0.1:"+backend.address().port;
  const {server,origin}=await startDevServer({port:0,backend:backendOrigin});
  try {
    const page=await fetch(origin+"/");assert.equal(page.status,200);assert.match(await page.text(),/type="module"/);
    const module=await fetch(origin+"/assets/shared/api.js");assert.match(module.headers.get("content-type"),/javascript/);assert.match(await module.text(),/createApiClient/);
    const reply=await fetch(origin+"/api/wechat/companion/preview",{method:"POST",headers:{Origin:origin,Authorization:"Bearer fixture","Content-Type":"application/json"},body:'{"query":"fixture"}'});
    assert.equal(reply.status,200);assert.equal(received.length,1);assert.equal(received[0].origin,backendOrigin);assert.equal(received[0].auth,"Bearer fixture");
    const denied=await fetch(origin+"/api/wechat/companion/preview",{method:"POST",headers:{Origin:"https://external.invalid"},body:"{}"});assert.equal(denied.status,403);assert.equal(received.length,1);
    assert.equal((await fetch(origin+"/.state/clawbot/session.dpapi")).status,404);
  } finally {await close(server);await close(backend);}
});

test("proxy rejects external backend origins",async()=>{
  await assert.rejects(startDevServer({port:0,backend:"https://external.invalid"}),/loopback/);
});
