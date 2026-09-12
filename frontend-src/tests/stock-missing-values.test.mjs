import assert from 'node:assert/strict';
import test from 'node:test';
import { build } from 'esbuild';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('..', import.meta.url));
const bundle = await build({
  stdin: { contents: `export { mapBar, mapChart, stocksApi } from './src/api/modules/stocks.ts';`, resolveDir: root },
  bundle: true, write: false, platform: 'node', format: 'esm',
  alias: { '@': `${root}/src` }, define: { 'import.meta.env': '{"VITE_API_MODE":"live"}' },
});
const api = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`);
const valid = { t: '2026-09-11T04:00:00Z', o: 100, h: 102, l: 99, c: 101, v: 0 };

test('malformed price candles are omitted instead of becoming a real zero candle', () => {
  const invalid = [
    { ...valid, c: undefined }, { ...valid, o: null }, { ...valid, h: 0 },
    { ...valid, l: -1 }, { ...valid, c: Number.NaN }, { ...valid, c: Infinity },
    { ...valid, c: 103 }, { ...valid, h: 98 }, { ...valid, t: 'invalid' },
  ];
  for (const bar of invalid) assert.equal(api.mapBar(bar), null);
  const chart = api.mapChart({ bars: [valid, ...invalid] }, 'TEST', '1d');
  assert.equal(chart.candles.length, 1);
  assert.equal(chart.candles[0].c, 101);
  assert.deepEqual(chart.ma20, [null]);
});

test('a real zero volume remains zero while absent volume stays unobserved', () => {
  assert.equal(api.mapBar(valid).v, 0);
  for (const v of [undefined, null, -1, Infinity]) {
    assert.ok(Number.isNaN(api.mapBar({ ...valid, v }).v));
  }
});

test('valid timestamp units, provider precision and closed-bar metadata survive mapping', () => {
  const penny = { o: .12, h: .13, l: .11, c: .123456, v: 20, closed: false, ext: true, quote_only: true };
  const seconds = Date.parse(valid.t) / 1000;
  for (const stamp of [seconds, seconds * 1000]) {
    const bar = api.mapBar({ ...penny, t: stamp });
    assert.equal(bar.t, new Date(valid.t).toISOString());
    assert.equal(bar.c, .123456);
    assert.equal(bar.closed, false);
    assert.equal(bar.ext, true);
    assert.equal(bar.quote_only, true);
  }
});

test('missing overview price is not mapped to a zero-dollar quote', async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({ ticker: 'MISSING', name: 'Missing quote' }));
  try {
    const detail = await api.stocksApi.detail('MISSING');
    assert.ok(Number.isNaN(detail.price));
  } finally { globalThis.fetch = original; }
});
