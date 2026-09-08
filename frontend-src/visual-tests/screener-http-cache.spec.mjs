import { expect, test } from '@playwright/test';
import http from 'node:http';
import { build } from 'esbuild';
import { fileURLToPath } from 'node:url';

function listen(server) {
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => resolve(server.address().port));
  });
}

test.describe('C01 native HTTP cache without Playwright routes', () => {
  /** This file must never call page.route or context.route — those disable HTTP cache. */
  let server;
  let port;
  let apiHits = 0;

  test.beforeAll(async () => {
    const bundled = await build({
      stdin: {
        contents: "import { marketGet } from './src/api/marketRead.ts'; window.readMarket = marketGet;",
        resolveDir: fileURLToPath(new URL('../', import.meta.url)),
      },
      bundle: true,
      format: 'iife',
      platform: 'browser',
      write: false,
      define: { 'import.meta.env': '{"VITE_API_MODE":"live"}' },
    });
    server = http.createServer((request, response) => {
      const url = request.url || '/';
      if (url === '/' || url === '/index.html') {
        response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
        response.end('<!doctype html><html><body><p>screener cache fixture</p><script src="/market-read.js"></script></body></html>');
        return;
      }
      if (url === '/market-read.js') {
        response.writeHead(200, { 'Content-Type': 'text/javascript' });
        response.end(bundled.outputFiles[0].text);
        return;
      }
      if (url.startsWith('/api/strength/scan')) {
        apiHits += 1;
        response.writeHead(200, {
          'Content-Type': 'application/json',
          'Cache-Control': 'private, max-age=60, stale-while-revalidate=300',
          'ETag': '"scan-v1"',
        });
        response.end(JSON.stringify({ version: apiHits, source_status: apiHits === 1 ? 'stale' : 'active' }));
        return;
      }
      response.writeHead(404);
      response.end();
    });
    port = await listen(server);
  });

  test.afterAll(async () => {
    await new Promise((resolve) => server.close(resolve));
  });

  test('cached GET is reused, then cache:reload reaches the server', async ({ page }) => {
    await page.goto(`http://127.0.0.1:${port}/`);
    const first = await page.evaluate(() => window.readMarket('/strength/scan?sector_id=semiconductors', { ttlMs: 0, staleMs: 0 }));
    expect(first.version).toBe(1);
    expect(apiHits).toBe(1);

    const cached = await page.evaluate(() => window.readMarket('/strength/scan?sector_id=semiconductors', { ttlMs: 0, staleMs: 0 }));
    expect(cached.version).toBe(1);
    expect(apiHits).toBe(1);

    const reloaded = await page.evaluate(() => window.readMarket('/strength/scan?sector_id=semiconductors', { force: true }));
    expect(reloaded.version).toBe(2);
    expect(reloaded.source_status).toBe('active');
    expect(apiHits).toBe(2);
  });
});
