(function () {
  "use strict";

  const form = document.getElementById("owner-login-form");
  const password = document.getElementById("owner-password");
  const submit = document.getElementById("owner-login-submit");
  const message = document.getElementById("login-message");

  async function status() {
    try {
      const response = await fetch("/api/access/status", {
        credentials: "same-origin",
        cache: "no-store",
        redirect: "error",
      });
      if (!response.ok) return;
      const body = await response.json();
      if (body.access_mode === "private_network" || body.logged_in === true) {
        location.replace("/");
      }
    } catch (error) { /* 登录表单仍可使用 */ }
  }

  form.addEventListener("submit", async event => {
    event.preventDefault();
    submit.disabled = true;
    message.textContent = "正在验证……";
    try {
      const response = await fetch("/api/access/login", {
        method: "POST",
        credentials: "same-origin",
        redirect: "error",
        headers: { "Content-Type": "application/json", "X-Optix-Action": "1" },
        body: JSON.stringify({ password: password.value }),
      });
      password.value = "";
      if (response.ok) {
        location.replace("/");
        return;
      }
      const body = await response.json().catch(() => ({}));
      const code = body && body.detail && body.detail.code;
      message.textContent = code === "login_cooldown"
        ? "连续登录失败，请稍后再试。"
        : code === "https_required"
          ? "密码模式必须通过安全的 HTTPS 地址访问。"
          : "密码不正确。";
    } catch (error) {
      password.value = "";
      message.textContent = "登录服务暂不可用。";
    } finally {
      submit.disabled = false;
      password.focus();
    }
  });

  status();
})();
