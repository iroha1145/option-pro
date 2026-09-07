import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './visual-tests', testMatch: 'audit-recovery.spec.mjs',
  outputDir: './test-results/audit', workers: 1, timeout: 30_000,
  expect: { timeout: 8_000 }, reporter: [['list']],
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 3145 --strictPort',
    url: 'http://127.0.0.1:3145', reuseExistingServer: false,
    env: { VITE_API_MODE: 'live', OPTIX_API_PROXY: 'http://127.0.0.1:9' },
  },
  use: { baseURL: 'http://127.0.0.1:3145', viewport: { width: 1440, height: 1000 }, locale: 'zh-CN',
    timezoneId: 'America/New_York', reducedMotion: 'reduce',
    launchOptions: process.env.OPTIX_PLAYWRIGHT_EXECUTABLE_PATH ? { executablePath: process.env.OPTIX_PLAYWRIGHT_EXECUTABLE_PATH } : undefined,
    screenshot: 'only-on-failure', trace: 'retain-on-failure' },
});
