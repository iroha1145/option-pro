import { defineConfig } from "@playwright/test";

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
  webServer: process.env.OPTIX_VISUAL_BASE_URL ? undefined : {
    command: "python3 -m http.server 8767 --directory .",
    url: "http://127.0.0.1:8767",
    reuseExistingServer: true,
    timeout: 15_000,
  },
  use: {
    baseURL: process.env.OPTIX_VISUAL_BASE_URL || "http://127.0.0.1:8767",
    browserName: "chromium",
    locale: "zh-CN",
    timezoneId: "America/New_York",
    reducedMotion: "reduce",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
