import {spawnSync} from "node:child_process";
import path from "node:path";
import {source,files} from "./layout.mjs";

for(const file of await files(source)) {
  if(!file.endsWith(".js")) continue;
  const result=spawnSync(process.execPath,["--check",path.join(source,file)],{stdio:"inherit"});
  if(result.status!==0) process.exit(result.status||1);
}
console.log("Frontend module syntax passed.");
