import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './visual-tests', testMatch: 'indicator-panes.spec.mjs',
  outputDir: './test-results/indicators', workers: 1, timeout: 30_000,
  expect: { timeout: 8_000 }, reporter: [['list']],
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 3032 --strictPort',
    url: 'http://127.0.0.1:3032', reuseExistingServer: false,
    env: { VITE_API_MODE: 'live', OPTIX_API_PROXY: 'http://127.0.0.1:9' },
  },
  use: { baseURL: 'http://127.0.0.1:3032', locale: 'zh-CN', timezoneId: 'America/New_York',
    viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce',
    launchOptions: process.env.OPTIX_PLAYWRIGHT_EXECUTABLE_PATH ? { executablePath: process.env.OPTIX_PLAYWRIGHT_EXECUTABLE_PATH } : undefined,
    screenshot: 'only-on-failure', trace: 'retain-on-failure',
  },
});
