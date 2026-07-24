import test from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(here, '..');
const src = path.join(frontendRoot, 'src');

async function loadMapper(t) {
  const tempRoot = await mkdtemp(path.join(tmpdir(), 'optix-trend-bias-'));
  t.after(() => rm(tempRoot, { recursive: true, force: true }));
  const entry = path.join(tempRoot, 'entry.ts');
  const output = path.join(tempRoot, 'mapper.mjs');
  const apiPath = path.join(src, 'components', 'detail', 'api.ts');
  await writeFile(
    entry,
    `export { mapTrendBiasResponse } from ${JSON.stringify(apiPath)};\n`,
    'utf8',
  );
  await build({
    absWorkingDir: frontendRoot,
    entryPoints: [entry],
    outfile: output,
    bundle: true,
    format: 'esm',
    platform: 'node',
    target: 'node20',
    packages: 'external',
    alias: { '@': src },
    define: {
      'import.meta.env': JSON.stringify({ VITE_API_MODE: 'live' }),
    },
    logLevel: 'silent',
  });
  return import(`${pathToFileURL(output).href}?test=${Date.now()}`);
}

test('mapTrendBiasResponse derives available subscores from the real signal envelope', async (t) => {
  const { mapTrendBiasResponse } = await loadMapper(t);
  const mapped = mapTrendBiasResponse(
    {
      ticker: 'AAOI',
      trend_bias_score: 71,
      trend_bias_label: '偏多',
      trend_bias_status: 'active',
      trend_bias_coverage: 1,
      trend_bias_missing_components: [],
      scores: {
        top_score: 63,
        bottom_score: 12,
      },
      signals: {
        sma20_dist: { value: 2, label: '距20日线偏离%', top_score: 16, bottom_score: 0 },
        sma50_dist: { value: 2, label: '距50日线偏离%', top_score: 10, bottom_score: 0 },
        relative_strength_spy: { value: 5, label: '相对强弱(vs SPY)%', top_score: 30, bottom_score: 0 },
        rsi14: { value: 60, label: 'RSI(14)', top_score: 30, bottom_score: 0 },
        return_20d: { value: 8, label: '20日涨幅%', top_score: 28, bottom_score: 0 },
        obv_divergence: { value: 10, label: 'OBV背离', top_score: 30, bottom_score: 0 },
        atr_percentile: { value: 72, label: 'ATR 1年分位%', top_score: 14, bottom_score: 2 },
      },
      as_of: '2026-07-24T01:00:00Z',
    },
    'AAOI',
  );

  assert.equal(mapped.trend_bias_status, 'ok');
  assert.equal(mapped.scores.trend, 59.3);
  assert.equal(mapped.scores.momentum, 64.5);
  assert.equal(mapped.scores.volume, 65);
  assert.equal(mapped.scores.volatility, 72);
  assert.deepEqual(
    mapped.factors.map((factor) => factor.key),
    ['trend', 'momentum', 'volume', 'volatility'],
  );
});

test('mapTrendBiasResponse keeps insufficient data nullable and never reads absent factors', async (t) => {
  const { mapTrendBiasResponse } = await loadMapper(t);
  const mapped = mapTrendBiasResponse(
    {
      ticker: 'NBIS',
      trend_bias_score: null,
      trend_bias_label: '数据不足',
      trend_bias_status: 'insufficient_data',
      trend_bias_coverage: 0,
      trend_bias_missing_components: ['relative_strength_spy', 'macd_hist', 'rsi14'],
      scores: {},
      signals: {
        rsi14: { value: null, label: 'RSI(14)', top_score: null, bottom_score: null },
      },
      as_of: '2026-07-24T01:00:00Z',
    },
    'NBIS',
  );

  assert.equal(mapped.trend_bias_score, null);
  assert.equal(mapped.trend_bias_label, '数据不足');
  assert.equal(mapped.trend_bias_status, 'insufficient_data');
  assert.deepEqual(mapped.scores, {
    trend: null,
    momentum: null,
    volume: null,
    volatility: null,
  });
  assert.deepEqual(mapped.factors, []);
});
