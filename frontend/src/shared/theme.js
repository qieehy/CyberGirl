"use strict";
(() => {
  const key="cybergirl-theme", system=window.matchMedia("(prefers-color-scheme: dark)");
  let mode="system";
  try { mode=localStorage.getItem(key)||"system"; } catch {}
  if(!["light","dark","system"].includes(mode))mode="system";
  function apply(){document.documentElement.dataset.theme=mode==="system"?(system.matches?"dark":"light"):mode;}
  window.cybergirlTheme={get:()=>mode,set:value=>{if(!["light","dark","system"].includes(value))return;mode=value;try{localStorage.setItem(key,value);}catch{}apply();}};
  system.addEventListener("change",apply);apply();
})();
