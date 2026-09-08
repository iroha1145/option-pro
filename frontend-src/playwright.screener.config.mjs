import { defineConfig } from '@playwright/test';
import { existsSync } from 'node:fs';

const localPython = new URL('../.venv/bin/python', import.meta.url);
const pythonExecutable = process.env.SCREENER_FRESHNESS_PYTHON
  || process.env.OPTIX_PYTHON_EXECUTABLE
  || (existsSync(localPython) ? localPython.pathname : 'python3');

export default defineConfig({
  testDir: './visual-tests',
  testMatch: ['screener-freshness.spec.mjs'],
  outputDir: './test-results/screener',
  workers: 1,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  reporter: [['list'], ['html', { outputFolder: './test-results/screener-report', open: 'never' }]],
  webServer: [
    {
      command: `${JSON.stringify(pythonExecutable)} visual-tests/support/screener_freshness_api.py`,
      url: 'http://127.0.0.1:8765/health',
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        ...process.env,
        PYTHONPATH: '../backend',
        SCREENER_FRESHNESS_HOST: '127.0.0.1',
        SCREENER_FRESHNESS_PORT: '8765',
      },
    },
    {
      command: 'npm run preview -- --host 127.0.0.1 --port 3033 --strictPort',
      url: 'http://127.0.0.1:3033',
      reuseExistingServer: false,
      env: {
        VITE_API_MODE: 'live',
        OPTIX_API_PROXY: 'http://127.0.0.1:8765',
      },
    },
  ],
  use: {
    baseURL: 'http://127.0.0.1:3033',
    viewport: { width: 1440, height: 900 },
    locale: 'zh-CN',
    timezoneId: 'America/New_York',
    contextOptions: { reducedMotion: 'reduce' },
    trace: 'on',
    screenshot: 'only-on-failure',
  },
});
