/**
 * Turning a project into one page that can run on the phone.
 *
 * A web project's index.html is inlined - its local stylesheets and scripts are pasted in -
 * because the preview has no file server to fetch "style.css" from. A lone JavaScript file runs
 * inside an empty page. Either way console output and errors are forwarded back to the app.
 */
const BRIDGE = `<script>(function(){
  var send=function(kind,args){try{var msg=JSON.stringify({kind:kind,text:Array.prototype.map.call(args,function(a){
    if(a instanceof Error)return a.stack||a.message;if(typeof a==="object"){try{return JSON.stringify(a,null,2)}catch(e){return String(a)}}return String(a)}).join(" ")});
    if(window.ReactNativeWebView)window.ReactNativeWebView.postMessage(msg);else if(window.parent!==window)window.parent.postMessage(msg,"*");}catch(e){}};
  ["log","info","warn","error","debug"].forEach(function(k){var o=console[k];console[k]=function(){send(k,arguments);if(o)o.apply(console,arguments)}});
  window.addEventListener("error",function(e){var n=e.lineno?e.lineno-(window.__lineOffset||0):0;send("error",[e.message+(n>0?" (line "+n+")":"")])});
  window.addEventListener("unhandledrejection",function(e){send("error",["Unhandled promise: "+(e.reason&&e.reason.message||e.reason)])});
})();</script>`;

const escapeScript = (code) => String(code).replace(/<\/script/gi, "<\\/script");

/** What Run does for this project, or null when there is nothing it can run on a phone. */
export function runPlan(files, active) {
  const paths = files.filter(f => !f.dir).map(f => f.path);
  if (paths.includes("index.html")) return { kind: "web", entry: "index.html" };
  const html = paths.find(p => /\.html?$/i.test(p));
  if (active && /\.html?$/i.test(active)) return { kind: "web", entry: active };
  if (active && /\.m?js$/i.test(active)) return { kind: "js", entry: active };
  if (html) return { kind: "web", entry: html };
  const js = paths.find(p => /\.m?js$/i.test(p));
  if (js) return { kind: "js", entry: js };
  if (paths.some(p => /\.py$/i.test(p))) return { kind: "python" };
  return null;
}

/** Build the page. `texts` maps project paths to their contents. */
export function buildPage(plan, texts) {
  if (plan.kind === "js") {
    const head = `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
${BRIDGE}<style>body{font:14px ui-monospace,Menlo,monospace;background:#0b0b0c;color:#b9b4ab;padding:14px}</style></head>
<body><div>Running ${plan.entry}… output is in the console below.</div><script>`;
    // Errors report page lines. The file starts this many lines into the page, so the bridge
    // subtracts it and the line matches the editor. (Injected on the <head> line: shifts nothing.)
    const offset = head.split("\n").length - 1;
    return `${head}${escapeScript(texts[plan.entry] || "")}\n//# sourceURL=${plan.entry}</script></body></html>`
      .replace("<head>", `<head><script>window.__lineOffset=${offset};</script>`);
  }
  let html = texts[plan.entry] || "";
  const base = plan.entry.split("/").slice(0, -1).join("/");
  const resolve = (ref) => {
    const clean = ref.replace(/^\.\//, "").split(/[?#]/)[0];
    return base ? `${base}/${clean}` : clean;
  };
  html = html.replace(/<link\b[^>]*href=["']([^"']+\.css)["'][^>]*>/gi, (tag, href) => {
    const body = texts[resolve(href)];
    return body === undefined ? tag : `<style>/* ${href} */\n${body}\n</style>`;
  });
  html = html.replace(/<script\b([^>]*)src=["']([^"']+)["']([^>]*)>\s*<\/script>/gi, (tag, pre, src, post) => {
    const body = texts[resolve(src)];
    if (body === undefined) return tag;
    const type = /type=["']module["']/i.test(pre + post) ? ' type="module"' : "";
    return `<script${type}>${escapeScript(body)}\n//# sourceURL=${src}</script>`;
  });
  return /<head[^>]*>/i.test(html) ? html.replace(/<head[^>]*>/i, (h) => h + BRIDGE) : BRIDGE + html;
}
