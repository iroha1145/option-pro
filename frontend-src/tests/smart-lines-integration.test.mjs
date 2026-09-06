import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { detectSmartLines, selectSmartOverlays, smartTolerance, withChartIndices } from '../src/components/detail/chart-drawings/analysis/smartLines.ts';
import { closedBarsForFingerprint, filterOverlays, barFingerprint, barFingerprintFromBars } from '../src/components/detail/chart-drawings/analysis/mapBundle.ts';
import { railCandidatesFromOverlays, candidatesAtBar } from '../src/components/detail/chart-drawings/railSnap.ts';
import { snapCandidatesFromOverlays } from '../src/components/detail/chart-drawings/snap.ts';
import { isSupportLevel } from '../src/components/detail/chart-drawings/linePresentation.ts';
import { clipLineToRect } from '../src/components/detail/chart-drawings/clippedLines.ts';
import { hasTranslation } from '../src/i18n/core.ts';
import { overlay } from './fixtures/smart-lines.mjs';

const sourceRoot = fileURLToPath(new URL('../src/', import.meta.url));
const temporary = await mkdtemp(path.join(tmpdir(), 'smart-lines-behavior-'));
after(() => rm(temporary, { recursive: true, force: true }));
const compiled = await build({
  stdin: { contents: `export { overlaysToMarks } from ${JSON.stringify(path.join(sourceRoot, 'components/detail/chart-drawings/analysis/overlaysToMarks.ts'))};`, resolveDir: sourceRoot, loader: 'ts' },
  bundle: true, write: false, platform: 'node', format: 'esm', alias: { '@': sourceRoot },
  define: { 'import.meta.env': '{}' },
});
const compiledPath = path.join(temporary, 'overlays.mjs');
await writeFile(compiledPath, compiled.outputFiles[0].text);
const { overlaysToMarks } = await import(pathToFileURL(compiledPath).href);

function wave({ n = 240, drift = 0.15, growth = 0 } = {}) {
  return Array.from({ length: n }, (_, i) => {
    const t = new Date(Date.UTC(2025, 0, 1 + i, 21)).toISOString();
    const c = 100 + drift * i + (7 + growth * i) * Math.sin(i * 2 * Math.PI / 24);
    return { t, key: t.slice(0, 10), o: c, h: c + 0.5, l: c - 0.5, c, v: 100, closed: true };
  });
}
function railPrice(rail, bars, position) {
  const [a, b] = rail;
  const ax = bars.findIndex(bar => bar.key === a.barKey), bx = bars.findIndex(bar => bar.key === b.barKey);
  const x = i => bars[i].chartIndex ?? i;
  return a.price + (b.price - a.price) * (x(position) - x(ax)) / (x(bx) - x(ax));
}
const settings = { enabled: ['auto_patterns', 'support_resistance'], minShapeQuality: 0.45,
  onlyActive: true, showInvalidated: false, maxPatterns: 64, maxLabels: 4, labelDensity: 1, preset: 'custom' };
const display = (bars, options = settings) => selectSmartOverlays(filterOverlays(detectSmartLines(bars), options), bars, options.maxPatterns);
const ctxFor = (bars, range = '1d') => ({ bars, range, xMin: 0, xMax: bars.length - 1, yMin: 1, yMax: 250 });

test('recovering prices start a new structure after the confirmed historical break', () => {
  const bars = wave();
  for (const i of [155, 156]) {
    const c = 90 + i * 0.15; Object.assign(bars[i], { o: c, c, h: c + 0.5, l: c - 0.5 });
  }
  const broken = detectSmartLines(bars.slice(0, 157)).find(row => row.status === 'broken_down');
  assert.ok(broken);
  assert.equal(broken.formationEnd, bars[156].key);
  const after = detectSmartLines(bars);
  const channels = after.filter(row => row.kind === 'channel');
  assert.ok(channels.length > 0, 'recovery can establish a genuinely new channel');
  for (const row of channels) {
    const tolerance = smartTolerance(bars);
    const crossesOldBreak = row.formationStart < bars[155].key && [155, 156].every(i => bars[i].c < railPrice(row.geometry.supportRail, bars, i) - tolerance);
    assert.equal(crossesOldBreak, false, 'an active channel cannot erase a two-close break inside its own lifetime');
  }
  assert.ok(channels.some(row => row.formationStart > bars[156].key));
});

test('a recovered horizontal support cannot borrow touches from before its break', () => {
  const bars = wave({ drift: 0 });
  for (const i of [155, 156]) Object.assign(bars[i], { o: 90, c: 90, h: 90.5, l: 89.5 });
  const levels = detectSmartLines(bars).filter(row => row.kind === 'level' && row.geometry.role === 'support' && row.geometry.price === 92.5);
  assert.ok(levels.length > 0, 'a new level has enough post-break touches');
  assert.ok(levels.every(row => row.formationStart > bars[156].key));
});

test('final parallel channel boundaries obey the latest two-close break rule', () => {
  const bars = wave({ growth: 0.0005 });
  assert.ok(detectSmartLines(bars).some(row => row.kind === 'channel'));
  for (const [i, c] of [[238, 127.62871380056073], [239, 127.77871380056074]]) Object.assign(bars[i], { o: c, c, h: c + 0.1, l: c - 0.1 });
  const tolerance = smartTolerance(bars);
  for (const row of detectSmartLines(bars).filter(row => row.kind === 'channel')) {
    assert.equal([238, 239].every(i => bars[i].c < railPrice(row.geometry.supportRail, bars, i) - tolerance), false);
    assert.equal([238, 239].every(i => bars[i].c > railPrice(row.geometry.resistanceRail, bars, i) + tolerance), false);
  }
});

test('a higher-quality standalone rail survives a lower-quality channel at the user gate', () => {
  const bars = wave({ n: 88 });
  const proposals = detectSmartLines(bars);
  assert.ok(proposals.some(row => row.kind === 'channel' && row.shapeQuality < 0.9));
  const selected = display(bars, { ...settings, minShapeQuality: 0.9 });
  assert.ok(selected.some(row => row.kind === 'resistance_trend' && Math.abs(row.shapeQuality - 0.931) < 1e-8));
  assert.ok(selected.every(row => row.shapeQuality >= 0.9));
});

function minuteChart() {
  const regular = [], chart = [];
  for (let i = 0; i < 240; i++) {
    const day = Math.floor(i / 78), slot = i % 78;
    const t = new Date(Date.UTC(2026, 0, 5 + day, 14, 30 + slot * 5)).toISOString();
    const c = 100 + chart.length * 0.15 + 7 * Math.sin(i * 2 * Math.PI / 24);
    const bar = { t, key: t, o: c, c, h: c + 0.5, l: c - 0.5, v: 100, closed: true };
    regular.push(bar); chart.push(bar);
    const extraCount = i === 77 ? 12 : i === 155 ? 48 : 0;
    for (let j = 0; j < extraCount; j++) {
      const extT = new Date(Date.UTC(2026, 0, 5 + day, 21, j * 5)).toISOString();
      chart.push({ t: extT, key: extT, o: c, c, h: c + 1, l: c - 1, v: 10, ext: true, closed: true });
    }
  }
  return { regular, chart };
}

test('fitting and rendering use one full minute axis across unequal after-hours gaps', () => {
  const { chart } = minuteChart();
  const eligible = closedBarsForFingerprint(chart, '5m');
  assert.equal(eligible.length, 240); assert.equal(chart.length, 300);
  const indexed = withChartIndices(eligible, chart.map(bar => bar.key));
  const selected = display(indexed);
  const channels = selected.filter(row => row.kind === 'channel');
  assert.ok(channels.length > 0, 'full-axis linear channel fixture must still be detected');
  for (const row of channels) {
    const marks = overlaysToMarks([row], ctxFor(chart, '5m'));
    assert.ok(marks.lines.length >= 2);
    const snaps = railCandidatesFromOverlays([row], chart.map(bar => bar.key));
    assert.equal(snaps.length, 2);
    for (const touch of row.geometry.touchAnchors) {
      const x = chart.findIndex(bar => bar.key === touch.barKey);
      const prices = candidatesAtBar(snaps, x).map(candidate => candidate.price);
      assert.ok(Math.min(...prices.map(price => Math.abs(price - touch.price))) <= row.evidence.tolerance + 1e-8);
      const linePrices = marks.lines.filter(line => line[0].coord[0] <= x && x <= line[1].coord[0]).map(line => {
        const [a, b] = line.map(point => point.coord);
        return a[1] + (b[1] - a[1]) * (x - a[0]) / (b[0] - a[0]);
      });
      assert.ok(Math.min(...linePrices.map(price => Math.abs(price - touch.price))) <= row.evidence.tolerance + 1e-8);
    }
  }
});

test('unresolved, duplicate or reversed chart coordinates cannot produce annotations', () => {
  const bars = wave(), keys = bars.map(bar => bar.key);
  assert.deepEqual(withChartIndices(bars, keys.slice(1)), []);
  assert.deepEqual(withChartIndices(bars, [...keys, keys[0]]), []);
  assert.deepEqual(withChartIndices(bars, [...keys].reverse()), []);
  const indexed = withChartIndices(bars, keys); indexed[50].chartIndex = indexed[49].chartIndex;
  assert.deepEqual(detectSmartLines(indexed), []);
});

for (const range of ['5m', '15m', '1h']) test(`${range} fingerprints require explicit completed regular bars`, () => {
  const bars = wave({ n: 6 });
  bars[2].ext = true; bars[3].quote_only = true; delete bars[4].closed; bars[5].closed = false;
  assert.deepEqual(closedBarsForFingerprint(bars, range), bars.slice(0, 2));
  assert.equal(barFingerprintFromBars(bars, range), barFingerprint(bars.slice(0, 2)));
  assert.deepEqual(closedBarsForFingerprint(bars.map(({ closed, ...bar }) => bar), range), []);
});

test('the second unfinished minute candle cannot prematurely confirm a breakout', () => {
  const bars = wave();
  for (const i of [238, 239]) {
    const c = 112 + i * 0.15; Object.assign(bars[i], { o: c, c, h: c + 0.5, l: c - 0.5 });
  }
  bars[239].closed = false;
  assert.equal(detectSmartLines(closedBarsForFingerprint(bars, '5m')).some(row => row.status === 'broken_up'), false);
  assert.equal(bars[239].closed, false, 'reading a cached snapshot must not upgrade an unfinished candle');
  const completed = bars.map((bar, i) => i === 239 ? { ...bar, closed: true } : bar);
  assert.ok(detectSmartLines(closedBarsForFingerprint(completed, '5m')).some(row => row.status === 'broken_up' && row.formationEnd === bars[239].key));
});

test('daily completion flags and legacy dropLast never discard two final candles', () => {
  const bars = wave({ n: 4 }).map(({ closed, ...bar }) => bar);
  assert.deepEqual(closedBarsForFingerprint(bars, '1d'), bars);
  assert.deepEqual(closedBarsForFingerprint(bars, '1w'), bars);
  assert.deepEqual(closedBarsForFingerprint(bars, '1d', { dropLast: true }), bars.slice(0, 3));
  bars[3].closed = false;
  assert.deepEqual(closedBarsForFingerprint(bars, '1d', { dropLast: true }), bars.slice(0, 3));
});

for (const [name, mutate] of [
  ['crossed rails', row => row.geometry.anchors[3].price = 80],
  ['missing second rail', row => row.geometry.anchors.splice(2)],
  ['unresolved second rail', row => row.geometry.anchors[3].barKey = 'missing'],
  ['non-finite quality', row => row.shapeQuality = NaN],
  ['out-of-range quality', row => row.shapeQuality = 1.1],
]) test(`invisible ${name} do not suppress valid rails or attract snapping`, () => {
  const bars = wave(), row = overlay(bars, 'channel', [92, 100, 110, 120]);
  mutate(row);
  const marks = overlaysToMarks([row], ctxFor(bars));
  assert.equal(marks.lines.length, 0);
  assert.deepEqual(railCandidatesFromOverlays([row], bars.map(bar => bar.key)), []);
  const valid = overlay(bars, 'support_trend', [92, 100], { id: 'valid' });
  assert.deepEqual(selectSmartOverlays([row, valid], bars, 4).map(item => item.id), ['valid']);
});

test('paired snapping is restricted to the same common interval that is drawn', () => {
  const bars = wave(), row = overlay(bars, 'channel', [92, 100, 110, 120], { start: 20, end: 100 });
  row.geometry.anchors[2] = { ...row.geometry.anchors[2], time: bars[40].t, barKey: bars[40].key };
  const targets = railCandidatesFromOverlays([row], bars.map(bar => bar.key));
  assert.equal(targets.length, 2);
  assert.deepEqual(candidatesAtBar(targets, 30), []);
  assert.equal(candidatesAtBar(targets, 60).length, 2);
});

test('broken horizontal levels end at their historical boundary and are not snap targets', () => {
  const bars = wave(), row = overlay(bars, 'level', [100, 100], { start: 20, end: 100, status: 'broken_down' });
  row.geometry.price = 100; row.geometry.role = 'support';
  const marks = overlaysToMarks([row], ctxFor(bars));
  assert.equal(marks.lines.length, 1);
  assert.deepEqual(marks.lines[0].map(point => point.coord), [[20, 100], [100, 100]]);
  assert.deepEqual(marks.lines[0][0].lineStyle.type, [2, 4]);
  assert.equal(marks.lines[0][0].lineStyle.opacity, 0.3);
  assert.deepEqual(snapCandidatesFromOverlays([row]), []);
  assert.deepEqual(snapCandidatesFromOverlays([{ kind: 'level', geometry: { price: '100' } }]), []);
});

test('level role fallback and support/resistance translations are shared', () => {
  assert.equal(isSupportLevel({ price: 98 }, 100), true);
  assert.equal(isSupportLevel({ price: 102 }, 100), false);
  assert.equal(isSupportLevel({ price: 98, role: 'resistance' }, 100), false);
  for (const label of ['支撑', '阻力', '实线为水平价位；淡色点线为已失效价位']) {
    assert.ok(hasTranslation(label, 'en')); assert.ok(hasTranslation(label, 'ja'));
  }
});

test('pixel clipping retains a crossing segment whose two endpoints are offscreen', () => {
  const a = [-20, 20], b = [120, 80];
  const rect = { x: 0, y: 0, width: 100, height: 100 };
  const clipped = clipLineToRect(a, b, rect);
  assert.ok(clipped);
  assert.deepEqual(clipped.map(point => point[0]), [0, 100]);
  assert.ok(Math.abs(clipped[0][1] - 200 / 7) < 1e-8);
  assert.ok(Math.abs(clipped[1][1] - 500 / 7) < 1e-8);
  assert.deepEqual(a, [-20, 20]); assert.deepEqual(b, [120, 80]);
  assert.equal(clipLineToRect([-10, -5], [110, -5], rect), null);
  assert.equal(clipLineToRect([NaN, 0], [10, 10], rect), null);
});
