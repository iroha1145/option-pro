/**
 * 2026-09-07 UI 审计里确认属实的回归：mock 日线闸门、进页乱滚、
 * 未来财报假实际值、首页辅助读数直接展示。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'path';

import {
  analysisGate,
  barFingerprint,
  barStampForRange,
  closedBarsForFingerprint,
  FINGERPRINT_ALGORITHM,
  fingerprintForBundle,
  fingerprintWindowOpts,
} from '../src/components/detail/chart-drawings/analysis/mapBundle.ts';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = path.resolve(here, '..', 'src');
const source = (rel) => readFile(path.join(src, rel), 'utf8');

function dayBars(count, start = '2026-01-02') {
  const origin = Date.parse(`${start}T20:00:00.000Z`);
  return Array.from({ length: count }, (_, i) => {
    const t = new Date(origin + i * 86_400_000).toISOString();
    const c = 100 + i;
    return { t, o: c, h: c + 1, l: c - 1, c, v: 1_000 };
  });
}

test('根数闸门与指纹切同一段窗口，不再因为 series_break_at 为空而吃完整序列', () => {
  const bars = dayBars(20);
  const window = bars.slice(-8);
  const bundle = {
    ticker: 'AAPL',
    range: '1d',
    adjustment: 'raw',
    dataThrough: barStampForRange(window.at(-1).t, '1d'),
    firstBarDate: barStampForRange(window[0].t, '1d'),
    lastBarDate: barStampForRange(window.at(-1).t, '1d'),
    barCount: window.length,
    lastClose: window.at(-1).c,
    barFingerprint: barFingerprint(window),
    fingerprintAlgorithm: FINGERPRINT_ALGORITHM,
    overlays: [],
    indicatorPanes: [],
    strengthContext: null,
  };
  const unwindowed = closedBarsForFingerprint(bars, '1d', {
    fromDate: null,
    throughDate: bundle.lastBarDate,
  });
  assert.equal(unwindowed.length, 20);
  const gated = closedBarsForFingerprint(bars, '1d', fingerprintWindowOpts(bundle));
  assert.equal(gated.length, 8);
  assert.equal(fingerprintForBundle(bundle, bars, '1d'), bundle.barFingerprint);
  assert.equal(analysisGate(bundle, {
    range: '1d',
    adjustment: 'raw',
    ticker: 'AAPL',
    dataThrough: bundle.dataThrough,
    barCount: gated.length,
    lastClose: gated.at(-1).c,
    fingerprint: fingerprintForBundle(bundle, bars, '1d'),
  }), 'ok');
});

test('KlineChart 与 mock 蜡烛都按同一窗口/时钟对齐', async () => {
  const chart = await source('components/detail/KlineChart.tsx');
  assert.match(chart, /fingerprintWindowOpts\(analysisBundle, fingerprintOpts\)/);
  const fixtures = await source('mocks/fixtures.ts');
  assert.match(fixtures, /MOCK_CANDLE_NOW_MS/);
  assert.match(fixtures, /const start = MOCK_CANDLE_NOW_MS - points \* stepMs/);
  assert.doesNotMatch(fixtures, /const start = Date\.now\(\) - points \* stepMs/);
});

test('财报自动选中不 scrollIntoView，用户点选才滚', async () => {
  const list = await source('components/earnings/EarningsList.tsx');
  assert.match(list, /autoSelected/);
  assert.match(list, /if \(autoSelected\) return;/);
  const page = await source('pages/Earnings.tsx');
  assert.match(page, /autoSelected=\{selectedTicker !== null && selectedTicker === autoPickedTicker\}/);
});

test('今日及未来财报不再随机写入实际值', async () => {
  const fixtures = await source('mocks/fixtures2.ts');
  assert.match(fixtures, /const reported = date < todayIso \? r\.chance\(0\.9\) : false;/);
  assert.doesNotMatch(fixtures, /date < todayIso \? r\.chance\(0\.9\) : r\.chance\(0\.15\)/);
});

test('首页辅助读数直接展示，不再默认收进 details', async () => {
  const home = await source('pages/Home.tsx');
  assert.match(home, /data-testid="home-supporting-metrics"/);
  assert.doesNotMatch(home, /<details[^>]*data-testid="home-supporting-metrics"/);
  assert.doesNotMatch(home, /group\/readings/);
});

test('首页指数后插入经济日历，普通午夜事件不再标成全天', async () => {
  const home = await source('pages/Home.tsx');
  const calendarIdx = home.indexOf('<EconomicCalendarCard');
  const marketIdx = home.indexOf('行2：市场状态 + 雷达信号');
  assert.ok(calendarIdx > 0 && marketIdx > calendarIdx, '经济日历应在市场状态之前');
  const card = await source('components/catalysts/EconomicCalendarCard.tsx');
  assert.match(card, /data-testid="home-economic-calendar"/);
  const panel = await source('components/catalysts/CalendarPanel.tsx');
  assert.match(panel, /impact === 'holiday' && t\.getHours\(\) === 0 && t\.getMinutes\(\) === 0/);
  const stocks = await source('components/catalysts/StocksPanel.tsx');
  assert.match(stocks, /t\(r\.sector\)/);
  const status = await source('components/catalysts/cacheStatusProps.ts');
  assert.match(status, /export function cacheStatusProps/);
  for (const file of [
    'components/catalysts/EconomicCalendarCard.tsx',
    'components/catalysts/CalendarPanel.tsx',
    'components/catalysts/FeedPanel.tsx',
    'components/catalysts/StocksPanel.tsx',
  ]) {
    const src = await source(file);
    assert.doesNotMatch(src, /<CatalystCacheStatus \{\.\.\.q\}/, `${file} 不得把资源 key 展开进 JSX`);
    assert.match(src, /cacheStatusProps\(q\)/);
  }
});
