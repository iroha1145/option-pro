import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './visual-tests', testMatch: ['realtime-quotes.spec.mjs', 'watchlist-management.spec.mjs'],
  outputDir: './test-results/quotes', workers: 1, timeout: 30_000,
  expect: { timeout: 8_000 }, reporter: [['list']],
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 3027 --strictPort',
    url: 'http://127.0.0.1:3027', reuseExistingServer: false,
    env: { VITE_API_MODE: 'live', OPTIX_API_PROXY: 'http://127.0.0.1:9' },
  },
  use: { baseURL: 'http://127.0.0.1:3027', viewport: { width: 1440, height: 900 }, locale: 'zh-CN', trace: 'retain-on-failure', screenshot: 'only-on-failure' },
});
