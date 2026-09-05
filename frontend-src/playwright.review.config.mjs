import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './visual-tests',
  testMatch: ['ui-review.spec.mjs', 'options-redesign.spec.mjs', 'feedback-layout.spec.mjs', 'overlay-behavior.spec.mjs', 'smart-drawings.spec.mjs', 'screener-tooltips.spec.mjs'],
  outputDir: './test-results/review',
  workers: 1,
  timeout: 30_000,
  expect: { timeout: 8_000 },
  reporter: [['list'], ['html', { outputFolder: './test-results/review-report', open: 'never' }]],
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 3020 --strictPort',
    url: 'http://127.0.0.1:3020',
    reuseExistingServer: false,
    env: { VITE_API_MODE: 'mock', OPTIX_API_PROXY: 'http://127.0.0.1:9' },
  },
  use: {
    baseURL: 'http://127.0.0.1:3020',
    viewport: { width: 1440, height: 900 },
    locale: 'zh-CN',
    contextOptions: { reducedMotion: 'reduce' },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
});
