/* 阻塞脚本：在首屏 CSS/React 之前贴上 html.dark，避免夜间模式闪白。
   与 src/lib/themePreference.ts 共用 optix_theme 键与解析规则。 */
(() => {
  let preference = null;
  try { preference = localStorage.getItem("optix_theme"); } catch { /* private mode */ }
  const dark = preference === "dark" || (preference !== "light" && matchMedia("(prefers-color-scheme: dark)").matches);
  const root = document.documentElement;
  root.classList.toggle("dark", dark);
  root.dataset.theme = dark ? "dark" : "light";
  root.style.colorScheme = dark ? "dark" : "light";
  var themeColor = document.querySelector('meta[name="theme-color"]');
  if (themeColor) themeColor.setAttribute("content", dark ? "#191B20" : "#F6F7F9");

  /* 在主包下载/解析期间发出同源 GET，身份与默认新闻首屏不再串在 React 挂载之后。
     不用可选链，保持与旧浏览器解析约定一致。响应只给同页 requestRaw 消费一次。 */
  try {
    var bag = Object.create(null);
    bag["/api/access/status"] = fetch("/api/access/status", { credentials: "include", redirect: "error" });
    var path = location.pathname;
    if ((path === "/catalysts" || path === "/catalysts/") && !location.search) {
      var feedUrl = "/api/catalysts/feed?window_hours=72&include_unanalyzed=true&include_neutral=true&limit=12";
      bag[feedUrl] = fetch(feedUrl, { credentials: "include", redirect: "error" });
    }
    window.__OPTIX_PREFETCH__ = bag;
  } catch (e) { /* fetch 不可用时主包仍走原请求 */ }
})();
