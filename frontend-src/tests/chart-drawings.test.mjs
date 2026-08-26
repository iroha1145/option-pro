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
const drawingsDir = path.join(src, 'components', 'detail', 'chart-drawings');

async function loadDrawings(t) {
  const tempRoot = await mkdtemp(path.join(tmpdir(), 'optix-drawings-'));
  t.after(() => rm(tempRoot, { recursive: true, force: true }));
  const entry = path.join(tempRoot, 'entry.ts');
  const output = path.join(tempRoot, 'drawings.mjs');
  const files = {
    geometry: path.join(drawingsDir, 'geometry.ts'),
    projection: path.join(drawingsDir, 'projection.ts'),
    hitTest: path.join(drawingsDir, 'hitTest.ts'),
    snap: path.join(drawingsDir, 'snap.ts'),
    schema: path.join(drawingsDir, 'schema.ts'),
    history: path.join(drawingsDir, 'history.ts'),
    storage: path.join(drawingsDir, 'storage.ts'),
    tools: path.join(drawingsDir, 'tools.ts'),
    renderer: path.join(drawingsDir, 'renderer.ts'),
    autoPatterns: path.join(drawingsDir, 'autoPatterns.ts'),
    sync: path.join(drawingsDir, 'sync.ts'),
    drag: path.join(drawingsDir, 'drag.ts'),
  };
  await writeFile(
    entry,
    `
export {
  horizontalProjection, clipRayToRect, channelEdges, channelOffset,
  vectorsParallel, moveChannelWhole, moveChannelAnchor, normalizeRectangle,
  fibonacciPrices, constrainByShift, applyAltNoSnap, distancePointToSegment, nudgePoint,
} from ${JSON.stringify(files.geometry)};
export {
  barKeyOf, resolveBarKey, resolveAnchor, snapBarIndex, drawingScopeKey, drawingsInScope, nudgeAnchors,
} from ${JSON.stringify(files.projection)};
export {
  hitTestProjected, pickTopHit, hitTestDrawings, DESKTOP_LINE_TOLERANCE_PX, TOUCH_LINE_TOLERANCE_PX,
} from ${JSON.stringify(files.hitTest)};
export { snapPointer, nearestPrice } from ${JSON.stringify(files.snap)};
export { parseDrawing, parseDrawingDetailed, validateImport, migrateStoredPayload, whitelistText, whitelistStyle, exportDrawings, resolvePaintColor } from ${JSON.stringify(files.schema)};
export { createHistory, historyPush, historyUndo, historyRedo, historyReplace, canUndo, canRedo } from ${JSON.stringify(files.history)};
export { loadDrawings, saveDrawings, anonymousStorageKey, drawingsStorageKey } from ${JSON.stringify(files.storage)};
export { addDraftPoint, applyShiftToDraft, exclusiveTool, isTextInputTarget } from ${JSON.stringify(files.tools)};
export {
  drawingsToMarks, drawingSegments, toProjectedDrawing, overlayMarks, selectionOverlay,
  draftOverlay, graphicFromOverlay, autoPatternGeometry, autoPatternsToMarks, fillIsAxisAligned,
} from ${JSON.stringify(files.renderer)};
export { mapAutoPatterns, mapAutoPatternItem, mapTechnicalAutoFields } from ${JSON.stringify(files.autoPatterns)};
export {
  DrawingOutbox, diffPersistOps, mutableFieldsDiffer, applyPersistResponse,
  resolveListApply, resolveRetryAction, SCOPE_JOB_ID,
} from ${JSON.stringify(files.sync)};
export { dragMove, previewDragAnchors, applyPixelShiftConstraint } from ${JSON.stringify(files.drag)};
`,
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

const rect = { xMin: 0, xMax: 100, yMin: 10, yMax: 50 };

test('horizontal projection spans the visible x range at a price', async (t) => {
  const { horizontalProjection } = await loadDrawings(t);
  const line = horizontalProjection(42, 0, 80);
  assert.equal(line.a.y, 42);
  assert.equal(line.b.y, 42);
  assert.equal(line.a.x, 0);
  assert.equal(line.b.x, 80);
});

test('segment endpoints stay at the two anchors', async (t) => {
  const { drawingSegments } = await loadDrawings(t);
  const bars = [
    { t: '2026-07-06T13:30:00Z', o: 1, h: 2, l: 1, c: 1.5 },
    { t: '2026-07-07T13:30:00Z', o: 1.5, h: 3, l: 1, c: 2 },
  ];
  const drawing = {
    schemaVersion: 1,
    id: '11111111-1111-4111-8111-111111111111',
    ticker: 'NVDA',
    range: '1d',
    adjustment: 'raw',
    kind: 'segment',
    anchors: [
      { time: '2026-07-06T13:30:00Z', barKey: '2026-07-06', price: 10 },
      { time: '2026-07-07T13:30:00Z', barKey: '2026-07-07', price: 20 },
    ],
    style: { color: '#2E46E0', width: 2, dash: 'solid' },
    locked: false,
    hidden: false,
    zOrder: 0,
    revision: 1,
    createdAt: '',
    updatedAt: '',
  };
  const geom = drawingSegments(drawing, { bars, range: '1d', xMin: 0, xMax: 1, yMin: 5, yMax: 30 });
  assert.equal(geom.segments.length, 1);
  assert.equal(geom.segments[0].a.x, 0);
  assert.equal(geom.segments[0].b.x, 1);
  assert.equal(geom.segments[0].a.y, 10);
  assert.equal(geom.segments[0].b.y, 20);
});

test('ray intersects the visible price grid instead of a huge fake coordinate', async (t) => {
  const { clipRayToRect } = await loadDrawings(t);
  const clipped = clipRayToRect({ x: 10, y: 20 }, { x: 20, y: 25 }, rect);
  assert.ok(clipped);
  assert.ok(clipped.b.x <= rect.xMax + 1e-9);
  assert.ok(clipped.b.x >= rect.xMin - 1e-9);
  assert.ok(clipped.b.y <= rect.yMax + 1e-9);
  assert.ok(clipped.b.y >= rect.yMin - 1e-9);
  assert.ok(Math.hypot(clipped.b.x, clipped.b.y) < 1e6);
});

test('channel stays parallel through whole-move and single-anchor move', async (t) => {
  const { channelEdges, moveChannelWhole, moveChannelAnchor, vectorsParallel } = await loadDrawings(t);
  const p1 = { x: 0, y: 10 };
  const p2 = { x: 10, y: 20 };
  const p3 = { x: 2, y: 4 };
  const base = channelEdges(p1, p2, p3);
  assert.equal(vectorsParallel(base.main.a, base.main.b, base.parallel.a, base.parallel.b), true);
  const [w1, w2, w3] = moveChannelWhole(p1, p2, p3, 3, -2);
  const moved = channelEdges(w1, w2, w3);
  assert.equal(vectorsParallel(moved.main.a, moved.main.b, moved.parallel.a, moved.parallel.b), true);
  const [a1, a2, a3] = moveChannelAnchor(p1, p2, p3, 2, { x: 4, y: 0 });
  const after = channelEdges(a1, a2, a3);
  assert.equal(vectorsParallel(after.main.a, after.main.b, after.parallel.a, after.parallel.b), true);
  const [b1, b2, b3] = moveChannelAnchor(p1, p2, p3, 0, { x: -2, y: 8 });
  const afterP1 = channelEdges(b1, b2, b3);
  assert.equal(vectorsParallel(afterP1.main.a, afterP1.main.b, afterP1.parallel.a, afterP1.parallel.b), true);
});

test('inverted rectangle normalizes min/max', async (t) => {
  const { normalizeRectangle } = await loadDrawings(t);
  const box = normalizeRectangle({ x: 9, y: 40 }, { x: 2, y: 10 });
  assert.equal(box.x0, 2);
  assert.equal(box.x1, 9);
  assert.equal(box.y0, 10);
  assert.equal(box.y1, 40);
});

test('fibonacci prices both directions include required ratios', async (t) => {
  const { fibonacciPrices } = await loadDrawings(t);
  const up = fibonacciPrices(100, 200);
  const down = fibonacciPrices(200, 100);
  const ratios = up.map((item) => item.ratio);
  for (const required of [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1]) {
    assert.ok(ratios.includes(required));
  }
  assert.equal(up.find((item) => item.ratio === 0).price, 100);
  assert.equal(up.find((item) => item.ratio === 1).price, 200);
  assert.equal(up.find((item) => item.ratio === 0.5).price, 150);
  assert.equal(down.find((item) => item.ratio === 0).price, 200);
  assert.equal(down.find((item) => item.ratio === 1).price, 100);
  assert.equal(down.find((item) => item.ratio === 0.5).price, 150);
});

test('hit-test distance uses CSS px tolerances for desktop and touch', async (t) => {
  const { hitTestProjected, DESKTOP_LINE_TOLERANCE_PX, TOUCH_LINE_TOLERANCE_PX } = await loadDrawings(t);
  assert.ok(DESKTOP_LINE_TOLERANCE_PX >= 6 && DESKTOP_LINE_TOLERANCE_PX <= 10);
  assert.ok(TOUCH_LINE_TOLERANCE_PX >= 16 && TOUCH_LINE_TOLERANCE_PX <= 24);
  const drawing = {
    id: 'a',
    zOrder: 1,
    locked: false,
    hidden: false,
    anchors: [{ x: 0, y: 0 }, { x: 100, y: 0 }],
    segments: [{ a: { x: 0, y: 0 }, b: { x: 100, y: 0 } }],
    fills: [],
  };
  const near = hitTestProjected(drawing, { x: 50, y: 7 }, 'mouse');
  assert.equal(near?.kind, 'body');
  const far = hitTestProjected(drawing, { x: 50, y: 20 }, 'mouse');
  assert.equal(far, null);
  const touch = hitTestProjected(drawing, { x: 50, y: 18 }, 'touch');
  assert.equal(touch?.kind, 'body');
  // High DPR does not shrink CSS-pixel tolerance: 7 CSS px still hits.
  const hidpi = hitTestProjected(drawing, { x: 50, y: 7 }, 'mouse');
  assert.ok(hidpi);
});

test('hit-test prefers anchor over selection over zOrder', async (t) => {
  const { pickTopHit } = await loadDrawings(t);
  const hits = [
    { id: 'low', kind: 'body', anchorIndex: -1, distance: 1, zOrder: 1 },
    { id: 'high', kind: 'body', anchorIndex: -1, distance: 4, zOrder: 5 },
    { id: 'sel', kind: 'body', anchorIndex: -1, distance: 3, zOrder: 2 },
    { id: 'anchor', kind: 'anchor', anchorIndex: 0, distance: 6, zOrder: 0 },
  ];
  assert.equal(pickTopHit(hits, null).id, 'anchor');
  const withoutAnchor = hits.filter((item) => item.kind !== 'anchor');
  assert.equal(pickTopHit(withoutAnchor, 'sel').id, 'sel');
  assert.equal(pickTopHit(withoutAnchor, null).id, 'high');
});

test('hidden drawings are not hittable; locked drawings are not dragged', async (t) => {
  const { hitTestProjected } = await loadDrawings(t);
  const hidden = {
    id: 'h',
    zOrder: 9,
    locked: false,
    hidden: true,
    anchors: [{ x: 0, y: 0 }],
    segments: [{ a: { x: 0, y: 0 }, b: { x: 10, y: 0 } }],
    fills: [],
  };
  assert.equal(hitTestProjected(hidden, { x: 0, y: 0 }, 'mouse'), null);
});

test('barKey resolves exactly and unresolvable anchors do not migrate', async (t) => {
  const { resolveBarKey, resolveAnchor, barKeyOf } = await loadDrawings(t);
  const bars = [
    { t: '2026-07-06T13:30:00Z' },
    { t: '2026-07-07T13:30:00Z' },
  ];
  assert.equal(barKeyOf(bars[0], '1d'), '2026-07-06');
  assert.equal(resolveBarKey(bars, '2026-07-06', '1d'), 0);
  assert.equal(resolveBarKey(bars, '2026-07-08', '1d'), -1);
  assert.equal(resolveAnchor(bars, { time: '2026-07-08T13:30:00Z', barKey: '2026-07-08', price: 1 }, '1d'), -1);
  assert.equal(barKeyOf({ t: '2026-07-06T14:35:00Z' }, '15m'), '2026-07-06T14:35:00Z');
});

test('unresolved drawings are omitted from marks rather than snapped', async (t) => {
  const { drawingsToMarks } = await loadDrawings(t);
  const bars = [{ t: '2026-07-06T13:30:00Z', o: 1, h: 2, l: 1, c: 1.5 }];
  const drawing = {
    schemaVersion: 1,
    id: '11111111-1111-4111-8111-111111111111',
    ticker: 'NVDA',
    range: '1d',
    adjustment: 'raw',
    kind: 'horizontal',
    anchors: [{ time: '2026-07-01T13:30:00Z', barKey: '2026-07-01', price: 10 }],
    style: { color: '#2E46E0', width: 2, dash: 'solid' },
    locked: false,
    hidden: false,
    zOrder: 0,
    revision: 1,
    createdAt: '',
    updatedAt: '',
  };
  const marks = drawingsToMarks([drawing], { bars, range: '1d', xMin: 0, xMax: 0, yMin: 1, yMax: 2 });
  assert.deepEqual(marks.unresolvedIds, [drawing.id]);
  assert.equal(marks.lines.length, 0);
});

test('ticker/range/adjustment isolation', async (t) => {
  const { drawingsInScope } = await loadDrawings(t);
  const list = [
    { ticker: 'NVDA', range: '1d', adjustment: 'raw' },
    { ticker: 'NVDA', range: '1w', adjustment: 'raw' },
    { ticker: 'AAPL', range: '1d', adjustment: 'raw' },
  ];
  assert.equal(drawingsInScope(list, 'NVDA', '1d').length, 1);
  assert.equal(drawingsInScope(list, 'nvda', '1w').length, 1);
});

test('corrupt local storage is ignored', async (t) => {
  const { loadDrawings: load } = await loadDrawings(t);
  const memory = new Map();
  const storage = {
    getItem: (key) => memory.get(key) ?? null,
    setItem: (key, value) => memory.set(key, value),
    removeItem: (key) => memory.delete(key),
  };
  memory.set('k', '{not json');
  const result = load('k', storage);
  assert.equal(result.ok, false);
  assert.equal(result.error, 'corrupt');
  assert.deepEqual(result.drawings, []);
});

test('schema migrate accepts v1 envelope and raw array', async (t) => {
  const { migrateStoredPayload, parseDrawing } = await loadDrawings(t);
  const drawing = {
    schemaVersion: 1,
    id: '11111111-1111-4111-8111-111111111111',
    ticker: 'NVDA',
    range: '1d',
    adjustment: 'raw',
    kind: 'horizontal',
    anchors: [{ time: '2026-07-06T13:30:00Z', barKey: '2026-07-06', price: 10 }],
    style: { color: '#2E46E0', width: 2, dash: 'solid' },
    locked: false,
    hidden: false,
    zOrder: 0,
  };
  assert.ok(parseDrawing(drawing));
  const wrapped = migrateStoredPayload({ schemaVersion: 1, drawings: [drawing] });
  assert.equal(wrapped.ok, true);
  assert.equal(wrapped.value.length, 1);
  const array = migrateStoredPayload([drawing]);
  assert.equal(array.ok, true);
});

test('undo and redo restore drawings; replace does not pollute', async (t) => {
  const { createHistory, historyPush, historyUndo, historyRedo, historyReplace, canUndo } = await loadDrawings(t);
  let state = createHistory([1]);
  state = historyPush(state, [1, 2]);
  state = historyPush(state, [1, 2, 3]);
  state = historyReplace(state, [1, 2, 3]);
  assert.equal(canUndo(state), true);
  state = historyUndo(state);
  assert.deepEqual(state.present, [1, 2]);
  state = historyUndo(state);
  assert.deepEqual(state.present, [1]);
  state = historyRedo(state);
  assert.deepEqual(state.present, [1, 2]);
});

test('import rejects invalid payloads, HTML, extra option, overlong text', async (t) => {
  const { validateImport, whitelistText, whitelistStyle } = await loadDrawings(t);
  assert.equal(validateImport({ option: { series: [] } }).ok, false);
  assert.equal(validateImport({ schemaVersion: 1, drawings: [{ kind: 'text', text: '<script>' }] }).ok, false);
  assert.equal(whitelistText('a'.repeat(241)), null);
  assert.equal(whitelistText('<b>x</b>'), null);
  assert.ok(whitelistText('hello'));
  assert.equal(whitelistStyle({ color: 'red', width: 2, dash: 'solid' }), null);
  assert.ok(whitelistStyle({ color: '#2E46E0', width: 2, dash: 'dashed' }));
});

test('Shift constrain and Alt no-snap helpers', async (t) => {
  const { constrainByShift, applyAltNoSnap, snapPointer } = await loadDrawings(t);
  const snapped = constrainByShift({ x: 0, y: 0 }, { x: 10, y: 1 });
  assert.ok(Math.abs(snapped.y) < 1e-6);
  const diagonal = constrainByShift({ x: 0, y: 0 }, { x: 10, y: 9 });
  assert.ok(Math.abs(Math.abs(diagonal.x) - Math.abs(diagonal.y)) < 1e-6);
  const alt = applyAltNoSnap(true, { snapped: true, price: 10 });
  assert.equal(alt.snapped, false);
  const identityY = (price) => price;
  const withSnap = snapPointer({
    x: 2,
    y: 10.2,
    barCount: 5,
    pointerPrice: 10.2,
    candidates: [{ price: 10, kind: 'ohlc' }],
    priceToY: identityY,
    thresholdPx: 1,
    alt: false,
  });
  assert.equal(withSnap.snapped, true);
  const noSnap = snapPointer({
    x: 2,
    y: 10.2,
    barCount: 5,
    pointerPrice: 10.2,
    candidates: [{ price: 10, kind: 'ohlc' }],
    priceToY: identityY,
    thresholdPx: 1,
    alt: true,
  });
  assert.equal(noSnap.snapped, false);
  assert.equal(noSnap.price, 10.2);
});

test('blank future area is not mapped onto a historical bar', async (t) => {
  const { snapBarIndex } = await loadDrawings(t);
  assert.equal(snapBarIndex(9.4, 10), 9);
  assert.equal(snapBarIndex(9.6, 10), null);
  assert.equal(snapBarIndex(10.2, 10), null);
  assert.equal(snapBarIndex(-1, 10), null);
});

test('anonymous storage key matches the documented shape', async (t) => {
  const { anonymousStorageKey } = await loadDrawings(t);
  assert.equal(
    anonymousStorageKey('NVDA', '1d', 'raw'),
    'option-pro:chart-drawings:v1:anonymous:NVDA:1d:raw',
  );
});

function barsFor(n = 12) {
  return Array.from({ length: n }, (_, i) => {
    const day = String(6 + i).padStart(2, '0');
    return {
      t: `2026-07-${day}T13:30:00Z`,
      o: 10,
      h: 30,
      l: 5,
      c: 15,
    };
  });
}

function drawingOf(kind, anchors, extra = {}) {
  return {
    schemaVersion: 1,
    id: '11111111-1111-4111-8111-111111111111',
    ticker: 'NVDA',
    range: '1d',
    adjustment: 'raw',
    kind,
    anchors,
    style: { color: '#2E46E0', width: 2, dash: 'solid', fillOpacity: 0.2 },
    locked: false,
    hidden: false,
    zOrder: 1,
    revision: 1,
    createdAt: '',
    updatedAt: '',
    ...extra,
  };
}

function ctxFor(bars) {
  return { bars, range: '1d', xMin: 0, xMax: bars.length - 1, yMin: 5, yMax: 30 };
}

test('toProjectedDrawing hit-tests rectangle fill, channel parallel, fib levels, and clipped rays', async (t) => {
  const { toProjectedDrawing, hitTestProjected, drawingSegments, clipRayToRect } = await loadDrawings(t);
  const bars = barsFor(12);
  const ctx = ctxFor(bars);

  const rectangle = drawingOf('rectangle', [
    { time: bars[8].t, barKey: '2026-07-14', price: 25 },
    { time: bars[1].t, barKey: '2026-07-07', price: 8 },
  ]);
  const rectProj = toProjectedDrawing(rectangle, ctx);
  assert.ok(rectProj);
  assert.equal(rectProj.segments.length, 4);
  assert.equal(rectProj.fills.length, 1);
  const inside = hitTestProjected(rectProj, { x: 4, y: 16 }, 'mouse');
  assert.equal(inside?.kind, 'body');
  const onEdge = hitTestProjected(rectProj, { x: 4, y: 8 }, 'mouse');
  assert.ok(onEdge);

  const channel = drawingOf('channel', [
    { time: bars[0].t, barKey: '2026-07-06', price: 10 },
    { time: bars[10].t, barKey: '2026-07-16', price: 20 },
    { time: bars[2].t, barKey: '2026-07-08', price: 4 },
  ]);
  const chProj = toProjectedDrawing(channel, ctx);
  assert.equal(chProj.segments.length, 2);
  assert.equal(chProj.fills.length, 1);
  const geom = drawingSegments(channel, ctx);
  const parallel = geom.segments[1];
  const midParallel = {
    x: (parallel.a.x + parallel.b.x) / 2,
    y: (parallel.a.y + parallel.b.y) / 2,
  };
  const railHit = hitTestProjected(chProj, midParallel, 'mouse');
  assert.ok(railHit);
  const firstSeg = geom.segments[0];
  assert.equal(
    firstSeg.a.x === parallel.a.x && firstSeg.a.y === parallel.a.y && firstSeg.b.x === parallel.b.x && firstSeg.b.y === parallel.b.y,
    false,
    'parallel rail is a distinct second segment, not the main line',
  );

  const fib = drawingOf('fibonacci', [
    { time: bars[0].t, barKey: '2026-07-06', price: 10 },
    { time: bars[10].t, barKey: '2026-07-16', price: 20 },
  ]);
  const fibProj = toProjectedDrawing(fib, ctx);
  const half = hitTestProjected(fibProj, { x: 5, y: 15 }, 'mouse');
  assert.ok(half);
  const miss = hitTestProjected(fibProj, { x: 5, y: 40 }, 'mouse');
  assert.equal(miss, null);

  const ray = drawingOf('ray', [
    { time: bars[1].t, barKey: '2026-07-07', price: 10 },
    { time: bars[3].t, barKey: '2026-07-09', price: 14 },
  ]);
  const rayProj = toProjectedDrawing(ray, ctx);
  const clipped = clipRayToRect({ x: 1, y: 10 }, { x: 3, y: 14 }, {
    xMin: ctx.xMin, xMax: ctx.xMax, yMin: ctx.yMin, yMax: ctx.yMax,
  });
  assert.ok(clipped);
  const beyondSecond = {
    x: (3 + clipped.b.x) / 2,
    y: (14 + clipped.b.y) / 2,
  };
  assert.ok(beyondSecond.x > 3, 'sample sits past the second anchor');
  const rayHit = hitTestProjected(rayProj, beyondSecond, 'mouse');
  assert.ok(rayHit);
});

test('selected segment overlay emits two anchor points and a rubber-band draft', async (t) => {
  const { drawingsToMarks, graphicFromOverlay, draftOverlay } = await loadDrawings(t);
  const bars = barsFor(6);
  const ctx = ctxFor(bars);
  const segment = drawingOf('segment', [
    { time: bars[0].t, barKey: '2026-07-06', price: 10 },
    { time: bars[2].t, barKey: '2026-07-08', price: 18 },
  ]);
  const idle = drawingsToMarks([segment], ctx);
  assert.equal(idle.points.length, 0);
  const selected = drawingsToMarks([segment], ctx, { selectedId: segment.id });
  const coords = selected.points.map((point) => point.coord);
  assert.equal(coords.length, 2);
  assert.deepEqual(
    [...coords].sort((a, b) => a[0] - b[0] || a[1] - b[1]),
    [[0, 10], [2, 18]],
  );
  const graphic = graphicFromOverlay(
    { anchors: [{ x: 0, y: 10 }, { x: 2, y: 18 }], segments: [{ a: { x: 0, y: 10 }, b: { x: 2, y: 18 } }], fills: [] },
    (point) => point,
    '#2E46E0',
  );
  assert.equal(graphic.filter((el) => el.type === 'circle').length, 2);
  const draft = draftOverlay({ kind: 'segment', points: [{ barIndex: 0, price: 10 }, { barIndex: 3, price: 16 }] }, ctx);
  assert.equal(draft.segments.length, 1);
  assert.equal(draft.anchors.length, 2);
});

test('nudgeAnchors applies the shipped nudge helper to real bar-key anchors', async (t) => {
  const { nudgeAnchors, nudgePoint, resolveAnchor } = await loadDrawings(t);
  const bars = barsFor(12);
  const anchors = [
    { time: bars[2].t, barKey: '2026-07-08', price: 10 },
    { time: bars[4].t, barKey: '2026-07-10', price: 20 },
  ];
  const whole = nudgeAnchors(anchors, 'ArrowUp', false, bars, '1d', null);
  const expected0 = nudgePoint({ x: 2, y: 10 }, 'ArrowUp', false);
  const expected1 = nudgePoint({ x: 4, y: 20 }, 'ArrowUp', false);
  assert.equal(whole[0].price, expected0.y);
  assert.equal(whole[1].price, expected1.y);
  const one = nudgeAnchors(anchors, 'ArrowRight', true, bars, '1d', 0);
  const moved = nudgePoint({ x: 2, y: 10 }, 'ArrowRight', true);
  assert.equal(resolveAnchor(bars, one[0], '1d'), Math.round(moved.x));
  assert.equal(one[1].barKey, anchors[1].barKey);
  assert.equal(one[1].price, anchors[1].price);
});

test('tilted channel fill vertices stay parallel to the two rails, not a bounding box', async (t) => {
  const { drawingsToMarks, drawingSegments, vectorsParallel, fillIsAxisAligned } = await loadDrawings(t);
  const bars = barsFor(12);
  const ctx = ctxFor(bars);
  const channel = drawingOf('channel', [
    { time: bars[0].t, barKey: '2026-07-06', price: 10 },
    { time: bars[10].t, barKey: '2026-07-16', price: 22 },
    { time: bars[2].t, barKey: '2026-07-08', price: 4 },
  ]);
  const geom = drawingSegments(channel, ctx);
  assert.ok(geom.fill);
  assert.equal(fillIsAxisAligned(geom.fill), false);
  const marks = drawingsToMarks([channel], ctx);
  assert.equal(marks.areas.length, 0);
  assert.equal(marks.polygons.length, 1);
  const verts = marks.polygons[0].vertices;
  assert.deepEqual(verts, geom.fill);
  assert.equal(vectorsParallel(verts[0], verts[1], verts[3], verts[2]), true);
  assert.equal(vectorsParallel(geom.segments[0].a, geom.segments[0].b, verts[0], verts[1]), true);
  assert.equal(vectorsParallel(geom.segments[1].a, geom.segments[1].b, verts[3], verts[2]), true);
  const minX = Math.min(...verts.map((v) => v.x));
  const maxX = Math.max(...verts.map((v) => v.x));
  const minY = Math.min(...verts.map((v) => v.y));
  const maxY = Math.max(...verts.map((v) => v.y));
  const isBbox = verts.every((v) => (v.x === minX || v.x === maxX) && (v.y === minY || v.y === maxY))
    && new Set(verts.map((v) => v.x)).size === 2
    && new Set(verts.map((v) => v.y)).size === 2;
  assert.equal(isBbox, false);
});

test('autoPatternsToMarks maps a 4-anchor triangle to two rails and a box to a rectangle', async (t) => {
  const { autoPatternGeometry, autoPatternsToMarks, fillIsAxisAligned } = await loadDrawings(t);
  const bars = barsFor(12);
  const ctx = ctxFor(bars);
  const triangle = {
    id: 'tri',
    kind: 'triangle',
    subtype: 'symmetric',
    confidence: 82,
    status: 'forming',
    anchors: [
      { time: bars[1].t, barKey: '2026-07-07', price: 10 },
      { time: bars[9].t, barKey: '2026-07-15', price: 16 },
      { time: bars[1].t, barKey: '2026-07-07', price: 24 },
      { time: bars[9].t, barKey: '2026-07-15', price: 18 },
    ],
  };
  const triGeom = autoPatternGeometry(triangle, ctx);
  assert.ok(triGeom);
  assert.equal(triGeom.segments.length, 2);
  assert.equal(triGeom.fill.length, 4);
  const triMarks = autoPatternsToMarks([triangle], ctx, 70);
  assert.equal(triMarks.lines.length, 2);
  assert.equal(triMarks.polygons.length, 1);
  assert.equal(triMarks.polygons[0].vertices.length, 4);

  const box = {
    id: 'box',
    kind: 'box',
    subtype: 'horizontal',
    confidence: 88,
    status: 'forming',
    anchors: [
      { time: bars[2].t, barKey: '2026-07-08', price: 11 },
      { time: bars[8].t, barKey: '2026-07-14', price: 12 },
      { time: bars[2].t, barKey: '2026-07-08', price: 21 },
      { time: bars[8].t, barKey: '2026-07-14', price: 20 },
    ],
  };
  const boxGeom = autoPatternGeometry(box, ctx);
  assert.ok(boxGeom);
  assert.equal(boxGeom.segments.length, 4);
  assert.equal(fillIsAxisAligned(boxGeom.fill), true);
  const boxMarks = autoPatternsToMarks([box], ctx, 70);
  assert.equal(boxMarks.areas.length, 1);
  assert.equal(boxMarks.polygons.length, 0);
  assert.equal(boxMarks.lines.length, 4);
});

function validPattern(overrides = {}) {
  return {
    id: 'pat-1',
    algorithmVersion: 'optix-auto-patterns-v1',
    kind: 'support_trend',
    subtype: 'rising',
    direction: 'bullish',
    anchors: [
      { time: '2026-07-06T00:00:00+00:00', barKey: '2026-07-06', price: 10 },
      { time: '2026-07-16T00:00:00+00:00', barKey: '2026-07-16', price: 14 },
    ],
    confidence: 82,
    touches: 3,
    formationStart: '2026-07-06',
    formationEnd: '2026-07-16',
    dataThrough: '2026-07-16',
    status: 'forming',
    breakoutPrice: null,
    invalidationPrice: 9.2,
    measuredTarget: null,
    rationaleCodes: ['touches'],
    ...overrides,
  };
}

test('mapTechnicalStructure copies validated auto_patterns and drops invalid items', async (t) => {
  const { mapTechnicalAutoFields, mapAutoPatterns } = await loadDrawings(t);
  const body = {
    as_of: '2026-07-16T20:00:00+00:00',
    auto_patterns_version: 'optix-auto-patterns-v1',
    auto_patterns: [
      validPattern(),
      validPattern({ id: 'bad-kind', kind: 'pitchfork' }),
      validPattern({ id: 'bad-price', anchors: [{ time: 't', barKey: 'k', price: Number.NaN }, { time: 't2', barKey: 'k2', price: 4 }] }),
      'not-an-object',
    ],
  };
  const mapped = mapTechnicalAutoFields(body);
  assert.equal(mapped.auto_patterns_version, 'optix-auto-patterns-v1');
  assert.equal(mapped.auto_patterns.length, 1);
  assert.equal(mapped.auto_patterns[0].id, 'pat-1');
  assert.equal(mapped.auto_patterns[0].kind, 'support_trend');
  assert.equal(mapAutoPatterns(body.auto_patterns).length, 1);
  assert.notEqual(mapped.auto_patterns.length, 0);
});

test('named palette color down paints a non-brand hex', async (t) => {
  const { drawingsToMarks, resolvePaintColor } = await loadDrawings(t);
  assert.notEqual(resolvePaintColor('down'), '#2E46E0');
  const bars = barsFor(4);
  const drawing = drawingOf('segment', [
    { time: bars[0].t, barKey: '2026-07-06', price: 10 },
    { time: bars[2].t, barKey: '2026-07-08', price: 16 },
  ], { style: { color: 'down', width: 2, dash: 'solid' } });
  const marks = drawingsToMarks([drawing], ctxFor(bars));
  assert.equal(marks.lines.length, 1);
  assert.equal(marks.lines[0][0].lineStyle.color, resolvePaintColor('down'));
  assert.notEqual(marks.lines[0][0].lineStyle.color, '#2E46E0');
});

test('import rejects string booleans and reports illegal text / id conflict', async (t) => {
  const { validateImport, parseDrawingDetailed } = await loadDrawings(t);
  const base = drawingOf('horizontal', [{ time: '2026-07-06T13:30:00Z', barKey: '2026-07-06', price: 10 }]);
  const asFalse = validateImport({ schemaVersion: 1, drawings: [{ ...base, locked: 'false' }] });
  assert.equal(asFalse.ok, false);
  assert.equal(asFalse.error, 'invalid_boolean');
  const detailed = parseDrawingDetailed({ ...base, hidden: 'false' });
  assert.equal(detailed.ok, false);
  const illegal = validateImport({
    schemaVersion: 1,
    drawings: [{ ...base, kind: 'text', text: '<script>', anchors: [{ time: '2026-07-06T13:30:00Z', barKey: '2026-07-06', price: 10 }] }],
  });
  assert.equal(illegal.ok, false);
  assert.equal(illegal.error, 'illegal_text');
  const conflict = validateImport({ schemaVersion: 1, drawings: [base, { ...base }] });
  assert.equal(conflict.ok, false);
  assert.equal(conflict.error, 'id_conflict');
});

test('a horizontal emits one labeled markLine, not a segment plus a label', async (t) => {
  const { drawingsToMarks, drawingSegments } = await loadDrawings(t);
  const bars = barsFor(6);
  const ctx = ctxFor(bars);
  const drawing = drawingOf('horizontal', [{ time: bars[2].t, barKey: '2026-07-08', price: 18 }]);
  const geom = drawingSegments(drawing, ctx);
  assert.equal(geom.segments.length, 0);
  assert.equal(geom.horizontals.length, 1);
  const marks = drawingsToMarks([drawing], ctx);
  assert.equal(marks.lines.length, 1);
  assert.equal(marks.lines[0][0].label.show, true);
});

test('Shift constraint in pixel space rewrites time and barKey', async (t) => {
  const { applyPixelShiftConstraint, barKeyOf } = await loadDrawings(t);
  const bars = barsFor(12);
  const result = applyPixelShiftConstraint({
    originPx: { x: 20, y: 40 },
    pointerPx: { x: 80, y: 42 },
    fromPixel: (x, y) => ({ barIndex: x / 10, price: 100 - y }),
    bars,
    range: '1d',
  });
  assert.ok(result);
  assert.equal(result.time, bars[result.barIndex].t);
  assert.equal(result.barKey, barKeyOf(bars[result.barIndex], '1d'));
  assert.ok(Math.abs(result.barIndex - 8) <= 1);
});

test('drag preview leaves committed drawings unchanged', async (t) => {
  const { dragMove } = await loadDrawings(t);
  const bars = barsFor(12);
  const origin = drawingOf('segment', [
    { time: bars[1].t, barKey: '2026-07-07', price: 10 },
    { time: bars[4].t, barKey: '2026-07-10', price: 16 },
  ]);
  const drawings = [origin];
  const moved = dragMove({
    drawings,
    drag: {
      id: origin.id,
      mode: 'anchor',
      anchorIndex: 1,
      origin,
      startPixel: { x: 0, y: 0 },
      startData: { barIndex: 4, price: 16 },
    },
    pointer: { barIndex: 7, price: 22 },
    bars,
    range: '1d',
  });
  assert.equal(moved.drawings, drawings);
  assert.deepEqual(moved.drawings[0].anchors, origin.anchors);
  assert.equal(moved.preview.anchors[1].price, 22);
  assert.equal(moved.preview.anchors[1].barKey, '2026-07-13');
});

test('outbox retry replays pending ops and ignores stale list tokens', async (t) => {
  const {
    DrawingOutbox, applyPersistResponse, resolveListApply, resolveRetryAction, diffPersistOps,
  } = await loadDrawings(t);
  const scope = { identity: 'acct', ticker: 'NVDA', range: '1d', adjustment: 'raw' };
  const box = new DrawingOutbox();
  box.setScope(scope);
  const drawing = drawingOf('horizontal', [{ time: '2026-07-06T13:30:00Z', barKey: '2026-07-06', price: 10 }]);
  box.enqueue({ drawingId: drawing.id, type: 'update', drawing: { ...drawing, style: { ...drawing.style, color: '#0E9F6E' } } });
  const first = box.takeNext(drawing.id);
  box.enqueue({ drawingId: drawing.id, type: 'update', drawing: { ...drawing, style: { ...drawing.style, color: '#E5484D' }, locked: true, hidden: true, zOrder: 8, text: 'note' } });
  const latest = box.latestGeneration(drawing.id);
  const action = applyPersistResponse({
    job: first,
    currentScope: box.getScope(),
    currentScopeGeneration: box.getScopeGeneration(),
    latestGenerationForId: latest,
    responseDrawing: { ...drawing, revision: 2 },
  });
  assert.equal(action.action, 'revision');
  assert.equal(action.revision, 2);
  box.complete(drawing.id, first.generation);
  const second = box.takeNext(drawing.id);
  assert.equal(second.drawing.style.color, '#E5484D');
  assert.equal(second.drawing.locked, true);
  assert.equal(resolveRetryAction(box.isEmpty()), 'replay');
  box.failKeep(drawing.id);
  assert.equal(resolveListApply(box.isEmpty(), true), false);
  assert.equal(resolveRetryAction(false), 'replay');
  const other = { ...scope, ticker: 'AAPL' };
  assert.equal(resolveListApply(true, false), false);
  const stale = applyPersistResponse({
    job: { ...second, scope },
    currentScope: other,
    currentScopeGeneration: 99,
    latestGenerationForId: second.generation,
    responseDrawing: { ...drawing, revision: 9 },
  });
  assert.equal(stale.action, 'ignore');
  const ops = diffPersistOps(
    [drawing],
    [{ ...drawing, style: { ...drawing.style, color: '#E5484D' }, text: 'x', locked: true, hidden: true, zOrder: 4 }],
  );
  assert.equal(ops.length, 1);
  assert.equal(ops[0].type, 'update');
});

test('replace import policy helper treats empty and swapped sets as the new list', async (t) => {
  const { DrawingOutbox, SCOPE_JOB_ID } = await loadDrawings(t);
  const box = new DrawingOutbox();
  box.setScope({ identity: 'acct', ticker: 'NVDA', range: '1d', adjustment: 'raw' });
  const a = drawingOf('horizontal', [{ time: '2026-07-06T13:30:00Z', barKey: '2026-07-06', price: 10 }]);
  const b = drawingOf('segment', [
    { time: '2026-07-06T13:30:00Z', barKey: '2026-07-06', price: 10 },
    { time: '2026-07-07T13:30:00Z', barKey: '2026-07-07', price: 12 },
  ], { id: '22222222-2222-4222-8222-222222222222' });
  box.enqueue({ drawingId: SCOPE_JOB_ID, type: 'replace', drawings: [b] });
  const job = box.takeNext(SCOPE_JOB_ID);
  assert.equal(job.type, 'replace');
  assert.equal(job.drawings[0].id, b.id);
  assert.equal(job.drawings.some((item) => item.id === a.id), false);
  box.complete(SCOPE_JOB_ID, job.generation);
  box.enqueue({ drawingId: SCOPE_JOB_ID, type: 'replace', drawings: [] });
  const empty = box.takeNext(SCOPE_JOB_ID);
  assert.deepEqual(empty.drawings, []);
});
