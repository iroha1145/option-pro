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

async function loadDrawdown(t) {
  const tempRoot = await mkdtemp(path.join(tmpdir(), 'optix-drawdown-'));
  t.after(() => rm(tempRoot, { recursive: true, force: true }));
  const entry = path.join(tempRoot, 'entry.ts');
  const output = path.join(tempRoot, 'drawdown.mjs');
  const libPath = path.join(src, 'lib', 'drawdown.ts');
  await writeFile(
    entry,
    `export { measureRange, resolveAnchor, snapToMeasurableBar, barTimeMs } from ${JSON.stringify(libPath)};\n`,
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
    logLevel: 'silent',
  });
  return import(`${pathToFileURL(output).href}?test=${Date.now()}`);
}

// 夏令时期间纽约为 UTC-4：13:30Z = 09:30 ET
function bar(t, o, h, l, c, quoteOnly = false) {
  return { t, o, h, l, c, v: 1000, ...(quoteOnly ? { quote_only: true } : {}) };
}

const ms = (t) => Date.parse(t);

const assertClose = (actual, expected, label) => {
  assert.ok(
    Math.abs(actual - expected) < 1e-9,
    `${label ?? 'value'}: expected ≈${expected}, got ${actual}`,
  );
};

test('wick 口径:峰 High → 谷 Low,回撤与修复涨幅', async (t) => {
  const { measureRange } = await loadDrawdown(t);
  const bars = [
    bar('2026-07-06T13:30:00Z', 98, 100, 96, 99),
    bar('2026-07-07T13:30:00Z', 99, 99.5, 90, 91),
    bar('2026-07-08T13:30:00Z', 91, 92, 80, 82),
  ];
  const m = measureRange(bars, ms(bars[0].t), ms(bars[2].t), 'wick');
  assert.ok(m);
  assert.equal(m.startIdx, 0);
  assert.equal(m.endIdx, 2);
  assert.equal(m.startPrice, 100);
  assert.equal(m.endPrice, 80);
  assertClose(m.changePct, -20, 'changePct');
  assertClose(m.changeAbs, -20, 'changeAbs');
  assertClose(m.recoveryPct, 25, 'recoveryPct');
  assert.equal(m.isDrawdown, true);
  assert.equal(m.barCount, 3);
  assert.equal(m.sessionDays, 3);
});

test('点击顺序自由:反序传入结果一致', async (t) => {
  const { measureRange } = await loadDrawdown(t);
  const bars = [
    bar('2026-07-06T13:30:00Z', 98, 100, 96, 99),
    bar('2026-07-07T13:30:00Z', 99, 99.5, 80, 82),
  ];
  const forward = measureRange(bars, ms(bars[0].t), ms(bars[1].t), 'wick');
  const backward = measureRange(bars, ms(bars[1].t), ms(bars[0].t), 'wick');
  assert.deepEqual(forward, backward);
  assertClose(forward.changePct, -20, 'changePct');
});

test('close 口径:两端收盘价,不看影线', async (t) => {
  const { measureRange } = await loadDrawdown(t);
  const bars = [
    bar('2026-07-06T13:30:00Z', 98, 120, 96, 100),
    bar('2026-07-07T13:30:00Z', 99, 99.5, 70, 90),
  ];
  const m = measureRange(bars, ms(bars[0].t), ms(bars[1].t), 'close');
  assert.equal(m.startPrice, 100);
  assert.equal(m.endPrice, 90);
  assert.ok(Math.abs(m.changePct - -10) < 1e-9);
  assert.ok(Math.abs(m.recoveryPct - (100 / 90 - 1) * 100) < 1e-9);
});

test('wick 口径:选段整体上涨(按收盘判向)时量 Low → High', async (t) => {
  const { measureRange } = await loadDrawdown(t);
  const bars = [
    bar('2026-07-06T13:30:00Z', 98, 101, 95, 100),
    bar('2026-07-07T13:30:00Z', 100, 110, 99, 108),
  ];
  const m = measureRange(bars, ms(bars[0].t), ms(bars[1].t), 'wick');
  assert.equal(m.startPrice, 95);
  assert.equal(m.endPrice, 110);
  assert.equal(m.isDrawdown, false);
  assert.equal(m.recoveryPct, null);
  assert.ok(m.changePct > 0);
});

test('单根 bar:量当根振幅 High → Low', async (t) => {
  const { measureRange } = await loadDrawdown(t);
  const bars = [bar('2026-07-06T13:30:00Z', 98, 105, 95, 99)];
  const m = measureRange(bars, ms(bars[0].t), ms(bars[0].t), 'wick');
  assert.equal(m.startPrice, 105);
  assert.equal(m.endPrice, 95);
  assert.equal(m.barCount, 1);
  assert.equal(m.sessionDays, 1);
  assert.ok(m.isDrawdown);
});

test('锚点解析不到(窗口滚动/换标的)返回 null,不悄悄挪到别的 bar', async (t) => {
  const { measureRange, resolveAnchor } = await loadDrawdown(t);
  const bars = [
    bar('2026-07-06T13:30:00Z', 98, 100, 96, 99),
    bar('2026-07-07T13:30:00Z', 99, 99.5, 90, 91),
  ];
  assert.equal(resolveAnchor(bars, ms('2026-07-05T13:30:00Z')), -1);
  assert.equal(measureRange(bars, ms('2026-07-05T13:30:00Z'), ms(bars[1].t), 'wick'), null);
});

test('snapToMeasurableBar 跳过 quote_only,取最近真实成交 bar', async (t) => {
  const { snapToMeasurableBar } = await loadDrawdown(t);
  const bars = [
    bar('2026-07-06T08:00:00Z', 98, 99, 98, 99, true),
    bar('2026-07-06T08:05:00Z', 99, 99, 98, 98, true),
    bar('2026-07-06T13:30:00Z', 98, 100, 96, 99),
    bar('2026-07-06T13:35:00Z', 99, 101, 97, 100),
  ];
  assert.equal(snapToMeasurableBar(bars, 0), 2);
  assert.equal(snapToMeasurableBar(bars, 3), 3);
  assert.equal(snapToMeasurableBar(bars, -5), 2);
  assert.equal(snapToMeasurableBar(bars, 99), 3);
  const allQuote = [bar('2026-07-06T08:00:00Z', 98, 99, 98, 99, true)];
  assert.equal(snapToMeasurableBar(allQuote, 0), -1);
  assert.equal(snapToMeasurableBar([], 0), -1);
});

test('sessionDays 按纽约时区换日,不按 UTC', async (t) => {
  const { measureRange } = await loadDrawdown(t);
  // 00:30Z 在纽约仍是前一天 20:30(盘后),UTC 口径会误并成同一天
  const bars = [
    bar('2026-07-21T00:30:00Z', 98, 100, 96, 99),
    bar('2026-07-21T13:35:00Z', 99, 99.5, 90, 91),
  ];
  const m = measureRange(bars, ms(bars[0].t), ms(bars[1].t), 'wick');
  assert.equal(m.sessionDays, 2);
});

test('非法价格(≤0 / 非有限)返回 null', async (t) => {
  const { measureRange } = await loadDrawdown(t);
  const bars = [
    bar('2026-07-06T13:30:00Z', 0, 0, 0, 0),
    bar('2026-07-07T13:30:00Z', 99, 99.5, 90, 91),
  ];
  assert.equal(measureRange(bars, ms(bars[0].t), ms(bars[1].t), 'wick'), null);
});
