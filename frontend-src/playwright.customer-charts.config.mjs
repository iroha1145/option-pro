import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './visual-tests', testMatch: ['customer-chart-periods.spec.mjs'],
  outputDir: './test-results/customer-charts', workers: 1, timeout: 45_000,
  expect: { timeout: 10_000 }, reporter: [['list']],
  webServer: {
    command: 'python3 visual-tests/support/customer_chart_server.py --port 3076',
    url: 'https://127.0.0.1:3076', ignoreHTTPSErrors: true, reuseExistingServer: false,
  },
  use: {
    baseURL: 'https://127.0.0.1:3076', ignoreHTTPSErrors: true,
    channel: process.env.PLAYWRIGHT_CHROMIUM_CHANNEL || undefined,
    locale: 'zh-CN', contextOptions: { reducedMotion: 'reduce' },
    trace: 'retain-on-failure', screenshot: 'only-on-failure',
  },
});
