import test from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('..', import.meta.url));
const bundle = await build({
  stdin: { contents: `
    export { getDetailChart } from './src/components/detail/api.ts';
    export { resetMarketReadState } from './src/api/marketRead.ts';
  `, resolveDir: root },
  bundle: true, write: false, format: 'esm', platform: 'node',
  alias: { '@': `${root}/src` }, define: { 'import.meta.env': '{"VITE_API_MODE":"live"}' },
});
const api = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`);
const savedChart = (range) => ({ ticker: 'NVDA', range, price_adjustment: 'raw', bars: [
  { t: 1_700_000_000, o: 100, h: 102, l: 99, c: 101, v: 1000 },
] });
const response = (value, status = 200) => new Response(JSON.stringify(value), { status });

for (const range of ['5m', '15m', '1h', '1w']) {
  test(`customer's missing ${range} chart is pulled, then force-read from the published cache`, async () => {
    api.resetMarketReadState();
    const original = globalThis.fetch;
    const calls = []; let ready = false;
    globalThis.fetch = async (url, options) => {
      calls.push({ url: String(url), method: options.method ?? 'GET', cache: options.cache });
      if (options.method === 'POST') {
        assert.equal(String(url), `/api/stocks/NVDA/pull?chart_range=${range}`);
        assert.equal(options.credentials, 'include');
        ready = true;
        return response({ status: 'completed', persisted: true });
      }
      return ready ? response(savedChart(range)) : response({ detail: { code: 'public_snapshot_unavailable' } }, 503);
    };
    try {
      const chart = await api.getDetailChart('nvda', range, false, true);
      assert.equal(chart.range, range);
      assert.equal(chart.bars.length, 1);
      assert.deepEqual(calls.map((c) => c.method), ['GET', 'POST', 'GET']);
      assert.equal(calls.at(-1).cache, 'reload');
      await api.getDetailChart('NVDA', range, false, true);
      assert.equal(calls.length, 3, 'the saved result must not spend another pull allowance');
    } finally { globalThis.fetch = original; }
  });
}

test('anonymous reads and daily prefetches never start automatic pull work', async () => {
  api.resetMarketReadState();
  const original = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push(options.method ?? 'GET');
    return response({ detail: { code: 'public_snapshot_unavailable' } }, 503);
  };
  try {
    await assert.rejects(api.getDetailChart('NVDA', '5m'), (error) => error.bizCode === 'public_snapshot_unavailable');
    await assert.rejects(api.getDetailChart('NVDA', '1d', false, true), (error) => error.bizCode === 'public_snapshot_unavailable');
    assert.deepEqual(calls, ['GET', 'GET']);
  } finally { globalThis.fetch = original; }
});

test('provider errors are surfaced without blind automatic retries', async () => {
  api.resetMarketReadState();
  const original = globalThis.fetch; let calls = 0;
  globalThis.fetch = async () => { calls += 1; return response({ detail: 'provider unavailable' }, 503); };
  try {
    await assert.rejects(api.getDetailChart('NVDA', '5m', false, true));
    assert.equal(calls, 1);
  } finally { globalThis.fetch = original; }
});

test('explicit refresh updates the selected period even when a saved chart exists', async () => {
  api.resetMarketReadState();
  const original = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url: String(url), method: options.method ?? 'GET' });
    return response(options.method === 'POST' ? { status: 'completed', persisted: true } : savedChart('15m'));
  };
  try {
    await api.getDetailChart('NVDA', '15m', false, true);
    calls.length = 0;
    await api.getDetailChart('NVDA', '15m', true, true);
    assert.deepEqual(calls.map((c) => c.method), ['POST', 'GET']);
    assert.match(calls[0].url, /chart_range=15m$/);
  } finally { globalThis.fetch = original; }
});

for (const available of [true, false]) {
  test(`a shared-pull cooldown ${available ? 'reuses the other customer result' : 'keeps its retry message when no result exists'}`, async () => {
    api.resetMarketReadState();
    const original = globalThis.fetch; let completedElsewhere = false; let posts = 0;
    globalThis.fetch = async (url, options) => {
      if (options.method === 'POST') {
        posts += 1; completedElsewhere = true;
        return response({ detail: { code: 'stock_pull_cooldown', retry_after_seconds: 60 } }, 429);
      }
      return completedElsewhere && available ? response(savedChart('5m')) : response({ detail: { code: 'public_snapshot_unavailable' } }, 503);
    };
    try {
      const request = api.getDetailChart('NVDA', '5m', false, true);
      if (available) assert.equal((await request).bars.length, 1);
      else await assert.rejects(request, (error) => error.bizCode === 'stock_pull_cooldown' && error.retryAfter === 60);
      assert.equal(posts, 1);
    } finally { globalThis.fetch = original; }
  });
}
