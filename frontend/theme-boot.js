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
})();
