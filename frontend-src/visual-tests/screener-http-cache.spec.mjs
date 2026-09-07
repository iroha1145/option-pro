import { expect, test } from '@playwright/test';
import http from 'node:http';

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
    server = http.createServer((request, response) => {
      const url = request.url || '/';
      if (url === '/' || url === '/index.html') {
        response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
        response.end('<!doctype html><html><body><p>screener cache fixture</p></body></html>');
        return;
      }
      if (url.startsWith('/strength/scan')) {
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
    const first = await page.evaluate(async () => {
      const response = await fetch('/strength/scan?sector_id=semiconductors');
      return response.json();
    });
    expect(first.version).toBe(1);
    expect(apiHits).toBe(1);

    const cached = await page.evaluate(async () => {
      const response = await fetch('/strength/scan?sector_id=semiconductors');
      return response.json();
    });
    expect(cached.version).toBe(1);
    expect(apiHits).toBe(1);

    const reloaded = await page.evaluate(async () => {
      const response = await fetch('/strength/scan?sector_id=semiconductors', { cache: 'reload' });
      return response.json();
    });
    expect(reloaded.version).toBe(2);
    expect(reloaded.source_status).toBe('active');
    expect(apiHits).toBe(2);
  });
});
