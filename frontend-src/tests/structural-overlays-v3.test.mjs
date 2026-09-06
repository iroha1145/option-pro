import test from 'node:test';
import assert from 'node:assert/strict';
import { prepareStructuralOverlays as prepare, selectStructuralOverlays as select, structuralTolerance, closedStructuralBars } from '../src/components/detail/chart-drawings/analysis/structuralOverlays.ts';
import { detectPriceGaps, subtractTradedRange } from '../src/components/detail/chart-drawings/analysis/priceGaps.ts';
import { semanticLabel, gapAreas, overlayTier } from '../src/components/detail/chart-drawings/analysis/semanticPresentation.ts';
import { packEndLabels, rectanglesOverlap, measureLabel } from '../src/components/detail/chart-drawings/labelLayout.ts';
import { renderPatternInk, manualLineInk, LINE_INK, normalizePatternSegments } from '../src/components/detail/chart-drawings/linePresentation.ts';
import { clippedLineSeries, clipLineToRect } from '../src/components/detail/chart-drawings/clippedLines.ts';

function bars(n = 60, price = 100) {
  const out = [];
  for (let day = 0; out.length < n; day++) {
    const d = new Date(Date.UTC(2026, 0, 5 + day, 21));
    if ([0, 6].includes(d.getUTCDay())) continue;
    const t = d.toISOString();
    out.push({ t, key: t.slice(0, 10), o: price, h: price + 1, l: price - 1, c: price, closed: true, chartIndex: out.length });
  }
  return out;
}
function setBar(data, i, close, low = close - 1, high = close + 1) {
  Object.assign(data[i], { o: close, c: close, l: low, h: high });
}
function line(data, id, y1 = 105, y2 = y1, extra = {}) {
  const start = extra.start ?? 0, end = extra.end ?? data.length - 1;
  const anchors = [start, end].map((i, n) => ({ time: data[i].t, barKey: data[i].key, price: n ? y2 : y1 }));
  const kind = extra.kind ?? 'resistance_trend';
  const overlay = { id, sourceId: 'test', algorithmVersion: 'fixture', group: 'price', kind,
    geometry: { anchors, fitAnchors: anchors }, status: 'forming', direction: 'neutral', shapeQuality: 0.85,
    displayPriority: 85, evidence: { touches: 3 }, formationStart: data[start].key, formationEnd: data[end].key,
    dataThrough: data.at(-1).key, label: '', detail: '', ...extra };
  delete overlay.start; delete overlay.end;
  return overlay;
}
const ctx = { xMin: 0, xMax: 59, yMin: 1, yMax: 300 };
const pattern = (extra = {}) => ({ id: 'r', kind: 'resistance_trend', status: 'forming', confidence: 85, label: '阻力', ...extra });
const geometry = { segments: [{ a: { x: 0, y: 110 }, b: { x: 59, y: 105 } }], fill: null };
const noOverlap = (r) => { for (let i = 0; i < r.length; i++) for (let j = i + 1; j < r.length; j++) assert.equal(rectanglesOverlap(r[i], r[j], 0), false); };

test('identical candidates from server and client collapse to one representative', () => {
  const b = bars(), a = line(b, 'a'), c = { ...line(b, 'b'), sourceId: 'server' };
  const out = select([a, c], b, 4);
  assert.equal(out.length, 1); assert.equal(out[0].evidence.collapsedCandidates, 1);
  assert.equal(out[0].evidence.touches, 3); assert.equal(out[0].shapeQuality, 0.85);
});
test('genuinely separated resistance levels survive and have a visual hierarchy', () => {
  const b = bars(), out = select([line(b, 'near', 104), line(b, 'other', 108)], b, 4);
  assert.equal(out.length, 2);
  assert.equal(out[0].evidence.displayTier, 'primary'); assert.equal(out[1].evidence.displayTier, 'secondary');
});
test('a shared right endpoint alone never merges different slopes', () => {
  const b = bars(), out = select([line(b, 'a', 115, 105), line(b, 'b', 130, 105)], b, 4);
  assert.equal(out.length, 2);
});
test('opposite boundary roles at the same price are not interchangeable', () => {
  const b = bars(), out = select([line(b, 'r', 100), line(b, 's', 100, 100, { kind: 'support_trend' })], b, 4);
  assert.equal(out.length, 2);
});
test('historical and newly active structures cannot suppress each other', () => {
  const b = bars(), out = select([line(b, 'new', 105), line(b, 'old', 105, 105, { status: 'broken_up' })], b, 4);
  assert.equal(out.length, 2); assert.equal(out.find(o => o.id === 'old').evidence.displayTier, 'historical');
});
test('far-away boundaries are context, not forced primary lines', () => {
  const b = bars(), out = select([line(b, 'remote', 135)], b, 4);
  assert.equal(out[0].evidence.displayTier, 'context');
});
test('two shared touch events allow conservative fit-family deduplication', () => {
  const b = bars(), a = line(b, 'a', 105), c = line(b, 'b', 106);
  const touches = [5, 25, 45].map(i => ({ time: b[i].t, barKey: b[i].key, price: 105.5 }));
  a.geometry.touchAnchors = touches; c.geometry.touchAnchors = touches;
  assert.ok(1 > structuralTolerance(b) * 1.5);
  assert.equal(select([a, c], b, 4).length, 1);
});
test('nearby fits without common touches are not forcibly averaged', () => {
  const b = bars(), a = line(b, 'a', 105), c = line(b, 'b', 106);
  assert.equal(select([a, c], b, 4).length, 2);
});
test('candidate input order cannot change the selected structures', () => {
  const b = bars(), rows = [line(b, 'c', 104), line(b, 'a', 104), line(b, 'x', 110)];
  assert.deepEqual(select(rows, b, 4), select([...rows].reverse(), b, 4));
});
test('deduplication runs before the pattern count cap', () => {
  const b = bars(), rows = [...Array.from({ length: 8 }, (_, i) => line(b, `same${i}`, 104)), line(b, 'distinct', 109)];
  const out = select(rows, b, 2); assert.equal(out.length, 2); assert.ok(out.some(o => o.id === 'distinct'));
});
test('non-overlapping formation intervals at one horizontal price are distinct', () => {
  const b = bars(), a = line(b, 'a', 100, 100, { kind: 'level', start: 0, end: 20 });
  const c = line(b, 'b', 100, 100, { kind: 'level', start: 35, end: 59 });
  a.geometry = { price: 100, role: 'support' }; c.geometry = { price: 100, role: 'support' };
  assert.equal(select([a, c], b, 4).length, 2);
});
test('complete channel suppresses an equivalent standalone boundary', () => {
  const b = bars(), low = line(b, 's', 95, 95, { kind: 'support_trend' }), high = line(b, 'r', 105);
  const pair = line(b, 'channel', 95, 95, { kind: 'channel' });
  pair.geometry = { supportRail: low.geometry.anchors, resistanceRail: high.geometry.anchors };
  const out = select([high, low, pair], b, 3);
  assert.deepEqual(out.map(o => o.id), ['channel']);
});
test('crossed or malformed paired rails cannot suppress a valid single rail', () => {
  const b = bars(), pair = line(b, 'bad', 95, 110, { kind: 'channel' });
  pair.geometry = { anchors: [...line(b, 'a', 95, 110).geometry.anchors, ...line(b, 'b', 105, 100).geometry.anchors] };
  assert.deepEqual(select([pair, line(b, 'r', 105)], b, 3).map(o => o.id), ['r']);
});
test('NaN scores, invalid quality and unresolved anchors are rejected', () => {
  const b = bars(), bad = line(b, 'bad'); bad.geometry.anchors[1].barKey = 'absent';
  assert.deepEqual(select([bad, line(b, 'nan', 105, 105, { shapeQuality: NaN }), line(b, 'big', 105, 105, { shapeQuality: 2 })], b, 3), []);
});
test('full category positions including session holes are preserved', () => {
  const b = bars(); for (let i = 30; i < b.length; i++) b[i].chartIndex += 50;
  const rows = [line(b, 'a', 110, 105), line(b, 'b', 110, 105)];
  assert.equal(select(rows, b, 4).length, 1); assert.equal(b[30].chartIndex, 80);
});
test('selection never mutates source candidates or bar arrays', () => {
  const b = bars(), input = [line(b, 'a'), line(b, 'b')], before = JSON.stringify({ b, input });
  select(input, b, 4); prepare(input, b); assert.equal(JSON.stringify({ b, input }), before);
});
test('empty, duplicate or non-monotonic bars fail closed', () => {
  const b = bars(), row = line(b, 'r');
  assert.deepEqual(select([row], [], 3), []);
  b[20].key = b[19].key; assert.deepEqual(closedStructuralBars(b), []);
  const reversed = bars().reverse(); assert.deepEqual(prepare([row], reversed), []);
});
test('live/unfinished bars never participate in state confirmation', () => {
  const b = bars(), row = line(b, 'r', 105, 105, { end: 57 });
  setBar(b, 58, 108); setBar(b, 59, 109); b[59].closed = false;
  const out = prepare([row], b)[0];
  assert.equal(out.status, 'testing'); assert.equal(out.evidence.visualState, 'breakout_pending');
  b[59].closed = true; const confirmed = prepare([row], b)[0];
  assert.equal(confirmed.status, 'broken_up'); assert.equal(confirmed.formationEnd, b[59].key);
});
test('wick-only penetration is not a confirmed break', () => {
  const b = bars(); b[58].h = 110; b[59].h = 111;
  assert.equal(prepare([line(b, 'r', 105)], b)[0].status, 'forming');
});
test('an old confirmed break is not erased by later recovery', () => {
  const b = bars(); setBar(b, 30, 108); setBar(b, 31, 109);
  const out = prepare([line(b, 'r', 105)], b)[0];
  assert.equal(out.status, 'broken_up'); assert.equal(out.geometry.fitAnchors[1].barKey, b[31].key);
  assert.equal(out.formationEnd, b[31].key);
});
test('invalidated source structures are never revived', () => {
  const b = bars(); assert.equal(prepare([line(b, 'r', 105, 105, { status: 'invalidated' })], b)[0].status, 'invalidated');
});
test('paired-pattern lifecycle is not rewritten using a single-rail rule', () => {
  const b = bars(), row = line(b, 'p', 95, 95, { kind: 'channel', status: 'confirmed' });
  row.geometry = { supportRail: line(b, 'l', 95).geometry.anchors, resistanceRail: line(b, 'u', 105).geometry.anchors };
  assert.equal(prepare([row], b)[0].status, 'confirmed');
});
test('a quote-only extreme cannot move selected geometry', () => {
  const b = bars(), row = line(b, 'r', 105, 105, { end: 58 }), first = prepare([row], b.slice(0, 59));
  setBar(b, 59, 1000); b[59].quote_only = true;
  assert.deepEqual(prepare([row], b), first);
});
test('zero pattern cap retains separately enabled horizontal levels', () => {
  const b = bars(), level = line(b, 'l', 99, 99, { kind: 'level' }); level.geometry = { price: 99, role: 'support' };
  assert.deepEqual(select([line(b, 'r'), level], b, 0).map(o => o.id), ['l']);
});
test('background budgets do not remove moving averages or consume line slots', () => {
  const b = bars(), items = ['box', 'gap'].flatMap(kind => Array.from({ length: 10 }, (_, i) => line(b, `${kind}${i}`, 105, 105, { kind })));
  items.push(line(b, 'ma20', 100, 100, { kind: 'ma' }), line(b, 'r'));
  const out = select(items, b, 1);
  assert.equal(out.filter(o => o.kind === 'box').length, 2); assert.equal(out.filter(o => o.kind === 'gap').length, 4);
  assert.ok(out.some(o => o.id === 'ma20')); assert.ok(out.some(o => o.id === 'r'));
});

function upGap(extra = 0) { const b = bars(31 + extra); for (let i = 30; i < b.length; i++) setBar(b, i, 107, 106, 108); return b; }
test('full high/low daily gap is detected with exact bounds', () => {
  const b = upGap(), out = detectPriceGaps(b, '1d'); assert.equal(out.length, 1);
  assert.deepEqual(out[0].geometry.remainingIntervals, [{ low: 101, high: 106 }]); assert.equal(out[0].status, 'forming');
});
test('a downward gap is handled symmetrically', () => {
  const b = bars(31); setBar(b, 30, 93, 92, 94);
  const out = detectPriceGaps(b, '1d'); assert.equal(out.length, 1);
  assert.deepEqual(out[0].geometry.remainingIntervals, [{ low: 94, high: 99 }]);
});
test('an opening jump with overlapping high/low ranges is not a full price gap', () => {
  const b = bars(31); setBar(b, 30, 104, 100, 105); assert.deepEqual(detectPriceGaps(b, '1d'), []);
});
test('tiny gaps beneath the formation-time range threshold are suppressed', () => {
  const b = bars(31); setBar(b, 30, 102, 101.5, 103); assert.deepEqual(detectPriceGaps(b, '1d'), []);
});
test('partial fill shrinks only the remaining interval', () => {
  const b = upGap(1); setBar(b, 31, 105, 104.5, 108);
  const out = detectPriceGaps(b, '1d')[0]; assert.deepEqual(out.geometry.remainingIntervals, [{ low: 101, high: 104.5 }]);
  assert.equal(out.status, 'testing'); assert.ok(Math.abs(out.evidence.filledFraction - 0.3) < 1e-12);
});
test('trading in the middle leaves both untraded pieces', () => {
  assert.deepEqual(subtractTradedRange([{ low: 100, high: 110 }], 103, 107), [{ low: 100, high: 103 }, { low: 107, high: 110 }]);
});
test('jumping across a gap does not pretend the missing interval traded', () => {
  assert.deepEqual(subtractTradedRange([{ low: 100, high: 110 }], 90, 99), [{ low: 100, high: 110 }]);
});
test('a fully filled gap is historical and ends at the filling candle', () => {
  const b = upGap(2); setBar(b, 31, 104, 100, 108);
  const out = detectPriceGaps(b, '1d').find(o => o.formationStart === b[30].key);
  assert.equal(out.status, 'expired'); assert.equal(out.formationEnd, b[31].key); assert.deepEqual(out.geometry.remainingIntervals, []);
});
test('unfinished gap candle is not emitted', () => {
  const b = upGap(); b[30].closed = false; assert.deepEqual(detectPriceGaps(b, '1d'), []);
});
test('missing weekday and category-axis holes do not create invented gaps', () => {
  const b = upGap(); b.splice(29, 1); assert.deepEqual(detectPriceGaps(b, '1d'), []);
});
test('intraday and weekly gaps are deliberately not inferred without a session policy', () => {
  for (const range of ['5m', '15m', '1h', '1w']) assert.deepEqual(detectPriceGaps(upGap(), range), []);
});
test('unverified huge discontinuities quarantine earlier gap structures', () => {
  const b = upGap(1); setBar(b, 31, 50, 49, 51); assert.deepEqual(detectPriceGaps(b, '1d'), []);
});
test('gap metadata states corporate actions were not independently verified', () => {
  assert.equal(detectPriceGaps(upGap(), '1d')[0].evidence.corporateActionsVerified, false);
});
test('remaining gap area can never grow as traded ranges are subtracted', () => {
  let intervals = [{ low: 100, high: 110 }], previous = 10;
  for (const [l, h] of [[107, 108], [103, 105], [109, 115], [98, 102], [101, 106]]) {
    intervals = subtractTradedRange(intervals, l, h);
    const area = intervals.reduce((s, p) => s + p.high - p.low, 0); assert.ok(area <= previous); previous = area;
  }
});
test('invalid traded ranges do not mutate the original gap', () => {
  const intervals = [{ low: 100, high: 110 }]; assert.deepEqual(subtractTradedRange(intervals, NaN, 105), intervals);
});
test('gap rendering resolves exact bar keys and suppresses unresolved geometry', () => {
  const b = upGap(), row = detectPriceGaps(b, '1d')[0];
  assert.equal(gapAreas(row, k => b.findIndex(v => v.key === k)).length, 1);
  assert.deepEqual(gapAreas(row, () => -1), []);
});
test('gap rendering rejects negative or malformed intervals', () => {
  const row = { status: 'forming', geometry: { startBarKey: 'a', endBarKey: 'b', remainingIntervals: [{ low: -1, high: 110 }] } };
  assert.deepEqual(gapAreas(row, k => k === 'a' ? 0 : 1), []);
});
test('semantic labels preserve the old role after a confirmed break', () => {
  assert.equal(semanticLabel('下降阻力', { status: 'broken_up', evidence: {} }), '原下降阻力 · 突破已确认');
  assert.equal(semanticLabel('上升支撑', { status: 'broken_down', evidence: {} }), '原上升支撑 · 跌破已确认');
});
test('pending and reference labels are explicit rather than silently recoloured', () => {
  assert.equal(semanticLabel('阻力', { status: 'testing', evidence: { visualState: 'breakout_pending' } }), '阻力 · 突破待确认');
  assert.equal(semanticLabel('阻力', { status: 'forming', evidence: { displayTier: 'secondary' } }), '阻力 · 参考');
  assert.equal(overlayTier({ evidence: { displayTier: 'unknown' } }), undefined);
});
test('primary, secondary and context boundaries have distinct visual weights', () => {
  const ink = ['primary', 'secondary', 'context'].map(tier => renderPatternInk(pattern({ tier }), geometry, ctx).lines[0][0].lineStyle);
  assert.equal(ink[0].opacity, 1); assert.ok(ink[0].width > ink[1].width && ink[1].width > ink[2].width);
  assert.ok(ink[0].opacity > ink[1].opacity && ink[1].opacity > ink[2].opacity); assert.equal(ink[0].color, LINE_INK.resistance);
});
test('last touch splits solid and dashed ink without moving the visible rail', () => {
  const out = renderPatternInk(pattern({ observedEnds: [40] }), geometry, ctx);
  assert.equal(out.lines[0][1].coord[0], 40); assert.equal(out.lines[1][0].coord[0], 40);
  assert.equal(out.lines[1][1].coord[0], 59); assert.deepEqual(out.lines[1][0].lineStyle.type, [7, 4]);
});
test('historical boundaries do not extend into current prices', () => {
  const out = renderPatternInk(pattern({ status: 'broken_up', observedEnds: [20] }), { ...geometry, segments: [{ a: { x: 0, y: 110 }, b: { x: 31, y: 105 } }] }, ctx);
  assert.equal(out.lines.length, 1); assert.equal(out.lines[0][1].coord[0], 31);
});
test('consolidation is a faint filled area with a thin neutral boundary', () => {
  const fill = [{ x: 10, y: 95 }, { x: 40, y: 95 }, { x: 40, y: 105 }, { x: 10, y: 105 }];
  const segments = fill.map((a, i) => ({ a, b: fill[(i + 1) % 4] }));
  const out = renderPatternInk(pattern({ kind: 'box', label: '整理区' }), { fill, segments }, ctx);
  assert.equal(out.areas.length, 1); assert.ok(out.areas[0][0].itemStyle.opacity >= 0.06);
  assert.ok(out.lines.every(l => l[0].lineStyle.width <= 1 && l[0].lineStyle.opacity <= 0.35));
});
test('manual widths, colours and dash patterns retain their previous contract', () => {
  const ink = manualLineInk('#123456', 3.2, [2, 5]);
  assert.equal(ink.width, 3.2); assert.equal(ink.color, '#123456'); assert.equal(ink.opacity, 1); assert.deepEqual(ink.type, [2, 5]);
});
test('malformed or crossed shapes render nothing', () => {
  const g = { segments: [{ a: { x: 0, y: 95 }, b: { x: 59, y: 110 } }, { a: { x: 0, y: 105 }, b: { x: 59, y: 100 } }], fill: null };
  assert.deepEqual(normalizePatternSegments(g.segments, 'channel'), []); assert.equal(renderPatternInk(pattern({ kind: 'channel' }), g, ctx).lines.length, 0);
});

const bounds = { x: 10, y: 10, width: 480, height: 300 };
const requests = [0, 1, 2, 3].map(i => ({ id: String(i), anchorX: 485, anchorY: 150 + i * 2, width: 145, height: 20, priority: 100 - i }));
test('dense automatic labels pack in pixel coordinates without collisions', () => {
  const out = packEndLabels(requests, bounds); assert.equal(out.length, 4); noOverlap(out);
  assert.ok(out.every(p => p.x >= bounds.x && p.y >= bounds.y && p.x + p.width <= bounds.x + bounds.width && p.y + p.height <= bounds.y + bounds.height));
});
test('price-reference and manual label rectangles are respected', () => {
  const obstacles = [{ x: 330, y: 125, width: 160, height: 23 }, { x: 330, y: 168, width: 150, height: 22 }];
  const out = packEndLabels(requests, bounds, obstacles); noOverlap(out);
  for (const p of out) assert.ok(obstacles.every(o => !rectanglesOverlap(p, o, 0)));
});
test('crowded narrow layouts omit low-priority labels rather than changing prices', () => {
  const input = requests.map(r => ({ ...r, anchorX: 190, anchorY: 35 }));
  const out = packEndLabels(input, { x: 0, y: 0, width: 200, height: 45 });
  assert.ok(out.length < input.length); assert.equal(out[0].id, '0'); assert.ok(out.every(p => p.anchorY === 35));
});
test('pixel layout is independent of renderItem call order', () => {
  assert.deepEqual(packEndLabels(requests, bounds), packEndLabels([...requests].reverse(), bounds));
});
test('invalid bounds and labels cannot create NaN render positions', () => {
  assert.deepEqual(packEndLabels(requests, { ...bounds, width: NaN }), []);
  assert.deepEqual(packEndLabels([{ ...requests[0], anchorY: NaN }], bounds), []);
});
test('Chinese and Latin labels use nonzero measured screen widths', () => {
  assert.ok(measureLabel('下降阻力 · 975.27').width > measureLabel('975').width); assert.ok(measureLabel('参考').height >= 15);
});
test('custom rail series reserves the live price label in the same packing pass', () => {
  const b = bars();
  const marks = [104, 104.2, 104.4].flatMap((price, i) => renderPatternInk(pattern({ id: `r${i}`, label: '阻力', tier: i ? 'secondary' : 'primary' }), { segments: [{ a: { x: 0, y: price }, b: { x: 59, y: price } }], fill: null }, ctx).lines);
  const series = clippedLineSeries(marks, { price: 104.1, text: '实时报价 $104.10' });
  assert.equal(series.id, 'drawing-rails'); assert.equal(series.animation, false);
  const context = {}, api = { coord: ([x, y]) => [10 + x * 8, 270 - y] };
  for (let i = marks.length - 1; i >= 0; i--) series.renderItem({ dataIndex: i, coordSys: bounds, context }, api);
  const size = measureLabel('实时报价 $104.10', 10, 14);
  const obstacle = { x: bounds.x + bounds.width - size.width - 4, y: 270 - 104.1 - size.height - 4, ...size };
  noOverlap(context.endLabels); assert.ok(context.endLabels.every(p => !rectanglesOverlap(p, obstacle, 0)));
  assert.equal(b.length, 60);
});
test('custom clipping keeps offscreen intersections without changing data geometry', () => {
  const original = [[-10, 20], [100, 20]];
  const clipped = clipLineToRect(...original, { x: 0, y: 0, width: 50, height: 50 });
  assert.ok(Math.abs(clipped[0][0]) < 1e-10 && Math.abs(clipped[1][0] - 50) < 1e-10);
  assert.equal(clipped[0][1], 20); assert.equal(clipped[1][1], 20);
  assert.deepEqual(original, [[-10, 20], [100, 20]]);
});
test('zoom/resize only changes label placement, not the fitted input coordinates', () => {
  const marks = renderPatternInk(pattern(), geometry, ctx).lines, before = JSON.stringify(marks), series = clippedLineSeries(marks);
  for (const scale of [2, 5, 8]) {
    const context = {}, api = { coord: ([x, y]) => [10 + x * scale, 270 - y] };
    for (let i = 0; i < marks.length; i++) series.renderItem({ dataIndex: i, coordSys: bounds, context }, api);
  }
  assert.equal(JSON.stringify(marks), before);
});
test('bounded deterministic stress fixture never returns intersecting label boxes', () => {
  for (let run = 0; run < 30; run++) {
    const rows = Array.from({ length: 30 }, (_, i) => ({ id: `${i}`, anchorX: 485, anchorY: 12 + ((i * 37 + run * 13) % 295), width: 100 + i % 5 * 15, height: 18, priority: 100 - i }));
    noOverlap(packEndLabels(rows, bounds));
  }
});
