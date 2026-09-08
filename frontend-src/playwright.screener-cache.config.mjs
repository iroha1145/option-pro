import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './visual-tests',
  testMatch: ['screener-http-cache.spec.mjs'],
  outputDir: './test-results/screener-cache',
  workers: 1,
  timeout: 30_000,
  expect: { timeout: 8_000 },
  reporter: [['list']],
  use: {
    locale: 'zh-CN',
    contextOptions: { reducedMotion: 'reduce' },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
});
