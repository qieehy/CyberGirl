import path from "node:path";
import {readFile,writeFile,mkdir,copyFile} from "node:fs/promises";
import {createHash} from "node:crypto";
import {fileURLToPath} from "node:url";
import {frontend,workspace,source,sourceRoutes,mime} from "./layout.mjs";

export async function build() {
  const outputs=[path.join(frontend,"dist"),path.join(workspace,"backend/src/wechat_agent/assets/web")];
  const routes=await sourceRoutes(), manifest={version:1,files:{}};
  for(const [url,file] of routes) {
    const bytes=await readFile(path.join(source,file));
    manifest.files[url]={file,type:mime(file),sha256:createHash("sha256").update(bytes).digest("hex")};
    for(const out of outputs) {
      const dest=path.join(out,file);
      await mkdir(path.dirname(dest),{recursive:true});
      await copyFile(path.join(source,file),dest);
    }
  }
  for(const out of outputs) {
    await mkdir(out,{recursive:true});
    await writeFile(path.join(out,"manifest.json"),JSON.stringify(manifest,null,2)+"\n");
  }
  console.log(`Built ${routes.size} routes to frontend/dist and backend packaged web assets.`);
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)) await build();
