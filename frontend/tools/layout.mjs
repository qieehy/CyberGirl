import {fileURLToPath} from "node:url";
import path from "node:path";
import {readdir} from "node:fs/promises";

export const frontend=path.resolve(path.dirname(fileURLToPath(import.meta.url)),"..");
export const workspace=path.resolve(frontend,"..");
export const source=path.join(frontend,"src");
export const pages={"/workbench":"pages/workbench.html","/sandbox":"pages/sandbox.html","/history":"pages/history.html"};
export const mime=file=>({".html":"text/html; charset=utf-8",".js":"text/javascript; charset=utf-8",".css":"text/css; charset=utf-8",".svg":"image/svg+xml",".png":"image/png",".jpg":"image/jpeg"}[path.extname(file)]||"application/octet-stream");

export async function files(root) {
  const result=[];
  async function walk(directory) {
    for(const item of await readdir(directory,{withFileTypes:true})) {
      if(item.isSymbolicLink()) throw new Error("Source symlinks are not supported");
      const absolute=path.join(directory,item.name);
      if(item.isDirectory()) await walk(absolute);
      else if(item.isFile()) result.push(path.relative(root,absolute).split(path.sep).join("/"));
    }
  }
  await walk(root);
  return result.sort();
}

export async function sourceRoutes() {
  const routes=new Map(Object.entries(pages));
  for(const file of await files(source)) if(!file.startsWith("pages/")) routes.set("/assets/"+file,file);
  return routes;
}
