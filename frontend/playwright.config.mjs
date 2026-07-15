import { defineConfig } from "@playwright/test";
import { existsSync } from "node:fs";

const visualBaseURL = process.env.OPTIX_VISUAL_BASE_URL || "http://127.0.0.1:8767";
const passwordBaseURL = process.env.OPTIX_PASSWORD_BASE_URL || "https://127.0.0.1:8768";
const webServers = [];
if (!process.env.OPTIX_VISUAL_BASE_URL) {
  webServers.push({
    command: "python3 -m http.server 8767 --directory .",
    url: visualBaseURL,
    reuseExistingServer: true,
    timeout: 15_000,
  });
}
if (!process.env.OPTIX_PASSWORD_BASE_URL) {
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
    baseURL: visualBaseURL,
    browserName: "chromium",
    launchOptions: process.env.OPTIX_PLAYWRIGHT_EXECUTABLE_PATH
      ? { executablePath: process.env.OPTIX_PLAYWRIGHT_EXECUTABLE_PATH }
      : undefined,
    locale: "zh-CN",
    timezoneId: "America/New_York",
    reducedMotion: "reduce",
    ignoreHTTPSErrors: true,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
