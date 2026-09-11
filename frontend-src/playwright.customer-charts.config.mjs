import { defineConfig } from '@playwright/test';
import { existsSync } from 'node:fs';

const localPython = new URL('../.venv/bin/python', import.meta.url);
const pythonExecutable = process.env.OPTIX_PYTHON_EXECUTABLE
  || (existsSync(localPython) ? localPython.pathname : 'python3');

export default defineConfig({
  testDir: './visual-tests', testMatch: ['customer-chart-periods.spec.mjs'],
  outputDir: './test-results/customer-charts', workers: 1, timeout: 45_000,
  expect: { timeout: 10_000 }, reporter: [['list']],
  webServer: {
    command: `${JSON.stringify(pythonExecutable)} visual-tests/support/customer_chart_server.py --port 3076`,
    url: 'https://127.0.0.1:3076', ignoreHTTPSErrors: true, reuseExistingServer: false,
  },
  use: {
    baseURL: 'https://127.0.0.1:3076', ignoreHTTPSErrors: true,
    channel: process.env.PLAYWRIGHT_CHROMIUM_CHANNEL || undefined,
    locale: 'zh-CN', contextOptions: { reducedMotion: 'reduce' },
    trace: 'retain-on-failure', screenshot: 'only-on-failure',
  },
});
