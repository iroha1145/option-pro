import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './visual-tests',
  testMatch: ['eps-chart-retry.spec.mjs'],
  outputDir: './test-results/eps-chart-retry',
  workers: 1,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  reporter: [['list']],
  webServer: {
    command: 'VITE_API_MODE=mock node node_modules/vite/bin/vite.js build --outDir test-results/eps-chart-build && node visual-tests/support/eps-chart-server.mjs',
    url: 'http://127.0.0.1:3027',
    reuseExistingServer: false,
    timeout: 120_000,
  },
  use: {
    baseURL: 'http://127.0.0.1:3027',
    viewport: { width: 1440, height: 900 },
    locale: 'zh-CN',
    reducedMotion: 'reduce',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
});
