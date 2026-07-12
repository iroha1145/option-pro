/* 主题预初始化:在首帧前给 <html> 打上 data-theme,避免闪烁。
   默认按本地时段(7:00–19:00 白天),用户选择持久化在 localStorage。
   本脚本必须保持无依赖、可在 <head> 同步执行。 */
(function () {
  var theme = "light";
  try {
    var saved = window.localStorage.getItem("optix.theme");
    var hour = new Date().getHours();
    var auto = hour >= 7 && hour < 19 ? "light" : "dark";
    theme = saved === "light" || saved === "dark" ? saved : auto;
  } catch (error) {
    theme = "light";
  }
  document.documentElement.dataset.theme = theme;
})();
