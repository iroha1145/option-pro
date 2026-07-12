/* 主题切换按钮:注入应用顶栏,切换 夜间观测台 / 白天清新 并同步 meta theme-color。 */
(function () {
  var THEME_COLORS = { light: "#f5f7fa", dark: "#10141c" };

  function currentTheme() {
    return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
  }

  function syncMetaColor() {
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", THEME_COLORS[currentTheme()]);
  }

  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    try { window.localStorage.setItem("optix.theme", theme); } catch (error) { /* 私隐模式等场景忽略 */ }
    syncMetaColor();
  }

  function buildButton() {
    var button = document.createElement("button");
    button.type = "button";
    button.className = "theme-toggle-v4";
    button.setAttribute("aria-label", "切换白天 / 夜间主题");
    button.innerHTML =
      '<svg class="tt-moon" viewBox="0 0 24 24" aria-hidden="true"><path d="M20.5 14.5A8.5 8.5 0 0 1 9.5 3.5a8.5 8.5 0 1 0 11 11Z"/></svg>' +
      '<svg class="tt-sun" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4.4"/><path d="M12 2.5v2.4M12 19.1v2.4M2.5 12h2.4M19.1 12h2.4M5.2 5.2l1.7 1.7M17.1 17.1l1.7 1.7M18.8 5.2l-1.7 1.7M6.9 17.1l-1.7 1.7"/></svg>';
    button.addEventListener("click", function () {
      applyTheme(currentTheme() === "dark" ? "light" : "dark");
    });
    return button;
  }

  function mount() {
    var inner = document.querySelector(".app-header__inner");
    if (!inner || inner.querySelector(".theme-toggle-v4")) return;
    var anchor = document.getElementById("sidebar-toggle");
    var button = buildButton();
    if (anchor && anchor.parentNode === inner) {
      inner.insertBefore(button, anchor);
    } else {
      inner.appendChild(button);
    }
    syncMetaColor();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }
})();
