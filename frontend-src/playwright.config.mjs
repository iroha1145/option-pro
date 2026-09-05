// 浏览器测试配置（自旧 frontend/playwright.config.mjs 移植，适配 React SPA 构建产物）。
// 两个本地服务：
//   1) visual（8767）：python http.server 直接服务仓库 ../frontend（Vite 构建产物）。
//      http.server 不支持 SPA 回退，因此 visual 取证用例只用 goto 访问 /，
//      深层路由（/catalysts 等）通过应用内导航（BrowserRouter 客户端路由）到达；
//      当 OPTIX_VISUAL_BASE_URL 指向真实后端（带 SPA 回退网关）时可直接 goto 深层路由。
//   2) password（8768）：visual-tests/support/password_server.py 以口令模式起真实后端
//      （HTTPS + 自签名证书），FRONTEND_DIR 指向 ../frontend，验证登录/登出全流程。
import { defineConfig } from "@playwright/test";
import { existsSync } from "node:fs";

const visualBaseURL = process.env.OPTIX_VISUAL_BASE_URL || "http://127.0.0.1:8767";
const passwordBaseURL = process.env.OPTIX_PASSWORD_BASE_URL || "https://127.0.0.1:8768";
const webServers = [];
if (!process.env.OPTIX_VISUAL_BASE_URL) {
  webServers.push({
    // 服务仓库级构建产物目录（frontend-src/dist 的提交副本）
    command: "python3 -m http.server 8767 --directory ../frontend",
    url: visualBaseURL,
    reuseExistingServer: true,
    timeout: 15_000,
  });
}
if (!process.env.OPTIX_PASSWORD_BASE_URL) {
  // 优先用仓库 .venv 的 Python（含后端依赖），否则回退系统 python3
  const localPython = new URL("../.venv/bin/python", import.meta.url);
  const pythonExecutable = process.env.OPTIX_PYTHON_EXECUTABLE
    || (existsSync(localPython) ? localPython.pathname : "python3");
  webServers.push({
    command: `${JSON.stringify(pythonExecutable)} visual-tests/support/password_server.py --port 8768`,
    url: `${passwordBaseURL}/health`,
    reuseExistingServer: false,
    timeout: 30_000,
    ignoreHTTPSErrors: true,
    env: {
      OPTIX_TEST_OWNER_PASSWORD: "optix-browser-test-password-2026",
    },
  });
}

export default defineConfig({
  testDir: "./visual-tests",
  // This suite uses isolated demo data and a Vite-only component harness.
  testIgnore: ["ui-review.spec.mjs", "options-redesign.spec.mjs", "feedback-layout.spec.mjs", "overlay-behavior.spec.mjs", "smart-drawings.spec.mjs"],
  outputDir: "./test-results/playwright",
  timeout: 45_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [
    ["list"],
    ["html", { outputFolder: "./test-results/report", open: "never" }],
  ],
  webServer: webServers.length ? webServers : undefined,
  use: {
    /* 动作级兜底：默认 actionTimeout=0 是无界等待，一次 isVisible→click 的采样
       竞态就能把整条用例吊满（chart-drawings 重试横幅事故，动作流实测 91.6s）。 */
    actionTimeout: 15_000,
    baseURL: visualBaseURL,
    browserName: "chromium",
    launchOptions: process.env.OPTIX_PLAYWRIGHT_EXECUTABLE_PATH
      ? { executablePath: process.env.OPTIX_PLAYWRIGHT_EXECUTABLE_PATH }
      : undefined,
    locale: "zh-CN",
    timezoneId: "America/New_York",
    contextOptions: { reducedMotion: "reduce" },
    ignoreHTTPSErrors: true,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
