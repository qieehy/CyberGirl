/** Shared same-origin JSON API; mutations have exactly one transport attempt. */
export class ApiError extends Error {
  constructor(code, status=0) { super(code); this.code=code; this.status=status; }
}

export function readToken(locationValue=globalThis.location, storage=globalThis.sessionStorage, historyValue=globalThis.history) {
  if (!locationValue) return "";
  let stored="";
  try { stored=storage?.getItem("studioToken")||""; } catch {}
  const token=new URLSearchParams(locationValue.hash?.slice(1)||"").get("auth")||stored;
  try { if(token) storage?.setItem("studioToken",token); } catch {}
  if(locationValue.hash) historyValue?.replaceState(null,"",locationValue.pathname);
  return token;
}

export function createApiClient({token="",fetchImpl=globalThis.fetch}={}) {
  return async function request(path,body) {
    if(typeof path!=="string"||!path.startsWith("/api/")||path.includes("\\")||path.includes("#")) throw new ApiError("invalid_api_path");
    let response;
    try {
      response=await fetchImpl(path,{method:body===undefined?"GET":"POST",headers:{Authorization:"Bearer "+token,...(body===undefined?{}:{"Content-Type":"application/json"})},...(body===undefined?{}:{body:JSON.stringify(body)}),cache:"no-store"});
    } catch { throw new ApiError("connection_failure"); }
    let result;
    try { result=await response.json(); } catch { throw new ApiError("invalid_api_response",response.status); }
    if(!response.ok) throw new ApiError(result.error||"request_failed",response.status);
    return result;
  };
}

export const api=createApiClient({token:readToken()});
