const __vite__mapDeps=(i,m=__vite__mapDeps,d=(m.f||(m.f=["assets/websandbox-H7Mpmiwg.js","assets/index-D4Ce1Swf.js","assets/index-DK_x2ca9.css"])))=>i.map(i=>d[i]);
import{_ as O,j as s,t as _,r as m}from"./index-D4Ce1Swf.js";const C="__ogui_resize",A="__ogui_report_error",T=`<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; connect-src 'none'; base-uri 'none'; form-action 'none'">`,M=`
(function () {
  function failed(event) {
    if (event && event.preventDefault) event.preventDefault();
    parent.postMessage({type: '${A}'}, '*');
  }
  window.addEventListener('error', failed);
  window.addEventListener('unhandledrejection', failed);
})();`;function E(t){return`<style>${t.replace(/<\/style/gi,"<\\/style")}</style>`}function F(t,e="",n=""){return`<!DOCTYPE html><html><head>${T}<meta charset="utf-8">${E("html,body{margin:0;padding:0;min-height:0;height:auto;overflow:auto}body{display:flow-root}")}${E(n)}${E(e)}<script>${M}<\/script></head><body>${t}</body></html>`}const I=`
(function () {
  if (window.__oguiResizeInstalled) return;
  window.__oguiResizeInstalled = true;
  var style = document.createElement('style');
  style.textContent = 'html,body{height:auto!important;min-height:0!important;overflow:auto!important}';
  document.head.appendChild(style);
  function reportHeight() {
    // body.scrollHeight is floored by the iframe viewport in some browsers;
    // its auto-height box can shrink after content is removed.
    var body = document.body;
    var rect = body.getBoundingClientRect();
    var cs = getComputedStyle(body);
    var h = rect.height + (parseFloat(cs.marginTop)||0) + (parseFloat(cs.marginBottom)||0);
    for (var node of body.querySelectorAll('*')) {
      // Closed details can retain layout boxes for their hidden descendants.
      if (typeof node.checkVisibility === 'function' && !node.checkVisibility()) continue;
      var closed = node.closest('details:not([open])');
      if (closed && node !== closed && !closed.querySelector('summary')?.contains(node)) continue;
      var child = node.getBoundingClientRect();
      h = Math.max(h, child.bottom - rect.top);
    }
    parent.postMessage({type: '${C}', height: Math.ceil(h)}, '*');
  }
  var scheduled = false;
  function queueHeight() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(function () { scheduled = false; reportHeight(); });
  }
  new ResizeObserver(queueHeight).observe(document.body);
  new MutationObserver(queueHeight).observe(document.body, {subtree:true,childList:true,attributes:true,characterData:true});
  window.addEventListener('resize', queueHeight);
  window.addEventListener('load', queueHeight);
  document.addEventListener('load', queueHeight, true);
  if (document.fonts) document.fonts.ready.then(queueHeight).catch(function() {});
  queueHeight();
})();`,q=2e4;function h(t){return typeof t!="number"||!Number.isFinite(t)?null:Math.max(50,Math.min(Math.ceil(t),q))}function N(t){if(!t||typeof t!="object"||Array.isArray(t))return null;const e=t,n=o=>Array.isArray(o)&&o.length<=4096&&o.every(p=>typeof p=="string"&&p.length<=512e3);for(const o of["cssComplete","jsFunctionsComplete","jsExpressionsComplete"])if(e[o]!==void 0&&e[o]!==!0)return null;if(e.generating!==!1||e.htmlComplete!==!0||!n(e.html)||!e.html.join("").trim()||e.css!==void 0&&(typeof e.css!="string"||e.cssComplete!==!0)||e.jsFunctions!==void 0&&(typeof e.jsFunctions!="string"||e.jsFunctionsComplete!==!0)||e.jsExpressions!==void 0&&(!n(e.jsExpressions)||e.jsExpressionsComplete!==!0)||e.initialHeight!==void 0&&h(e.initialHeight)===null)return null;const a={initialHeight:h(e.initialHeight)??200,generating:!1,css:e.css??"",cssComplete:!0,html:e.html,htmlComplete:!0,jsFunctions:e.jsFunctions??"",jsFunctionsComplete:!0,jsExpressions:e.jsExpressions??[],jsExpressionsComplete:!0};return JSON.stringify(a).length<=512e3?a:null}function $(t){const e=N(t);return e?JSON.stringify(e):null}function L(t){if(!t||typeof t!="object"||Array.isArray(t))return!1;const e=t;return e.type==="__ogui_resize"?h(e.height)!==null:e.type==="__ogui_report_error"?!0:typeof e.callId=="string"&&/^\d{1,16}$/.test(e.callId)?e.type==="response"?typeof e.success=="boolean":e.type==="service-message"&&e.methodName==="iframeInitialized"&&Array.isArray(e.arguments)&&e.arguments.length===0:!1}async function z(){const t=await O(()=>import("./websandbox-H7Mpmiwg.js").then(n=>n.w),__vite__mapDeps([0,1,2])),e=t.default?.default??t.default;if(!e||typeof e.create!="function")throw new Error("Report renderer unavailable");return e}const P=15e3,J=m.memo(function({signature:e,fallback:n,title:a,themeCss:u}){const o=m.useRef(null),[p,R]=m.useState(()=>JSON.parse(e).initialHeight??200),[l,w]=m.useState("loading");return m.useEffect(()=>{const x=o.current;if(!x)return;const f=JSON.parse(e);let g=!1,y=!1,i=null;const j=()=>{const r=i;if(i=null,r)try{r.destroy()}catch{r.iframe.remove()}},b=()=>{g||y||(y=!0,clearTimeout(v),j(),w("failed"))},v=setTimeout(b,P),d=()=>!g&&!y,S=r=>{if(!(!d()||!i||r.source!==i.iframe.contentWindow)){if(!L(r.data)){r.stopImmediatePropagation();return}if(r.data?.type===A){b();return}if(r.data?.type===C){const c=h(r.data.height);c!==null&&R(c)}}};return window.addEventListener("message",S,!0),(async()=>{const r=await z();if(!d())return;i=r.create(Object.create(null),{frameContainer:x,frameContent:F(f.html.join(""),f.css,u),sandboxAdditionalAttributes:"",allowAdditionalAttributes:""}),i.iframe.title=a,i.iframe.setAttribute("loading","eager"),i.iframe.setAttribute("referrerpolicy","no-referrer"),i.iframe.style.cssText="display:block;width:100%;height:100%;border:0;background:transparent;";const c=i;if(await c.promise,!!d()&&!(f.jsFunctions&&(await c.run(f.jsFunctions),!d()))){for(const H of f.jsExpressions??[])if(await c.run(H),!d())return;await c.run(I),d()&&(clearTimeout(v),w("ready"))}})().catch(b),()=>{g=!0,clearTimeout(v),window.removeEventListener("message",S,!0),j()}},[e,u,a]),s.jsxs("div",{style:{position:"relative"},"aria-busy":l==="loading",children:[l!=="ready"&&s.jsxs(s.Fragment,{children:[l==="failed"&&s.jsx("p",{role:"status",children:_("图文展示暂时无法打开，已显示文字报告。")}),l==="loading"&&s.jsx("span",{role:"status",className:"sr-only",children:_("正在准备图文报告")}),n]}),s.jsx("div",{ref:o,"aria-hidden":l!=="ready",style:{width:"100%",height:`${p}px`,position:l==="ready"?"relative":"absolute",top:0,visibility:l==="ready"?"visible":"hidden"}})]})});function G({content:t,fallback:e,title:n=_("市场报告"),themeCss:a=""}){const u=$(t);return u===null?s.jsx(s.Fragment,{children:e}):s.jsx(J,{signature:u,fallback:e,title:n,themeCss:a},JSON.stringify([u,a,n]))}export{P as RENDER_TIMEOUT_MS,G as default};
