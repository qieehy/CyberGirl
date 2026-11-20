import {test} from "node:test";
import assert from "node:assert/strict";
import {createApiClient,readToken} from "../src/shared/api.js";

test("fragment auth is stored and removed from browser URL",()=>{
  const saved=[],replaced=[];
  const token=readToken({hash:"#auth=fixture-token",pathname:"/workbench"},{getItem:()=>null,setItem:(...args)=>saved.push(args)},{replaceState:(...args)=>replaced.push(args)});
  assert.equal(token,"fixture-token");assert.deepEqual(saved,[["studioToken","fixture-token"]]);assert.equal(replaced[0][2],"/workbench");
});

test("POST carries bearer JSON and is never retried",async()=>{
  const calls=[];
  const api=createApiClient({token:"fixture",fetchImpl:async(...args)=>{calls.push(args);return {ok:false,status:409,json:async()=>({error:"version_conflict"})};}});
  await assert.rejects(api("/api/wechat/companion/config",{expected_revision:0}),e=>e.code==="version_conflict"&&e.status===409);
  assert.equal(calls.length,1);assert.equal(calls[0][1].headers.Authorization,"Bearer fixture");assert.equal(calls[0][1].method,"POST");
});

test("unavailable network and non-API paths are explicit failures",async()=>{
  let calls=0;
  const api=createApiClient({fetchImpl:async()=>{calls++;throw Error("fixture");}});
  await assert.rejects(api("https://external.invalid"),e=>e.code==="invalid_api_path");
  assert.equal(calls,0);
  await assert.rejects(api("/api/wechat/state"),e=>e.code==="connection_failure");assert.equal(calls,1);
});
