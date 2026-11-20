/** Loopback development server: frontend source + a bounded same-origin API proxy. */
import http from "node:http";
import path from "node:path";
import {readFile} from "node:fs/promises";
import {fileURLToPath} from "node:url";
import {source,sourceRoutes,mime} from "./layout.mjs";

export async function startDevServer({port=5173,backend="http://127.0.0.1:8765"}={}) {
  const upstream=new URL(backend);
  if(upstream.protocol!=="http:"||upstream.hostname!=="127.0.0.1"||upstream.username||upstream.password||upstream.pathname!=="/"||upstream.search||upstream.hash) throw new Error("Backend must be a loopback HTTP origin");
  if(!Number.isInteger(port)||port<0||port>65535) throw new Error("Invalid frontend port");
  const routes=await sourceRoutes();
  routes.set("/","pages/workbench.html");
  let origin;
  const server=http.createServer(async(req,res)=>{
    res.setHeader("Cache-Control","no-store");
    res.setHeader("X-Content-Type-Options","nosniff");
    res.setHeader("Referrer-Policy","no-referrer");
    res.setHeader("Content-Security-Policy","default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'");
    if(req.headers.host!==origin.slice(7)||(req.headers.origin&&req.headers.origin!==origin)) {
      res.writeHead(403,{"Content-Type":"application/json"});res.end('{"error":"invalid_origin"}');return;
    }
    const url=new URL(req.url,origin);
    if(url.pathname.startsWith("/api/")) {
      if(!["GET","POST"].includes(req.method)||(req.method==="POST"&&req.headers.origin!==origin)) {res.writeHead(403);res.end();return;}
      if(Number(req.headers["content-length"]||0)>32768||req.headers["transfer-encoding"]) {res.writeHead(413);res.end();return;}
      const headers={host:upstream.host,...(req.headers.authorization?{authorization:req.headers.authorization}:{}),...(req.headers["content-type"]?{"content-type":req.headers["content-type"]}:{}),...(req.headers["content-length"]?{"content-length":req.headers["content-length"]}:{})};
      if(req.headers.origin) headers.origin=upstream.origin;
      const proxy=http.request(new URL(url.pathname+url.search,upstream),{method:req.method,headers,timeout:130000},response=>{
        res.writeHead(response.statusCode,{"Content-Type":response.headers["content-type"]||"application/json"});
        response.pipe(res);
      });
      proxy.on("timeout",()=>proxy.destroy());
      proxy.on("error",()=>{if(!res.headersSent)res.writeHead(502,{"Content-Type":"application/json"});res.end('{"error":"backend_unavailable"}');});
      res.on("close",()=>{if(!res.writableFinished)proxy.destroy();});
      req.pipe(proxy);return;
    }
    const file=routes.get(url.pathname);
    if(req.method!=="GET"||!file) {res.writeHead(404);res.end();return;}
    try {const bytes=await readFile(path.join(source,file));res.writeHead(200,{"Content-Type":mime(file)});res.end(bytes);}
    catch {res.writeHead(500);res.end("Frontend source unavailable");}
  });
  await new Promise((resolve,reject)=>{server.once("error",reject);server.listen(port,"127.0.0.1",resolve);});
  origin="http://127.0.0.1:"+server.address().port;
  if(origin===upstream.origin) {await new Promise(resolve=>server.close(resolve));throw new Error("Frontend cannot proxy itself");}
  return {server,origin,backend:upstream.origin};
}

if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)) {
  const args=process.argv.slice(2), allowed=new Set(["--port","--backend"]), values={};
  for(let i=0;i<args.length;i+=2) {if(!allowed.has(args[i])||!args[i+1])throw new Error("Use --port <number> --backend <loopback-origin>");values[args[i]]=args[i+1];}
  const {server,origin,backend}=await startDevServer({port:Number(values["--port"]||5173),backend:values["--backend"]||process.env.WECHAT_AGENT_API_ORIGIN||"http://127.0.0.1:8765"});
  console.log(`Frontend: ${origin}/\nSandbox: ${origin}/sandbox\nAPI proxy: ${backend}\nUse the backend's #auth fragment on this frontend URL. Refresh after source changes.`);
  for(const signal of ["SIGINT","SIGTERM"])process.on(signal,()=>server.close(()=>process.exit(0)));
}
