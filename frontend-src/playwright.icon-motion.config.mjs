import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './visual-tests',
  testMatch: 'icon-motion.spec.mjs',
  outputDir: './test-results/icon-motion',
  workers: 1,
  timeout: 30_000,
  reporter: [['list']],
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 3192 --strictPort',
    url: 'http://127.0.0.1:3192',
    reuseExistingServer: false,
    env: { VITE_API_MODE: 'mock', OPTIX_API_PROXY: 'http://127.0.0.1:9' },
  },
  use: {
    baseURL: 'http://127.0.0.1:3192',
    viewport: { width: 1440, height: 1000 },
    locale: 'zh-CN',
    reducedMotion: 'no-preference',
    actionTimeout: 10_000,
    launchOptions: process.env.OPTIX_PLAYWRIGHT_EXECUTABLE_PATH
      ? { executablePath: process.env.OPTIX_PLAYWRIGHT_EXECUTABLE_PATH } : undefined,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
});
