import { expect, test } from '@playwright/test';
import { build } from 'esbuild';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const frontend = fileURLToPath(new URL('../', import.meta.url));
const source = path.join(frontend, 'src');
let bundle;

// Run the production renderer and chart option builder in a browser. The private
// builder is exposed only in this in-memory test bundle; product exports stay unchanged.
test.beforeAll(async () => {
  const result = await build({
    stdin: { contents: `
      export { buildOption, autoPatternName } from './components/detail/KlineChart.tsx';
      export { overlaysToMarks } from './components/detail/chart-drawings/analysis/overlaysToMarks.ts';
      export { clippedLineSeries } from './components/detail/chart-drawings/clippedLines.ts';
      export { detectPriceGaps } from './components/detail/chart-drawings/analysis/priceGaps.ts';
      export { closedBarsForFingerprint, filterOverlays, labelBudget } from './components/detail/chart-drawings/analysis/mapBundle.ts';
      export { detectSmartLines, selectSmartOverlays, withChartIndices } from './components/detail/chart-drawings/analysis/smartLines.ts';
      export { railCandidatesFromOverlays, candidatesAtBar } from './components/detail/chart-drawings/railSnap.ts';
      export { snapCandidatesFromOverlays, snapPointer, visiblePriceToY } from './components/detail/chart-drawings/snap.ts';
      export { echarts } from './lib/chart.ts';
      import React from 'react';
      import { createRoot } from 'react-dom/client';
      import AnalysisLegend from './components/detail/chart-drawings/AnalysisLegend.tsx';
      export function renderLegend(overlays, smartEnabled = true) {
        createRoot(document.getElementById('legend')).render(React.createElement(AnalysisLegend, { overlays, smartEnabled }));
      }
    `, resolveDir: source, loader: 'tsx' },
    bundle: true, write: false, platform: 'browser', format: 'iife', globalName: 'DrawingsReview', jsx: 'automatic',
    alias: { '@': source },
    define: { 'process.env.NODE_ENV': '"production"', 'import.meta.env': '{"MODE":"test","DEV":false,"VITE_API_MODE":"mock"}' },
    plugins: [{ name: 'expose-chart-option-builder', setup(builder) {
      builder.onLoad({ filter: /\/KlineChart\.tsx$/ }, ({ path: filename }) => ({
        contents: fs.readFileSync(filename, 'utf8') + '\nexport { buildOption, autoPatternName };\n',
        loader: 'tsx', resolveDir: path.dirname(filename),
      }));
    } }],
  });
  bundle = result.outputFiles[0].text;
});

async function harness(page, locale = 'zh') {
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  await page.route('http://drawings.test/**', route => route.fulfill({ contentType: 'text/html', body: `
    <!doctype html><html><head><meta charset="utf-8"><style>
    body{margin:0;background:white;font:14px system-ui;color:#1e293b}#chart{height:680px;width:100vw}
    #legend{margin:16px;line-height:1.7}#legend p{display:flex;flex-wrap:wrap;gap:12px}#legend span{display:inline-flex;align-items:center;gap:4px}
    #legend span[aria-hidden]{width:16px;border-top:3px solid}
    </style></head><body><div id="chart" role="img" aria-label="K-line chart"></div><div id="legend"></div></body></html>` }));
  await page.goto('http://drawings.test/');
  await page.evaluate(locale => localStorage.setItem('optix:locale', locale), locale);
  await page.addScriptTag({ content: bundle });
  await page.evaluate(() => {
    const R = DrawingsReview;
    window.bars = Array.from({ length: 160 }, (_, i) => {
      const c = 100 + 20 * Math.sin(i / 12) + i * 0.12;
      const t = new Date(Date.UTC(2026, 0, 1 + i, 21)).toISOString();
      return { t, key: t.slice(0, 10), o: c - 0.8, h: c + 4, l: c - 4, c, v: 1000 + i, closed: true };
    });
    window.ctx = { bars, range: '1d', xMin: 0, xMax: 159, yMin: 70, yMax: 145 };
    window.anchor = (i, price) => ({ time: bars[i].t, barKey: bars[i].key, price });
    window.overlay = (extra = {}) => ({
      id: 'line', kind: 'support_trend', status: 'forming', shapeQuality: 0.85, displayPriority: 85, evidence: { touches: 4 },
      formationStart: bars[8].key, formationEnd: bars[40].key,
      geometry: { anchors: [anchor(8, 96), anchor(40, 112)] }, ...extra,
    });
    window.chart = R.echarts.init(document.getElementById('chart'), undefined, { renderer: 'canvas' });
    window.paint = (marks, input = bars, range = '1d', zoom = { start: 0, end: input.length - 1, pinnedEnd: false }) => {
      const option = R.buildOption(input, [], range, 'candle', undefined, null, marks, { showMa20: false }, zoom);
      option.animation = false;
      chart.setOption(option, { notMerge: true }); chart.getZr().flush();
      const display = chart.getZr().storage.getDisplayList(true);
      return {
        lines: display.filter(item => ['line', 'ec-line'].includes(item.type) && ['#0E647F', '#8D299B'].includes(item.style?.stroke) && item.style?.lineWidth >= 1)
          .map(item => ({ color: item.style.stroke, width: item.style.lineWidth, dash: item.style.lineDash, opacity: item.style.opacity, shape: { ...item.shape } })),
        labels: display.filter(item => item.type === 'tspan' && item.style?.text?.includes('Review')).map(item => item.style.text),
        polygons: display.filter(item => item.type === 'polygon' && item.style?.opacity === 0.04).length,
        zoom: chart.getOption().dataZoom[0],
      };
    };
  });
  return errors;
}

for (const width of [1440, 390]) {
  test(`real chart preserves confirmed, projected and invalidated line styles at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 850 });
    const errors = await harness(page);
    const result = await page.evaluate(() => {
      const R = DrawingsReview;
      const draw = row => paint(R.overlaysToMarks([row], ctx, () => 'Review', new Set([row.id])));
      const active = draw(overlay());
      const broken = draw(overlay({ status: 'broken_down' }));
      const level = overlay({ id: 'broken-level', kind: 'level', status: 'broken_down', geometry: { price: 100, role: 'support' } });
      const levelMarks = R.overlaysToMarks([level], ctx, () => 'Review', new Set([level.id]));
      const historicalLevel = paint(levelMarks);
      const channel = overlay({ kind: 'channel', geometry: { anchors: [anchor(10, 94), anchor(40, 103), anchor(10, 105), anchor(40, 114)] } });
      const paired = draw(channel);
      const projectedMidpoint = (chart.convertToPixel({ xAxisIndex: 0, yAxisIndex: 0 }, [62, 109.75])[0]
        + chart.convertToPixel({ xAxisIndex: 0, yAxisIndex: 0 }, [63, 109.75])[0]) / 2;
      const marks = R.overlaysToMarks([channel], ctx);
      const zoomed = paint(marks, bars, '1d', { start: 20, end: 55, pinnedEnd: false });
      const entirelyInside = paint(marks, bars, '1d', { start: 20, end: 35, pinnedEnd: false });
      return { active, broken, historicalLevel, levelCoordinates: levelMarks.lines[0].map(point => point.coord),
        levelSnaps: R.snapCandidatesFromOverlays([level]), paired, projectedMidpoint, zoomed, entirelyInside };
    });
    expect(result.active.lines).toHaveLength(2);
    expect(result.active.lines[0].width).toBe(2.4);
    expect(result.active.lines[1].dash).toEqual([7, 4]);
    expect(result.active.labels).toHaveLength(1);
    expect(result.broken.lines).toHaveLength(1);
    expect(result.broken.lines[0]).toMatchObject({ opacity: 0.3, dash: [2, 4] });
    expect(result.historicalLevel.lines).toHaveLength(1);
    expect(result.historicalLevel.lines[0]).toMatchObject({ opacity: 0.3, dash: [2, 4] });
    expect(result.levelCoordinates).toEqual([[8, 100], [40, 100]]);
    expect(result.levelSnaps).toEqual([]);
    expect(result.paired.lines).toHaveLength(4);
    expect(result.paired.polygons).toBe(1);
    expect(new Set(result.paired.lines.map(line => line.color))).toEqual(new Set(['#0E647F', '#8D299B']));
    const extension = result.paired.lines.find(line => line.color === '#0E647F' && line.dash?.[0] === 7);
    expect(extension.shape.x2).toBeCloseTo(result.projectedMidpoint, 6);
    expect(result.zoomed.zoom).toMatchObject({ startValue: 20, endValue: 55 });
    expect(result.zoomed.lines.length).toBeGreaterThanOrEqual(2);
    expect(result.entirelyInside.lines).toHaveLength(2);
    expect(errors).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath(`channel-zoom-${width}.png`) });
  });

  test(`minute fitting, visible rails and snapping share the full axis at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 850 });
    const errors = await harness(page);
    const result = await page.evaluate(() => {
      const R = DrawingsReview, input = [];
      for (let i = 0; i < 240; i++) {
        const day = Math.floor(i / 78), slot = i % 78;
        const t = new Date(Date.UTC(2026, 0, 5 + day, 14, 30 + slot * 5)).toISOString();
        const c = 100 + input.length * 0.15 + 7 * Math.sin(i * 2 * Math.PI / 24);
        input.push({ t, key: t, o: c, c, h: c + 0.5, l: c - 0.5, v: 100, closed: true });
        const extra = i === 77 ? 12 : i === 155 ? 48 : 0;
        for (let j = 0; j < extra; j++) {
          const t = new Date(Date.UTC(2026, 0, 5 + day, 21, j * 5)).toISOString();
          input.push({ t, key: t, o: c, c, h: c + 1, l: c - 1, v: 10, ext: true, closed: true });
        }
      }
      const eligible = R.withChartIndices(R.closedBarsForFingerprint(input, '5m'), input.map(bar => bar.key));
      const settings = { enabled: ['auto_patterns', 'support_resistance'], minShapeQuality: 0.45,
        onlyActive: true, showInvalidated: false, maxPatterns: 4, maxLabels: 4, labelDensity: 1, preset: 'custom' };
      const selected = R.selectSmartOverlays(R.filterOverlays(R.detectSmartLines(eligible), { ...settings, maxPatterns: 64 }), eligible, 4);
      const context = { bars: input, range: '5m', xMin: 0, xMax: input.length - 1, yMin: 85, yMax: 155 };
      const reports = selected.filter(row => row.kind === 'channel').map(row => {
        const own = R.overlaysToMarks([row], context);
        const targets = R.railCandidatesFromOverlays([row], input.map(bar => bar.key));
        const errors = row.geometry.touchAnchors.map(touch => {
          const x = input.findIndex(bar => bar.key === touch.barKey);
          const prices = own.lines.filter(line => line[0].coord[0] <= x && x <= line[1].coord[0]).map(line => {
            const [a, b] = line.map(point => point.coord);
            return a[1] + (b[1] - a[1]) * (x - a[0]) / (b[0] - a[0]);
          });
          return { render: Math.min(...prices.map(price => Math.abs(price - touch.price))),
            snap: Math.min(...R.candidatesAtBar(targets, x).map(target => Math.abs(target.price - touch.price))) };
        });
        return { tolerance: row.evidence.tolerance, targets: targets.length, errors };
      });
      const labels = new Set(R.labelBudget(selected, settings).map(row => row.id));
      const marks = R.overlaysToMarks(selected, context, () => 'Review', labels);
      const actual = paint(marks, input, '5m');
      return { eligible: eligible.length, total: input.length, reports, actual };
    });
    expect(result.eligible).toBe(240); expect(result.total).toBe(300);
    expect(result.reports.length).toBeGreaterThan(0);
    for (const report of result.reports) {
      expect(report.targets).toBe(2); expect(report.errors.length).toBeGreaterThanOrEqual(5);
      for (const error of report.errors) {
        expect(error.render).toBeLessThanOrEqual(report.tolerance + 1e-8);
        expect(error.snap).toBeLessThanOrEqual(report.tolerance + 1e-8);
      }
    }
    expect(result.actual.lines.length).toBeGreaterThanOrEqual(2);
    expect(result.actual.polygons).toBeGreaterThanOrEqual(1);
    expect(errors).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath(`minute-full-axis-${width}.png`) });
    await testInfo.attach('minute-geometry', { body: JSON.stringify(result, null, 2), contentType: 'application/json' });
  });
}

for (const [locale, support, resistance, description] of [
  ['zh', '支撑', '阻力', '深色为主要边界，细线为参考；虚线为延伸，淡色点线为历史结构'],
  ['en', 'Support', 'Resistance', 'Dark: primary; thin: secondary; dashed: extension; faint dotted: historical'],
  ['ja', 'サポート', 'レジスタンス', '濃色：主要境界、細線：参考、破線：延長、薄い点線：過去の構造'],
]) test(`level-only legend is visible and translated in ${locale}`, async ({ page }) => {
  const errors = await harness(page, locale);
  await page.evaluate(() => DrawingsReview.renderLegend([{ kind: 'level' }], false));
  const legend = page.locator('#legend');
  await expect(legend.getByText(support, { exact: true })).toBeVisible();
  await expect(legend.getByText(resistance, { exact: true })).toBeVisible();
  await expect(legend.getByText(description, { exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});

test('unrenderable channel geometry has no ink, snap target or deduplication priority', async ({ page }) => {
  const errors = await harness(page);
  const result = await page.evaluate(() => {
    const R = DrawingsReview;
    const valid = overlay({ id: 'valid' });
    const invalid = overlay({ id: 'invalid', kind: 'channel', shapeQuality: 0.99,
      geometry: { anchors: [anchor(8, 96), anchor(40, 112), anchor(8, 110), anchor(40, 90)] } });
    const marks = R.overlaysToMarks([invalid], ctx);
    const actual = paint(marks);
    return { lines: marks.lines.length, polygons: actual.polygons, visibleLines: actual.lines.length,
      snaps: R.railCandidatesFromOverlays([invalid], bars.map(bar => bar.key)),
      selected: R.selectSmartOverlays([invalid, valid], bars, 4).map(row => row.id) };
  });
  expect(result).toEqual({ lines: 0, polygons: 0, visibleLines: 0, snaps: [], selected: ['valid'] });
  expect(errors).toEqual([]);
});

test('a nearby price beyond the real plot edge cannot attract snapping', async ({ page }) => {
  const errors = await harness(page);
  const result = await page.evaluate(() => {
    const R = DrawingsReview;
    paint({ lines: [], points: [], areas: [], polygons: [] });
    const rect = chart.getModel().getComponent('grid', 0).coordinateSystem.getRect();
    const x = chart.convertToPixel({ xAxisIndex: 0, yAxisIndex: 0 }, [40, 100])[0];
    const priceAt = y => chart.convertFromPixel({ xAxisIndex: 0, yAxisIndex: 0 }, [x, y])[1];
    const pointerY = rect.y + 3, offscreenPrice = priceAt(rect.y - 2), visiblePrice = priceAt(rect.y + 6);
    const base = { x: 40, y: pointerY, barCount: bars.length, pointerPrice: priceAt(pointerY), thresholdPx: 10,
      alt: false, priceToY: price => R.visiblePriceToY(chart, 40, price) };
    return {
      physicalDistance: Math.abs(chart.convertToPixel({ gridIndex: 0 }, [40, offscreenPrice])[1] - pointerY),
      outside: R.snapPointer({ ...base, candidates: [{ price: offscreenPrice, kind: 'level' }] }),
      inside: R.snapPointer({ ...base, candidates: [{ price: visiblePrice, kind: 'level' }] }),
      visiblePrice,
    };
  });
  expect(result.physicalDistance).toBeLessThan(10);
  expect(result.outside.snapped).toBe(false);
  expect(result.inside.snapped).toBe(true);
  expect(result.inside.price).toBe(result.visiblePrice);
  expect(errors).toEqual([]);
});

for (const width of [390, 1440]) {
  test(`crowded labels avoid manual text and moving quotes through zoom and resize at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 850 });
    const errors = await harness(page);
    const initial = await page.evaluate(() => {
      const R = DrawingsReview;
      const auto = Array.from({ length: 7 }, (_, i) => [
        { coord: [20, 110 + i * 0.24], clipToPlot: true,
          lineStyle: { color: '#0E647F', width: 2.4, opacity: 0.9 },
          label: { show: true, formatter: `ReviewAuto ${i}`, fontSize: 11, lineHeight: 14,
            priority: 100 - i, color: '#0E647F', backgroundColor: '#fff' } },
        { coord: [159, 110 + i * 0.24] },
      ]);
      const manual = [
        { coord: [120, 110.6], lineStyle: { color: '#f97316', width: 1.6 },
          label: { show: true, formatter: 'ReviewManual', position: 'insideEndTop', distance: 4,
            fontSize: 11, lineHeight: 14, color: '#f97316', backgroundColor: '#fff', padding: [2, 5] } },
        { coord: [159, 110.6] },
      ];
      window.packedMarks = { lines: [...auto, manual], points: [], areas: [], polygons: [] };
      window.originalMarks = JSON.stringify(packedMarks);
      paint(packedMarks);
      window.updateReference = price => {
        const text = `ReviewQuote $${price.toFixed(2)}`;
        chart.setOption({ series: [R.clippedLineSeries(packedMarks.lines, { price, text }), {
          id: 'realtime-price-reference', type: 'line', xAxisIndex: 0, yAxisIndex: 0,
          data: [[159, price]], showSymbol: false, lineStyle: { opacity: 0 }, silent: true, animation: false,
          markLine: { symbol: 'none', animation: false, data: [{ yAxis: price }],
            label: { show: true, formatter: text, position: 'insideEndTop', fontSize: 10 },
            lineStyle: { color: '#2E46E0', type: 'dashed', width: 1 } },
        }] }, { notMerge: false, lazyUpdate: false });
        chart.getZr().flush();
      };
      window.inspectPacked = () => {
        const labels = chart.getZr().storage.getDisplayList(true)
          .filter(item => item.type === 'tspan' && /^Review(Auto|Manual|Quote)/.test(item.style?.text ?? ''))
          .map(item => {
            const rect = item.getBoundingRect().clone();
            rect.applyTransform(item.getComputedTransform());
            // Include the real text padding, beyond the glyph rectangle.
            const quote = item.style.text.startsWith('ReviewQuote');
            return { text: item.style.text, x: rect.x - (quote ? 0 : 5), y: rect.y - (quote ? 0 : 2),
              width: rect.width + (quote ? 0 : 10), height: rect.height + (quote ? 0 : 4) };
          });
        const grid = chart.getModel().getComponent('grid', 0).coordinateSystem.getRect();
        return { labels, grid: { x: grid.x, y: grid.y, width: grid.width, height: grid.height },
          zoom: chart.getOption().dataZoom[0], unchanged: originalMarks === JSON.stringify(packedMarks) };
      };
      updateReference(111);
      return inspectPacked();
    });
    const moved = await page.evaluate(() => { updateReference(108.5); return inspectPacked(); });
    const zoomed = await page.evaluate(() => {
      chart.dispatchAction({ type: 'dataZoom', startValue: 100, endValue: 159 });
      chart.getZr().flush(); return inspectPacked();
    });
    await page.setViewportSize({ width: width === 390 ? 768 : 390, height: 850 });
    const resized = await page.evaluate(() => { chart.resize(); chart.getZr().flush(); return inspectPacked(); });
    await testInfo.attach('label-bounds', { body: JSON.stringify({ initial, moved, zoomed, resized }, null, 2), contentType: 'application/json' });
    const overlaps = (a, b) => a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y;
    for (const snapshot of [initial, moved, zoomed, resized]) {
      const auto = snapshot.labels.filter(label => label.text.startsWith('ReviewAuto'));
      const obstacles = snapshot.labels.filter(label => !label.text.startsWith('ReviewAuto'));
      expect(auto.length).toBeGreaterThanOrEqual(3);
      expect(obstacles.map(label => label.text.split(' ')[0]).sort()).toEqual(['ReviewManual', 'ReviewQuote']);
      for (let i = 0; i < auto.length; i++) {
        for (const other of [...auto.slice(i + 1), ...obstacles]) expect(overlaps(auto[i], other), `${auto[i].text} overlaps ${other.text}`).toBe(false);
        expect(auto[i].x).toBeGreaterThanOrEqual(snapshot.grid.x);
        expect(auto[i].x + auto[i].width).toBeLessThanOrEqual(snapshot.grid.x + snapshot.grid.width);
        expect(auto[i].y).toBeGreaterThanOrEqual(snapshot.grid.y);
        expect(auto[i].y + auto[i].height).toBeLessThanOrEqual(snapshot.grid.y + snapshot.grid.height);
      }
      expect(snapshot.unchanged).toBe(true);
    }
    expect(moved.zoom).toMatchObject({ startValue: 0, endValue: 159 });
    expect(zoomed.zoom).toMatchObject({ startValue: 100, endValue: 159 });
    expect(resized.zoom).toMatchObject({ startValue: 100, endValue: 159 });
    expect(initial.labels.find(label => label.text === 'ReviewManual')).toEqual(moved.labels.find(label => label.text === 'ReviewManual'));
    expect(errors).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath(`packed-labels-${width}.png`) });
  });
}

test('partially filled daily gap paints only its remaining interval below the candles', async ({ page }, testInfo) => {
  const errors = await harness(page);
  const result = await page.evaluate(() => {
    const R = DrawingsReview, input = [];
    for (let day = 0; input.length < 32; day++) {
      const date = new Date(Date.UTC(2026, 0, 5 + day, 21));
      if ([0, 6].includes(date.getUTCDay())) continue;
      const t = date.toISOString();
      input.push({ t, key: t.slice(0, 10), o: 100, h: 101, l: 99, c: 100, v: 1000, closed: true });
    }
    Object.assign(input[30], { o: 107, h: 108, l: 106, c: 107 });
    Object.assign(input[31], { o: 107, h: 108, l: 104.5, c: 105 });
    const detected = R.detectPriceGaps(input, '1d');
    const marks = R.overlaysToMarks(detected, { bars: input, range: '1d', xMin: 0, xMax: 31, yMin: 95, yMax: 110 });
    paint(marks, input);
    const display = chart.getZr().storage.getDisplayList(true);
    const gaps = display.filter(item => item.style?.fill === 'rgba(184,120,33,0.10)');
    const candles = chart.getModel().getSeries().find(series => series.subType === 'candlestick');
    const low = chart.convertToPixel({ xAxisIndex: 0, yAxisIndex: 0 }, [31, 101])[1];
    const high = chart.convertToPixel({ xAxisIndex: 0, yAxisIndex: 0 }, [31, 104.5])[1];
    return { detected: detected.map(row => ({ status: row.status, intervals: row.geometry.remainingIntervals })),
      gaps: gaps.map(item => ({ type: item.type, z: item.z, points: item.shape.points, fill: item.style.fill })),
      candleZ: candles.get('z'), low, high };
  });
  await testInfo.attach('gap-bounds', { body: JSON.stringify(result, null, 2), contentType: 'application/json' });
  expect(result.detected).toEqual([{ status: 'testing', intervals: [{ low: 101, high: 104.5 }] }]);
  expect(result.gaps).toHaveLength(1);
  expect(Math.min(...result.gaps[0].points.map(point => point[1]))).toBeCloseTo(result.high, 3);
  expect(Math.max(...result.gaps[0].points.map(point => point[1]))).toBeCloseTo(result.low, 3);
  expect(result.gaps[0].z).toBeLessThan(result.candleZ);
  expect(errors).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath('partial-gap.png') });
});
