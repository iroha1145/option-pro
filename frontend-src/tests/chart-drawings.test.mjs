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
  };
  await writeFile(
    entry,
    `
export {
  horizontalProjection, clipRayToRect, channelEdges, channelOffset,
  vectorsParallel, moveChannelWhole, moveChannelAnchor, normalizeRectangle,
  fibonacciPrices, constrainByShift, applyAltNoSnap, distancePointToSegment,
} from ${JSON.stringify(files.geometry)};
export {
  barKeyOf, resolveBarKey, resolveAnchor, snapBarIndex, drawingScopeKey, drawingsInScope,
} from ${JSON.stringify(files.projection)};
export {
  hitTestProjected, pickTopHit, hitTestDrawings, DESKTOP_LINE_TOLERANCE_PX, TOUCH_LINE_TOLERANCE_PX,
} from ${JSON.stringify(files.hitTest)};
export { snapPointer, nearestPrice } from ${JSON.stringify(files.snap)};
export { parseDrawing, validateImport, migrateStoredPayload, whitelistText, whitelistStyle, exportDrawings } from ${JSON.stringify(files.schema)};
export { createHistory, historyPush, historyUndo, historyRedo, historyReplace, canUndo, canRedo } from ${JSON.stringify(files.history)};
export { loadDrawings, saveDrawings, anonymousStorageKey, drawingsStorageKey } from ${JSON.stringify(files.storage)};
export { addDraftPoint, applyShiftToDraft, exclusiveTool, isTextInputTarget } from ${JSON.stringify(files.tools)};
export { drawingsToMarks, drawingSegments } from ${JSON.stringify(files.renderer)};
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
