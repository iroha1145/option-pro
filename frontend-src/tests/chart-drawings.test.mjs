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
    sync: path.join(drawingsDir, 'sync.ts'),
    drag: path.join(drawingsDir, 'drag.ts'),
    registry: path.join(drawingsDir, 'analysis/registry.ts'),
    settings: path.join(drawingsDir, 'analysis/settings.ts'),
    mapBundle: path.join(drawingsDir, 'analysis/mapBundle.ts'),
    overlaysToMarks: path.join(drawingsDir, 'analysis/overlaysToMarks.ts'),
    sha256: path.join(drawingsDir, 'analysis/sha256.ts'),
    drain: path.join(drawingsDir, 'drain.ts'),
    scopeLoad: path.join(drawingsDir, 'scopeLoad.ts'),
    merge: path.join(drawingsDir, 'merge.ts'),
    contract: path.join(drawingsDir, 'contract.ts'),
    zoom: path.join(drawingsDir, 'zoom.ts'),
    identityRetry: path.join(src, 'lib', 'identityRetry.ts'),
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
  hitTestProjected, pickTopHit, hitTestDrawings, isLockedDragBlocked, textLabelBox,
  DESKTOP_LINE_TOLERANCE_PX, TOUCH_LINE_TOLERANCE_PX, DESKTOP_ANCHOR_TOLERANCE_PX, TOUCH_ANCHOR_TOLERANCE_PX,
} from ${JSON.stringify(files.hitTest)};
export { snapPointer, nearestPrice, snapCandidatesFromOverlays } from ${JSON.stringify(files.snap)};
export {
  parseDrawing, parseDrawingDetailed, validateImport, migrateStoredPayload, collectStoredDrawings,
  whitelistText, whitelistStyle, exportDrawings, resolvePaintColor, PRICE_MAX, PRICE_MIN,
} from ${JSON.stringify(files.schema)};
export { createHistory, historyPush, historyUndo, historyRedo, historyReplace, canUndo, canRedo } from ${JSON.stringify(files.history)};
export {
  loadDrawings, saveDrawings, anonymousStorageKey, drawingsStorageKey, outboxStorageKey,
  quarantineDrawings, quarantineKey, drawingsFromCache,
} from ${JSON.stringify(files.storage)};
export {
  addDraftPoint, applyShiftToDraft, exclusiveTool, isTextInputTarget,
  pointerKindFromEvent, escapeHandledByOverlay,
} from ${JSON.stringify(files.tools)};
export {
  drawingsToMarks, drawingSegments, toProjectedDrawing, overlayMarks, selectionOverlay, projectToPixels,
  draftOverlay, graphicFromOverlay, autoPatternGeometry, autoPatternsToMarks, fillIsAxisAligned,
} from ${JSON.stringify(files.renderer)};
export {
  DrawingOutbox, diffPersistOps, mutableFieldsDiffer, applyPersistResponse,
  resolveListApply, resolveRetryAction, SCOPE_JOB_ID, keepLocalWithServerRevisions,
  regeneratePersistOps, resolveSyncFailure, applyKnownRevisions, latestKnownRevision, parsePersistJobs,
  jobIsCurrent, jobBelongsToScope, settleJob, releaseInflight, conflictSnapshotUsable, nextDrainRetryDelayMs,
} from ${JSON.stringify(files.sync)};
export { drainPersistJob } from ${JSON.stringify(files.drain)};
export {
  previewScopeLoad, completeScopeLoad, applyConflictDecision, layerInputEnabled,
} from ${JSON.stringify(files.scopeLoad)};
export { replayPendingOps, evaluateRemoteVsPending, quotaRollbackDrawings } from ${JSON.stringify(files.merge)};
export { parseList, parseSaved, parseMutation, DrawingContractError } from ${JSON.stringify(files.contract)};
export { insideZoom, zoomFromOption } from ${JSON.stringify(files.zoom)};
export { identityRetryDelayMs } from ${JSON.stringify(files.identityRetry)};
export {
  dragMove, previewDragAnchors, applyPixelShiftConstraint, clampDragPoint, dragExceedsThreshold,
  DRAG_THRESHOLD_MOUSE_PX, DRAG_THRESHOLD_TOUCH_PX,
} from ${JSON.stringify(files.drag)};
export { LAYERS, PRESETS, layersStorageKey, GROUPS } from ${JSON.stringify(files.registry)};
export {
  DEFAULT_LAYER_SETTINGS, settingsFromPreset, parseLayerSettings, loadLayerSettings,
  saveLayerSettings, toggleLayer, layerIdForOverlay,
} from ${JSON.stringify(files.settings)};
export {
  mapChartAnalysis, analysisMatchesChart, filterOverlays, filterPanes, labelBudget, barFingerprint,
  barFingerprintFromBars, closedBarsForFingerprint, canonicalBarPayload, sha256Hex,
} from ${JSON.stringify(files.mapBundle)};
export { overlaysToMarks, overlaysToSeries, analysisLayout, alignSeriesToBars, panesToOption } from ${JSON.stringify(files.overlaysToMarks)};
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
  // FIB_RATIOS 出厂就带 1.272 / 1.618 两条扩展位，只钉基础七条等于没盯住。
  for (const required of [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1, 1.272, 1.618]) {
    assert.ok(ratios.includes(required), `missing ratio ${required}`);
  }
  assert.deepEqual(JSON.parse(JSON.stringify(ratios)), [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1, 1.272, 1.618]);
  assert.equal(up.find((item) => item.ratio === 1.272).price, 100 + 100 * 1.272);
  assert.equal(down.find((item) => item.ratio === 1.618).price, 200 - 100 * 1.618);
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
});

test('tolerances are CSS px: the device-pixel offset the caller would pass still hits', async (t) => {
  const { hitTestProjected, toProjectedDrawing, projectToPixels } = await loadDrawings(t);
  // hitTestProjected 不吃 DPR：真正会缩放的是调用方的像素换算。这里就按 DPR=2
  // 的画布做一次换算，再像控制器那样把 offsetX/offsetY（CSS px）喂进来。
  const bars = barsFor(6);
  const ctx = ctxFor(bars);
  const drawing = drawingOf('segment', [
    { time: bars[0].t, barKey: '2026-07-06', price: 10 },
    { time: bars[4].t, barKey: '2026-07-10', price: 10 },
  ]);
  const dpr = 2;
  const toDevicePixel = (point) => ({ x: point.x * 40 * dpr, y: (30 - point.y) * 10 * dpr });
  const projected = projectToPixels(toProjectedDrawing(drawing, ctx), toDevicePixel);
  const onLine = { x: 2 * 40 * dpr, y: (30 - 10) * 10 * dpr };
  assert.equal(hitTestProjected(projected, onLine, 'mouse')?.kind, 'body');
  // 设备像素里偏 7px（= 3.5 CSS px）仍在 8 CSS px 容差内；偏 20 设备像素则不在。
  assert.ok(hitTestProjected(projected, { ...onLine, y: onLine.y + 7 }, 'mouse'));
  assert.equal(hitTestProjected(projected, { ...onLine, y: onLine.y + 20 }, 'mouse'), null);
  // 同一个偏移在触摸容差下要命中，证明容差确实随指针类型走。
  assert.ok(hitTestProjected(projected, { ...onLine, y: onLine.y + 20 }, 'touch'));
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

test('hidden drawings are not hittable; locked drawings select but never drag', async (t) => {
  const { hitTestProjected, isLockedDragBlocked, dragMove } = await loadDrawings(t);
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

  // 锁定的图形照样可以命中（要能选中改属性），但控制器必须在这一步掉头不建拖动。
  const locked = { ...hidden, id: 'l', hidden: false, locked: true };
  const hit = hitTestProjected(locked, { x: 9.5, y: 0 }, 'mouse');
  assert.equal(hit?.kind, 'body');
  assert.equal(isLockedDragBlocked({ locked: true }), true);
  assert.equal(isLockedDragBlocked({ locked: false }), false);

  // 真按控制器的顺序走一遍：命中 → 锁定就不建 drag → 没有 drag 就没有 preview。
  const bars = barsFor(12);
  const origin = drawingOf('segment', [
    { time: bars[1].t, barKey: '2026-07-07', price: 10 },
    { time: bars[4].t, barKey: '2026-07-10', price: 16 },
  ], { locked: true });
  const startDrag = () => (isLockedDragBlocked(origin) ? null : {
    id: origin.id,
    mode: 'anchor',
    anchorIndex: 1,
    origin,
    startPixel: { x: 0, y: 0 },
    startData: { barIndex: 4, price: 16 },
    moved: false,
  });
  assert.equal(startDrag(), null);
  const unlocked = { ...origin, locked: false };
  const drag = isLockedDragBlocked(unlocked) ? null : {
    id: unlocked.id,
    mode: 'anchor',
    anchorIndex: 1,
    origin: unlocked,
    startPixel: { x: 0, y: 0 },
    startData: { barIndex: 4, price: 16 },
    moved: false,
  };
  assert.ok(drag);
  const moved = dragMove({ drawings: [unlocked], drag, pointer: { barIndex: 7, price: 22 }, bars, range: '1d' });
  assert.equal(moved.preview.anchors[1].price, 22);
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
  assert.equal(resolveRetryAction('write_failed', box.isEmpty()), 'replay');
  box.failKeep(drawing.id);
  assert.equal(resolveListApply(box.isEmpty(), true), false);
  assert.equal(resolveRetryAction('write_failed', false), 'replay');
  assert.equal(resolveRetryAction('load_failed', true), 'reload');
  assert.equal(resolveRetryAction(null, true), 'idle');
  assert.equal(resolveRetryAction('write_failed', false, false), 'reload');
  assert.equal(resolveRetryAction('write_failed', false, true), 'replay');
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

test('failed inflight create is replayed before a later update', async (t) => {
  const { DrawingOutbox } = await loadDrawings(t);
  const box = new DrawingOutbox();
  box.setScope({ identity: 'acct', ticker: 'NVDA', range: '1d', adjustment: 'raw' });
  const created = drawingOf('horizontal', [{ time: '2026-07-06T13:30:00Z', barKey: '2026-07-06', price: 10 }]);
  const edited = { ...created, style: { ...created.style, color: '#E5484D' }, revision: 1 };
  box.enqueue({ drawingId: created.id, type: 'create', drawing: created });
  const inflight = box.takeNext(created.id);
  assert.equal(inflight.type, 'create');
  box.enqueue({ drawingId: created.id, type: 'update', drawing: edited });
  box.failKeep(created.id);
  const replay = box.snapshot();
  assert.deepEqual(replay.map((job) => job.type), ['create', 'update']);
  assert.equal(replay[0].drawing.id, created.id);
  assert.equal(replay[1].drawing.style.color, '#E5484D');
  const first = box.takeNext(created.id);
  assert.equal(first.type, 'create');
  box.complete(created.id, first.generation);
  const second = box.takeNext(created.id);
  assert.equal(second.type, 'update');
  assert.equal(second.drawing.style.color, '#E5484D');
});

test('keep-local conflict adopts the server revision and leaves local fields', async (t) => {
  const { DrawingOutbox, keepLocalWithServerRevisions } = await loadDrawings(t);
  const local = drawingOf('horizontal', [{ time: '2026-07-06T13:30:00Z', barKey: '2026-07-06', price: 10 }], {
    style: { color: '#E5484D', width: 3, dash: 'dashed' },
    revision: 2,
  });
  const server = drawingOf('horizontal', [{ time: '2026-07-06T13:30:00Z', barKey: '2026-07-06', price: 99 }], {
    style: { color: '#2E46E0', width: 1, dash: 'solid' },
    revision: 5,
  });
  const kept = keepLocalWithServerRevisions([local], [server]);
  assert.equal(kept[0].revision, 5);
  assert.equal(kept[0].style.color, '#E5484D');
  assert.equal(kept[0].style.width, 3);
  assert.equal(kept[0].anchors[0].price, 10);
  const box = new DrawingOutbox();
  box.setScope({ identity: 'acct', ticker: 'NVDA', range: '1d', adjustment: 'raw' });
  box.enqueue({ drawingId: local.id, type: 'update', drawing: local });
  const job = box.takeNext(local.id);
  box.failKeep(local.id);
  box.stampRevisions(kept);
  const replay = box.takeNext(local.id);
  assert.equal(replay.type, 'update');
  assert.equal(replay.drawing.revision, 5);
  assert.equal(replay.drawing.style.color, '#E5484D');
  assert.notEqual(job.drawing.revision, replay.drawing.revision);
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

function overlayOf(kind, extra = {}) {
  return {
    id: extra.id ?? kind,
    sourceId: extra.sourceId ?? 'auto_patterns',
    algorithmVersion: 'optix-auto-patterns-v2',
    group: extra.group ?? 'price',
    kind,
    geometry: extra.geometry ?? { anchors: [{ time: 't', barKey: '2026-07-06', price: 10 }, { time: 't2', barKey: '2026-07-16', price: 12 }] },
    status: extra.status ?? 'forming',
    direction: 'bullish',
    shapeQuality: extra.shapeQuality ?? 0.8,
    displayPriority: extra.displayPriority ?? 0.7,
    evidence: { shapeQuality: 0.8, volumeConfirmation: 0.5, trendAlignment: 0.5, recency: 0.5, consensus: 1 },
    formationStart: '2026-07-06',
    formationEnd: '2026-07-16',
    dataThrough: '2026-07-16',
    label: kind,
    detail: 'geometry quality is not a win rate',
  };
}

test('layer registry filters algorithm and pattern groups and presets', async (t) => {
  const {
    PRESETS, settingsFromPreset, toggleLayer, filterOverlays, filterPanes, labelBudget, layersStorageKey,
  } = await loadDrawings(t);
  const structure = settingsFromPreset('structure');
  assert.equal(structure.preset, 'structure');
  assert.ok(structure.enabled.includes('auto_patterns'));
  assert.equal(PRESETS.minimal.maxPatterns, 3);
  assert.equal(PRESETS.minimal.maxLabels, 6);
  const overlays = [
    overlayOf('support_trend', { id: 'a', displayPriority: 0.9 }),
    overlayOf('support_trend', { id: 'b', displayPriority: 0.8 }),
    overlayOf('support_trend', { id: 'c', displayPriority: 0.7 }),
    overlayOf('support_trend', { id: 'd', displayPriority: 0.6 }),
    overlayOf('swing', { id: 's', group: 'price', geometry: { type: 'point' } }),
  ];
  const minimal = settingsFromPreset('minimal');
  const kept = filterOverlays(overlays, minimal);
  assert.equal(kept.filter((row) => row.kind === 'support_trend').length, 3);
  assert.equal(kept.some((row) => row.id === 's'), false);
  const withSwings = filterOverlays(overlays, structure);
  assert.equal(withSwings.some((row) => row.id === 's'), true);
  const custom = toggleLayer(minimal, 'rsi');
  assert.equal(custom.preset, 'custom');
  const panes = filterPanes(
    [{ id: 'rsi', label: 'RSI', kind: 'rsi', values: { rsi: [1] }, dates: [] }, { id: 'macd', label: 'MACD', kind: 'macd', values: { macd: [1] }, dates: [] }],
    settingsFromPreset('momentum'),
  );
  assert.deepEqual(panes.map((pane) => pane.id).sort(), ['macd', 'rsi']);
  const labels = labelBudget(overlays, settingsFromPreset('minimal'));
  assert.ok(labels.length <= 6);
  assert.equal(layersStorageKey('anonymous').startsWith('option-pro:chart-layers:v1:'), true);
  assert.equal(layersStorageKey('anonymous').includes('chart-drawings'), false);
});

test('layer settings persist on a key separate from drawings', async (t) => {
  const { loadLayerSettings, saveLayerSettings, settingsFromPreset, layersStorageKey, anonymousStorageKey } = await loadDrawings(t);
  const mem = new Map();
  const storage = {
    getItem: (key) => mem.get(key) ?? null,
    setItem: (key, value) => { mem.set(key, value); },
    removeItem: (key) => { mem.delete(key); },
  };
  const next = settingsFromPreset('breakout');
  saveLayerSettings('anonymous', next, storage);
  const loaded = loadLayerSettings('anonymous', storage);
  assert.equal(loaded.preset, 'breakout');
  assert.ok(loaded.enabled.includes('breakouts'));
  assert.notEqual(layersStorageKey('anonymous'), anonymousStorageKey('AAPL', '1d', 'raw'));
});

test('fingerprint and dataThrough mismatch yields no auto marks', async (t) => {
  const { mapChartAnalysis, analysisMatchesChart, filterOverlays, settingsFromPreset } = await loadDrawings(t);
  const bundle = mapChartAnalysis({
    ticker: 'AAPL',
    range: '1d',
    adjustment: 'raw',
    dataThrough: '2026-07-16',
    barFingerprint: 'abc',
    barCount: 10,
    overlays: [overlayOf('support_trend')],
    indicatorPanes: [{ id: 'rsi', label: 'RSI', kind: 'rsi', values: { rsi: [50] }, dates: ['2026-07-16'] }],
    strengthContext: { finalScore: null, note: 'not a win probability' },
  });
  assert.ok(bundle);
  assert.equal(analysisMatchesChart(bundle, { range: '1d', adjustment: 'raw', dataThrough: '2026-07-16' }), false);
  assert.equal(analysisMatchesChart(bundle, { range: '1d', adjustment: 'raw', ticker: 'AAPL', dataThrough: '2026-07-16', fingerprint: 'abc' }), true);
  assert.equal(analysisMatchesChart(bundle, { range: '1d', adjustment: 'raw', ticker: 'MSFT', dataThrough: '2026-07-16', fingerprint: 'abc' }), false);
  assert.equal(analysisMatchesChart(bundle, { range: '1d', adjustment: 'raw', ticker: 'AAPL', dataThrough: '2026-07-15', fingerprint: 'abc' }), false);
  assert.equal(analysisMatchesChart(bundle, { range: '1w', adjustment: 'raw', ticker: 'AAPL', dataThrough: '2026-07-16', fingerprint: 'abc' }), false);
  assert.equal(analysisMatchesChart(bundle, { range: '1d', adjustment: 'raw', ticker: 'AAPL', dataThrough: '2026-07-16', fingerprint: 'other' }), false);
  const none = analysisMatchesChart(bundle, { range: '1d', adjustment: 'raw', ticker: 'AAPL', dataThrough: '2026-07-15', fingerprint: 'abc' })
    ? filterOverlays(bundle.overlays, settingsFromPreset('minimal'))
    : [];
  assert.equal(none.length, 0);
  assert.equal(mapChartAnalysis({ option: { series: [] }, dataThrough: 'x', barFingerprint: 'y' }), null);
});

test('layer toggles change real marks and MA series, not only filterOverlays', async (t) => {
  const {
    overlaysToMarks, overlaysToSeries, filterOverlays, settingsFromPreset, toggleLayer, analysisLayout,
    analysisMatchesChart, barFingerprint, barFingerprintFromBars, canonicalBarPayload, sha256Hex,
  } = await loadDrawings(t);
  const bars = [
    { t: '2026-07-06T13:30:00Z', o: 10, h: 12, l: 9, c: 11 },
    { t: '2026-07-07T13:30:00Z', o: 11, h: 13, l: 10, c: 12 },
    { t: '2026-07-08T13:30:00Z', o: 12, h: 14, l: 11, c: 13 },
  ];
  const ctx = { bars, range: '1d', xMin: 0, xMax: 2, yMin: 9, yMax: 14 };
  const overlays = [
    overlayOf('swing', {
      id: 's',
      group: 'price',
      geometry: { type: 'point', role: 'high', anchors: [{ time: '2026-07-07T00:00:00Z', barKey: '2026-07-07', price: 13 }] },
    }),
    overlayOf('level', { id: 'lv', geometry: { type: 'level', price: 12, role: 'resistance' } }),
    overlayOf('candle', {
      id: 'cd',
      group: 'event',
      geometry: { type: 'point', pattern: 'hammer', anchors: [{ time: '2026-07-08T00:00:00Z', barKey: '2026-07-08', price: 13 }] },
    }),
    overlayOf('trap', {
      id: 'tr',
      group: 'event',
      geometry: { type: 'point', pattern: 'spring', anchors: [{ time: '2026-07-06T00:00:00Z', barKey: '2026-07-06', price: 9 }] },
    }),
    overlayOf('breakout', { id: 'br', group: 'event', geometry: { type: 'levels', pivot: 13, invalidation: 10 } }),
    overlayOf('volume_setup', { id: 'vs', group: 'event', geometry: { type: 'summary', window: 10 } }),
    overlayOf('ma', { id: 'ma20', geometry: { type: 'series', window: 20, values: [null, 11, 12], dates: ['2026-07-06', '2026-07-07', '2026-07-08'] } }),
    overlayOf('support_trend', {
      id: 'st',
      geometry: {
        anchors: [
          { time: '2026-07-06T13:30:00Z', barKey: '2026-07-06', price: 9 },
          { time: '2026-07-08T13:30:00Z', barKey: '2026-07-08', price: 11 },
        ],
      },
    }),
  ];
  const all = settingsFromPreset('all');
  const visible = filterOverlays(overlays, all);
  const marks = overlaysToMarks(visible, ctx);
  assert.ok(marks.points.length >= 3, 'swing/candle/trap become markPoints');
  assert.ok(marks.lines.length >= 3, 'level/breakout/trend become markLines');
  assert.ok(marks.areas.length >= 1, 'volume_setup becomes a markArea');
  const series = overlaysToSeries(visible, bars, '1d');
  assert.equal(series.some((line) => line.id === 'ma20'), true);
  const withoutMa = filterOverlays(overlays, toggleLayer(all, 'ma20'));
  assert.equal(overlaysToSeries(withoutMa, bars, '1d').some((line) => line.id === 'ma20'), false);
  const structureMarks = overlaysToMarks(filterOverlays(overlays, settingsFromPreset('structure')), ctx);
  assert.ok(structureMarks.points.some((point) => Array.isArray(point.coord)));
  assert.equal(overlaysToSeries(filterOverlays(overlays, settingsFromPreset('structure')), bars, '1d').length, 0);

  const grids = analysisLayout(6);
  assert.equal(grids.length, 8);
  for (const grid of grids) {
    const top = Number.parseFloat(grid.top);
    const height = Number.parseFloat(grid.height);
    assert.ok(grid.top.endsWith('%'));
    assert.ok(top + height <= 100 + 1e-6, `overflow ${grid.top}+${grid.height}`);
  }

  const fp = barFingerprint(bars);
  const fromBars = barFingerprintFromBars(bars, '1d');
  assert.equal(fromBars, fp);
  const payload = canonicalBarPayload(bars);
  assert.equal(sha256Hex(payload), fp);
  assert.match(payload, /\|0\|0$/);
  const mutated = bars.map((bar, index) => (index === 0 ? { ...bar, h: bar.h + 4 } : bar));
  assert.notEqual(barFingerprint(mutated), fp);
  const bundle = {
    ticker: 'AAPL', range: '1d', adjustment: 'raw', dataThrough: '2026-07-08',
    barFingerprint: fp, overlays: [], indicatorPanes: [], strengthContext: null, barCount: 3, lastClose: 13,
  };
  assert.equal(analysisMatchesChart(bundle, { range: '1d', adjustment: 'raw', ticker: 'AAPL', dataThrough: '2026-07-08', fingerprint: fp }), true);
  assert.equal(analysisMatchesChart(bundle, { range: '1d', adjustment: 'raw', ticker: 'AAPL', dataThrough: '2026-07-08', fingerprint: 'nope' }), false);
  assert.equal(analysisMatchesChart(bundle, { range: '1d', adjustment: 'raw', ticker: 'MSFT', dataThrough: '2026-07-08', fingerprint: fp }), false);
  const vector = canonicalBarPayload([
    { t: 1700000000, o: 10, h: 11, l: 9, c: 10.5, v: 1000 },
    { t: 1700086400, o: 10.5, h: 12, l: 10, c: 11, v: 1100 },
  ]);
  assert.equal(
    vector,
    '1700000000|10.000000|11.000000|9.000000|10.500000|1000.000000|0|0\n1700086400|10.500000|12.000000|10.000000|11.000000|1100.000000|0|0',
  );
  assert.equal(sha256Hex(''), 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855');
  // 跨语言钉死：同一条向量在 tests/test_chart_analysis_layers.py 里 pin 同一个
  // 字面 digest。闸门是 fail-closed 的——两边漂开只会让分析图层整块静默消失，
  // 所以必须先挂测试，而不是先挂产品。
  assert.equal(
    sha256Hex(vector),
    '656393794b6b8d7ac710ac4f37f7a1b950ab6b673f5118d5b16416db114f6f39',
  );
});

function memStore() {
  const mem = new Map();
  return {
    getItem: (key) => mem.get(key) ?? null,
    setItem: (key, value) => { mem.set(key, value); },
    removeItem: (key) => { mem.delete(key); },
  };
}

test('outbox persists across setScope and restores before a later GET would apply', async (t) => {
  const { DrawingOutbox, outboxStorageKey, SCOPE_JOB_ID, applyPersistResponse, regeneratePersistOps } = await loadDrawings(t);
  const store = memStore();
  const nvda = { identity: 'acct', ticker: 'NVDA', range: '1d', adjustment: 'raw' };
  const aapl = { identity: 'acct', ticker: 'AAPL', range: '1d', adjustment: 'raw' };
  const drawing = drawingOf('horizontal', [{ time: '2026-07-06T13:30:00Z', barKey: '2026-07-06', price: 10 }]);
  const box = new DrawingOutbox(store);
  box.setScope(nvda);
  box.enqueue({ drawingId: drawing.id, type: 'update', drawing });
  assert.equal(box.isEmpty(), false);
  box.setScope(aapl);
  assert.equal(box.isEmpty(), true);
  box.setScope(nvda);
  assert.equal(box.isEmpty(), false);
  const restored = box.takeNext(drawing.id);
  assert.equal(restored.type, 'update');
  assert.equal(restored.drawing.id, drawing.id);
  assert.ok(store.getItem(outboxStorageKey('acct', 'NVDA', '1d', 'raw')));

  box.complete(drawing.id, restored.generation);
  box.enqueue({ drawingId: drawing.id, type: 'create', drawing });
  const inflight = box.takeNext(drawing.id);
  const genBeforeClear = inflight.scopeGeneration;
  box.enqueue({ drawingId: SCOPE_JOB_ID, type: 'clear' });
  assert.equal(box.takeNext(SCOPE_JOB_ID), null, 'barrier waits for inflight per-id');
  box.complete(drawing.id, inflight.generation);
  const ignored = applyPersistResponse({
    job: inflight,
    currentScope: box.getScope(),
    currentScopeGeneration: box.getScopeGeneration(),
    latestGenerationForId: box.latestGeneration(drawing.id),
    responseDrawing: { ...drawing, revision: 9 },
  });
  assert.equal(ignored.action, 'ignore');
  assert.notEqual(box.getScopeGeneration(), genBeforeClear);
  const barrier = box.takeNext(SCOPE_JOB_ID);
  assert.equal(barrier.type, 'clear');

  const local = [
    { ...drawing, revision: 2 },
    drawingOf('segment', [
      { time: '2026-07-06T13:30:00Z', barKey: '2026-07-06', price: 10 },
      { time: '2026-07-07T13:30:00Z', barKey: '2026-07-07', price: 12 },
    ], { id: '22222222-2222-4222-8222-222222222222' }),
  ];
  const server = [
    { ...drawing, revision: 5, style: { ...drawing.style, color: '#E5484D' } },
    drawingOf('horizontal', [{ time: '2026-07-08T13:30:00Z', barKey: '2026-07-08', price: 11 }], { id: '33333333-3333-4333-8333-333333333333' }),
  ];
  const ops = regeneratePersistOps(local, server);
  const byType = Object.fromEntries(ops.map((op) => [op.drawingId, op.type]));
  assert.equal(byType[drawing.id], 'update');
  assert.equal(ops.find((op) => op.drawingId === drawing.id).drawing.revision, 5);
  assert.equal(byType['22222222-2222-4222-8222-222222222222'], 'create');
  assert.equal(byType['33333333-3333-4333-8333-333333333333'], 'delete');
});

const ANCHOR_ONE = { time: '2026-07-06T13:30:00Z', barKey: '2026-07-06', price: 10 };
const SCOPE_NVDA = { identity: 'acct', ticker: 'NVDA', range: '1d', adjustment: 'raw' };

test('unsent work survives a scope switch: the queue comes back and blocks the stale server list', async (t) => {
  const { DrawingOutbox, resolveListApply, parsePersistJobs, outboxStorageKey } = await loadDrawings(t);
  const store = memStore();
  const edited = drawingOf('horizontal', [ANCHOR_ONE], {
    style: { color: '#E5484D', width: 3, dash: 'dashed' },
    revision: 4,
  });
  const box = new DrawingOutbox(store);
  box.setScope(SCOPE_NVDA);
  box.enqueue({ drawingId: edited.id, type: 'update', drawing: edited });
  const inflight = box.takeNext(edited.id);
  box.failKeep(edited.id); // 离线：任务留在队列里，状态是 unsynced
  assert.equal(inflight.type, 'update');

  // 换标的再换回来：队列必须还在，而且非空就不许用服务器列表覆盖本地。
  box.setScope({ ...SCOPE_NVDA, ticker: 'AAPL' });
  assert.equal(box.isEmpty(), true);
  box.setScope(SCOPE_NVDA);
  assert.equal(box.isEmpty(), false);
  assert.equal(resolveListApply(box.isEmpty(), true), false);
  const back = box.takeNext(edited.id);
  assert.equal(back.type, 'update');
  assert.equal(back.drawing.style.color, '#E5484D');
  assert.equal(back.drawing.style.width, 3);

  // 落盘的是不可信输入：逐条按 schema 过一遍，坏行丢掉。
  const raw = JSON.parse(store.getItem(outboxStorageKey('acct', 'NVDA', '1d', 'raw')));
  assert.ok(Array.isArray(raw.jobs));
  const dirty = {
    jobs: [
      { ...raw.jobs[0], drawing: { ...edited, anchors: [{ ...ANCHOR_ONE, price: -1 }] } },
      { drawingId: edited.id, generation: 9, type: 'nonsense' },
      { ...raw.jobs[0], generation: 7 },
    ],
  };
  const parsed = parsePersistJobs(dirty, SCOPE_NVDA, 3);
  assert.equal(parsed.length, 1);
  assert.equal(parsed[0].generation, 7);
  assert.equal(parsed[0].scopeGeneration, 3);
});

test('undo replays the latest server revision, not the one frozen in the history snapshot', async (t) => {
  const {
    createHistory, historyPush, historyUndo, applyKnownRevisions, latestKnownRevision,
  } = await loadDrawings(t);
  const base = drawingOf('horizontal', [ANCHOR_ONE], { revision: 1 });
  let state = createHistory([base]);
  const edited = { ...base, style: { ...base.style, color: '#E5484D' }, revision: 1 };
  state = historyPush(state, [edited]);
  // 服务器确认了这次编辑：revision 变成 5，只有 present 会被回声改写。
  const revisions = new Map([[base.id, 5]]);
  state = historyUndo(state);
  assert.equal(state.present[0].revision, 1, 'history snapshot still carries the stale revision');

  const restored = applyKnownRevisions(state.present, revisions);
  assert.equal(restored[0].revision, 5);
  assert.equal(restored[0].style.color, base.style.color, 'only the revision is rewritten');
  assert.equal(latestKnownRevision(revisions, base.id, state.present[0].revision), 5);
  assert.equal(latestKnownRevision(new Map(), base.id, 2), 2, 'no ack yet falls back to the local revision');
});

test('a server echo may not clobber edits still sitting in the debounce window', async (t) => {
  const { DrawingOutbox, applyPersistResponse } = await loadDrawings(t);
  const box = new DrawingOutbox(memStore());
  box.setScope(SCOPE_NVDA);
  const typed = drawingOf('text', [ANCHOR_ONE], { kind: 'text', text: 'note ab' });
  box.enqueue({ drawingId: typed.id, type: 'update', drawing: typed });
  const job = box.takeNext(typed.id);
  const server = { ...typed, text: 'note a', revision: 3 };
  const common = {
    job,
    currentScope: box.getScope(),
    currentScopeGeneration: box.getScopeGeneration(),
    latestGenerationForId: box.latestGeneration(typed.id),
    responseDrawing: server,
  };
  // 干净时回声可以整条替换。
  assert.equal(applyPersistResponse(common).action, 'replace');
  // 防抖窗口里还有没入队的键入：只允许吃 revision，文本必须留在本地。
  const dirty = applyPersistResponse({ ...common, localDirty: true });
  assert.equal(dirty.action, 'revision');
  assert.equal(dirty.revision, 3);
  assert.equal(dirty.id, typed.id);
});

test('one bad stored row drops only that row and never rewrites the scope empty', async (t) => {
  const { loadDrawings: load, collectStoredDrawings, saveDrawings, quarantineDrawings, quarantineKey } = await loadDrawings(t);
  const store = memStore();
  const good = drawingOf('horizontal', [ANCHOR_ONE]);
  const other = drawingOf('segment', [
    ANCHOR_ONE,
    { time: '2026-07-07T13:30:00Z', barKey: '2026-07-07', price: 12 },
  ], { id: '22222222-2222-4222-8222-222222222222' });
  const bad = { ...good, id: '33333333-3333-4333-8333-333333333333', anchors: [{ ...ANCHOR_ONE, price: 0 }] };
  const rawPayload = JSON.stringify({ schemaVersion: 1, drawings: [good, bad, other] });
  store.setItem('k', rawPayload);

  const collected = collectStoredDrawings(JSON.parse(rawPayload));
  assert.equal(collected.drawings.length, 2);
  assert.equal(collected.dropped, 1);
  assert.equal(collected.fatal, null);

  const loaded = load('k', store);
  assert.equal(loaded.ok, false, 'the drop is still reported to the UI');
  assert.equal(loaded.recoverable, true);
  assert.equal(loaded.drawings.length, 2);
  assert.deepEqual(
    JSON.parse(JSON.stringify(loaded.drawings.map((item) => item.id))),
    [good.id, other.id],
  );

  // 控制器的策略：先留底，再只写回能解析的行。
  assert.equal(quarantineDrawings('k', store), true);
  assert.equal(store.getItem(quarantineKey('k')), rawPayload);
  saveDrawings('k', loaded.drawings, store);
  assert.equal(load('k', store).drawings.length, 2);

  // 整份解析不出来时一行都不许写回去，原文必须留在原地。
  store.setItem('dead', '{"schemaVersion":2,"drawings":[]}');
  const dead = load('dead', store);
  assert.equal(dead.ok, false);
  assert.equal(dead.drawings.length, 0);
  assert.equal(dead.recoverable, false);
  const persist = dead.ok || dead.drawings.length ? 'now' : 'skip';
  assert.equal(persist, 'skip');
  assert.equal(quarantineDrawings('dead', store), true);
  assert.equal(store.getItem('dead'), '{"schemaVersion":2,"drawings":[]}');
});

test('sync failures branch on the body code: only revision_conflict is a conflict', async (t) => {
  const { resolveSyncFailure } = await loadDrawings(t);
  assert.equal(resolveSyncFailure('update', 'revision_conflict', 409), 'conflict');
  // 409 的其它三个码都不是冲突，弹重载对话框就等于让必败的任务无限重放。
  assert.equal(resolveSyncFailure('create', 'drawings_range_full', 409), 'quota');
  assert.equal(resolveSyncFailure('create', 'drawings_full', 409), 'quota');
  assert.equal(resolveSyncFailure('create', 'drawing_exists', 409), 'drop');
  // 后端已幂等：删掉早就没有的行算成功，任务直接丢掉而不是永远重试。
  assert.equal(resolveSyncFailure('delete', 'drawing_not_found', 404), 'drop');
  assert.equal(resolveSyncFailure('update', 'drawing_not_found', 404), 'conflict');
  assert.equal(resolveSyncFailure('update', 'scope_revision_conflict', 409), 'conflict');
  assert.equal(resolveSyncFailure('delete', null, 404), 'drop');
  // 400 是请求本身不合法，重放多少次都是同一个 400。
  assert.equal(resolveSyncFailure('update', 'invalid_price', 400), 'drop');
  // 网络/5xx 才该留在队列里等重试。
  assert.equal(resolveSyncFailure('update', null, 503), 'retry');
  assert.equal(resolveSyncFailure('create', null, null), 'retry');
  assert.equal(resolveSyncFailure('create', 'rate_limited', 429), 'retry');
  assert.equal(resolveSyncFailure('delete', null, 429), 'retry');
});

test('a selection click commits nothing: no history entry and no PUT without real movement', async (t) => {
  const {
    dragMove, dragExceedsThreshold, mutableFieldsDiffer, DRAG_THRESHOLD_MOUSE_PX, DRAG_THRESHOLD_TOUCH_PX,
  } = await loadDrawings(t);
  const bars = barsFor(12);
  const origin = drawingOf('segment', [
    { time: bars[1].t, barKey: '2026-07-07', price: 10 },
    { time: bars[4].t, barKey: '2026-07-10', price: 16 },
  ]);
  const start = { x: 100, y: 50 };
  assert.equal(dragExceedsThreshold(start, { x: 101, y: 51 }, 'mouse'), false);
  assert.equal(dragExceedsThreshold(start, { x: 100 + DRAG_THRESHOLD_MOUSE_PX + 1, y: 50 }, 'mouse'), true);
  // 手指抖得比鼠标厉害，阈值必须更宽。
  assert.ok(DRAG_THRESHOLD_TOUCH_PX > DRAG_THRESHOLD_MOUSE_PX);
  assert.equal(dragExceedsThreshold(start, { x: 100 + DRAG_THRESHOLD_MOUSE_PX + 1, y: 50 }, 'touch'), false);

  const drag = {
    id: origin.id,
    mode: 'anchor',
    anchorIndex: 1,
    origin,
    startPixel: start,
    startData: { barIndex: 4, price: 16 },
    moved: dragExceedsThreshold(start, { x: 101, y: 51 }, 'mouse'),
  };
  const still = dragMove({ drawings: [origin], drag, pointer: { barIndex: 4, price: 16 }, bars, range: '1d' });
  const commit = drag.moved && mutableFieldsDiffer(drag.origin, still.preview);
  assert.equal(commit, false, 'no movement, no commit');
  assert.equal(mutableFieldsDiffer(origin, still.preview), false, 'preview is field-identical to the origin');

  const dragged = { ...drag, moved: true };
  const moved = dragMove({ drawings: [origin], drag: dragged, pointer: { barIndex: 7, price: 22 }, bars, range: '1d' });
  assert.equal(dragged.moved && mutableFieldsDiffer(dragged.origin, moved.preview), true);
});

test('touch is detected from the zrender signal and really widens the tolerance', async (t) => {
  const { pointerKindFromEvent, hitTestProjected, TOUCH_LINE_TOLERANCE_PX, DESKTOP_LINE_TOLERANCE_PX } = await loadDrawings(t);
  // zrender 5.x 的包裹上没有 pointerType，只有 zrByTouch；原生 touchstart 同理。
  assert.equal(pointerKindFromEvent({ zrByTouch: true, event: { type: 'touchstart' } }), 'touch');
  assert.equal(pointerKindFromEvent({ event: { type: 'touchmove' } }), 'touch');
  assert.equal(pointerKindFromEvent({ event: { pointerType: 'touch', type: 'pointerdown' } }), 'touch');
  assert.equal(pointerKindFromEvent({ event: { pointerType: 'pen', type: 'pointerdown' } }), 'pen');
  assert.equal(pointerKindFromEvent({ event: { type: 'mousedown' } }), 'mouse');
  assert.equal(pointerKindFromEvent({}), 'mouse');

  const line = {
    id: 'a',
    zOrder: 1,
    locked: false,
    hidden: false,
    anchors: [{ x: 0, y: 0 }, { x: 200, y: 0 }],
    segments: [{ a: { x: 0, y: 0 }, b: { x: 200, y: 0 } }],
    fills: [],
  };
  const finger = { x: 100, y: (DESKTOP_LINE_TOLERANCE_PX + TOUCH_LINE_TOLERANCE_PX) / 2 };
  const touchEvent = { zrByTouch: true, event: { type: 'touchstart' } };
  const mouseEvent = { event: { type: 'mousedown' } };
  assert.ok(hitTestProjected(line, finger, pointerKindFromEvent(touchEvent)), 'finger hits');
  assert.equal(hitTestProjected(line, finger, pointerKindFromEvent(mouseEvent)), null, 'mouse would miss');
});

test('drag commits are clamped into the grid and re-validated before they reach the queue', async (t) => {
  const { clampDragPoint, dragMove, parseDrawing, PRICE_MAX, PRICE_MIN } = await loadDrawings(t);
  const bars = barsFor(12);
  const origin = drawingOf('segment', [
    { time: bars[1].t, barKey: '2026-07-07', price: 10 },
    { time: bars[4].t, barKey: '2026-07-10', price: 16 },
  ]);
  // 拖到图外：价格为负、索引越界，夹回来之后仍然是一条合法负载。
  const clamped = clampDragPoint({ barIndex: 99, price: -4 }, bars.length);
  assert.equal(clamped.barIndex, bars.length - 1);
  assert.ok(clamped.price >= PRICE_MIN);
  assert.equal(clampDragPoint({ barIndex: -7, price: PRICE_MAX * 10 }, bars.length).barIndex, 0);
  assert.equal(clampDragPoint({ barIndex: -7, price: PRICE_MAX * 10 }, bars.length).price, PRICE_MAX);

  const drag = {
    id: origin.id,
    mode: 'anchor',
    anchorIndex: 1,
    origin,
    startPixel: { x: 0, y: 0 },
    startData: { barIndex: 4, price: 16 },
    moved: true,
  };
  const good = dragMove({ drawings: [origin], drag, pointer: clamped, bars, range: '1d' });
  assert.ok(parseDrawing({ ...good.preview, updatedAt: '2026-07-16T00:00:00Z' }), 'clamped commit parses');

  // 不夹的话就是 schema 直接拒收的负载（后端同样按 invalid_price 回 400）。
  const bad = dragMove({ drawings: [origin], drag, pointer: { barIndex: 5, price: -4 }, bars, range: '1d' });
  assert.equal(parseDrawing({ ...bad.preview, updatedAt: '2026-07-16T00:00:00Z' }), null);
});

test('clicking rendered text selects it instead of falling through to deselect', async (t) => {
  const { toProjectedDrawing, projectToPixels, hitTestProjected, textLabelBox } = await loadDrawings(t);
  const bars = barsFor(6);
  const ctx = ctxFor(bars);
  const note = drawingOf('text', [{ time: bars[2].t, barKey: '2026-07-08', price: 18 }], {
    kind: 'text',
    text: 'earnings gap fills here',
    style: { color: '#3D4A68', width: 1, dash: 'solid' },
  });
  const projected = toProjectedDrawing(note, ctx);
  assert.ok(projected);
  assert.equal(projected.segments.length, 0, 'text has no segments to hit');
  assert.equal(projected.fills.length, 0);
  assert.equal(projected.label.text, note.text);

  const toPixel = (point) => ({ x: 60 + point.x * 40, y: 400 - point.y * 10 });
  const pixels = projectToPixels(projected, toPixel);
  const anchor = pixels.anchors[0];
  const box = textLabelBox(anchor, note.text);
  assert.ok(box.x1 - box.x0 > 40, 'the label box spans the rendered string');
  const onLabel = { x: (box.x0 + box.x1) / 2, y: (box.y0 + box.y1) / 2 };
  assert.ok(Math.hypot(onLabel.x - anchor.x, onLabel.y - anchor.y) > 20, 'sample sits well past the 6px dot');
  assert.equal(hitTestProjected(pixels, onLabel, 'mouse')?.kind, 'body');
  // 标签右边的空白仍然是取消选中。
  assert.equal(hitTestProjected(pixels, { x: box.x1 + 40, y: onLabel.y }, 'mouse'), null);
  // 没有文字的图形不会凭空长出命中区。
  const line = drawingOf('segment', [
    { time: bars[0].t, barKey: '2026-07-06', price: 10 },
    { time: bars[2].t, barKey: '2026-07-08', price: 18 },
  ]);
  assert.equal(toProjectedDrawing(line, ctx).label, null);
});

test('Escape defers to any overlay above the workspace but still collapses the workspace', async (t) => {
  const { escapeHandledByOverlay } = await loadDrawings(t);
  // 已经被别人处理过（preventDefault）就让路。
  assert.equal(escapeHandledByOverlay({ defaultPrevented: true, openModals: 0, workspaceExpanded: false }), true);
  // 工作区自己那一层不算覆盖层，否则全屏永远收不起来。
  assert.equal(escapeHandledByOverlay({ defaultPrevented: false, openModals: 1, workspaceExpanded: true }), false);
  // 工作区之上还开着确认框：这次 Escape 归确认框。
  assert.equal(escapeHandledByOverlay({ defaultPrevented: false, openModals: 2, workspaceExpanded: true }), true);
  // 非全屏时任何对话框都优先。
  assert.equal(escapeHandledByOverlay({ defaultPrevented: false, openModals: 1, workspaceExpanded: false }), true);
  assert.equal(escapeHandledByOverlay({ defaultPrevented: false, openModals: 0, workspaceExpanded: false }), false);
});

test('scope-level ops and per-id ops drain in the order they were queued', async (t) => {
  const { DrawingOutbox, SCOPE_JOB_ID, resolveListApply } = await loadDrawings(t);
  const drawn = drawingOf('horizontal', [ANCHOR_ONE]);
  const box = new DrawingOutbox(memStore());
  box.setScope(SCOPE_NVDA);

  // 先清空再画：新画的那条绝不能抢在 DELETE 前面到达服务器。
  box.enqueue({ drawingId: SCOPE_JOB_ID, type: 'clear' });
  box.enqueue({ drawingId: drawn.id, type: 'create', drawing: drawn });
  assert.equal(box.takeNext(drawn.id), null, 'create waits for the clear');
  assert.deepEqual(JSON.parse(JSON.stringify(box.readyIds())), [SCOPE_JOB_ID]);
  const clear = box.takeNext(SCOPE_JOB_ID);
  assert.equal(clear.type, 'clear');
  assert.equal(box.takeNext(drawn.id), null, 'still waiting while the clear is in flight');
  box.complete(SCOPE_JOB_ID, clear.generation);
  const create = box.takeNext(drawn.id);
  assert.equal(create.type, 'create');

  // 反向也一样：单条在飞时范围级任务必须等。
  const later = new DrawingOutbox(memStore());
  later.setScope(SCOPE_NVDA);
  later.enqueue({ drawingId: drawn.id, type: 'create', drawing: drawn });
  const flying = later.takeNext(drawn.id);
  later.enqueue({ drawingId: SCOPE_JOB_ID, type: 'clear' });
  assert.equal(later.takeNext(SCOPE_JOB_ID), null, 'clear waits for the in-flight create');
  assert.deepEqual(JSON.parse(JSON.stringify(later.readyIds())), []);
  later.complete(drawn.id, flying.generation);
  assert.equal(later.takeNext(SCOPE_JOB_ID).type, 'clear');

  // replace 回包只在「除了自己以外队列是空的」时才敢覆盖本地。
  const box3 = new DrawingOutbox(memStore());
  box3.setScope(SCOPE_NVDA);
  box3.enqueue({ drawingId: SCOPE_JOB_ID, type: 'replace', drawings: [drawn] });
  const replace = box3.takeNext(SCOPE_JOB_ID);
  assert.equal(box3.isEmptyExcept(replace.drawingId, replace.generation), true);
  assert.equal(resolveListApply(box3.isEmptyExcept(replace.drawingId, replace.generation), true), true);
  box3.enqueue({ drawingId: drawn.id, type: 'update', drawing: { ...drawn, hidden: true } });
  assert.equal(box3.isEmptyExcept(replace.drawingId, replace.generation), false);
  assert.equal(resolveListApply(box3.isEmptyExcept(replace.drawingId, replace.generation), true), false);
});

test('outbox delete path, update coalescing, stale-update drop and retry dedupe', async (t) => {
  const { DrawingOutbox } = await loadDrawings(t);
  const drawing = drawingOf('horizontal', [ANCHOR_ONE]);
  const box = new DrawingOutbox(memStore());
  box.setScope(SCOPE_NVDA);

  // 画完立刻删：创建还没发出去就被折叠掉，只剩一条 DELETE（服务器从没见过这个编号）。
  box.enqueue({ drawingId: drawing.id, type: 'create', drawing });
  box.enqueue({ drawingId: drawing.id, type: 'delete' });
  const queued = box.snapshot();
  assert.deepEqual(JSON.parse(JSON.stringify(queued.map((job) => job.type))), ['delete']);
  const del = box.takeNext(drawing.id);
  assert.equal(del.type, 'delete');
  box.complete(drawing.id, del.generation);
  assert.equal(box.isEmpty(), true);

  // 连续两次样式改动合并成一条 pending update，不是排两条。
  box.enqueue({ drawingId: drawing.id, type: 'update', drawing: { ...drawing, style: { ...drawing.style, color: '#0E9F6E' } } });
  box.enqueue({ drawingId: drawing.id, type: 'update', drawing: { ...drawing, style: { ...drawing.style, color: '#E8930C' } } });
  const coalesced = box.snapshot();
  assert.equal(coalesced.length, 1);
  assert.equal(coalesced[0].drawing.style.color, '#E8930C');

  // 失败的旧 update 后面已经排了更新的一条：旧的直接丢，不要发两遍。
  const flying = box.takeNext(drawing.id);
  box.enqueue({ drawingId: drawing.id, type: 'update', drawing: { ...drawing, style: { ...drawing.style, color: '#0B7285' } } });
  box.failKeep(drawing.id);
  const afterFail = box.snapshot();
  assert.deepEqual(JSON.parse(JSON.stringify(afterFail.map((job) => job.type))), ['update']);
  assert.equal(afterFail[0].drawing.style.color, '#0B7285');
  assert.ok(afterFail[0].generation > flying.generation);

  // restoreForRetry 不许把已经在队列里的任务再排一遍，且按 generation 排好序。
  const snapshot = box.snapshot();
  box.restoreForRetry(snapshot);
  assert.equal(box.snapshot().length, snapshot.length, 'replaying the same snapshot is a no-op');
  const older = { ...snapshot[0], generation: snapshot[0].generation - 1, seq: (snapshot[0].seq ?? 1) - 1 };
  box.restoreForRetry([older]);
  const sorted = box.snapshot().map((job) => job.generation);
  assert.deepEqual(
    JSON.parse(JSON.stringify(sorted)),
    JSON.parse(JSON.stringify([...sorted].sort((a, b) => a - b))),
  );
  assert.equal(sorted.length, snapshot.length + 1);
});

test('every drawing kind projects something hittable', async (t) => {
  const { toProjectedDrawing } = await loadDrawings(t);
  const bars = barsFor(12);
  const ctx = ctxFor(bars);
  const anchorAt = (index, price) => ({ time: bars[index].t, barKey: `2026-07-${String(6 + index).padStart(2, '0')}`, price });
  const samples = {
    horizontal: drawingOf('horizontal', [anchorAt(2, 18)]),
    segment: drawingOf('segment', [anchorAt(1, 10), anchorAt(5, 20)]),
    ray: drawingOf('ray', [anchorAt(1, 10), anchorAt(3, 14)]),
    channel: drawingOf('channel', [anchorAt(0, 10), anchorAt(8, 20), anchorAt(2, 6)]),
    rectangle: drawingOf('rectangle', [anchorAt(1, 8), anchorAt(6, 24)]),
    fibonacci: drawingOf('fibonacci', [anchorAt(0, 10), anchorAt(9, 20)]),
    text: drawingOf('text', [anchorAt(3, 15)], { kind: 'text', text: 'note' }),
  };
  for (const [kind, drawing] of Object.entries(samples)) {
    const projected = toProjectedDrawing(drawing, ctx);
    assert.ok(projected, `${kind} projects`);
    const hittable = projected.segments.length > 0 || projected.fills.length > 0 || Boolean(projected.label);
    assert.equal(hittable, true, `${kind} has a hit region`);
  }
});

test('empty text drawings parse and naive times are rejected', async (t) => {
  const { parseDrawingDetailed, whitelistText } = await loadDrawings(t);
  const empty = parseDrawingDetailed({
    schemaVersion: 1,
    id: '11111111-1111-4111-8111-111111111111',
    ticker: 'NVDA',
    range: '1d',
    adjustment: 'raw',
    kind: 'text',
    anchors: [{ time: '2026-07-06T13:30:00Z', barKey: '2026-07-06', price: 10 }],
    style: { color: '#2E46E0', width: 2, dash: 'solid' },
    text: '',
    locked: false,
    hidden: false,
    zOrder: 0,
  });
  assert.equal(empty.ok, true);
  assert.equal(empty.value.text, '');
  assert.equal(whitelistText(''), '');
  const naive = parseDrawingDetailed({
    schemaVersion: 1,
    id: '11111111-1111-4111-8111-111111111111',
    ticker: 'NVDA',
    range: '1d',
    kind: 'horizontal',
    anchors: [{ time: '2026-07-06T13:30:00', barKey: '2026-07-06', price: 10 }],
    style: { color: '#2E46E0', width: 2, dash: 'solid' },
    locked: false,
    hidden: false,
    zOrder: 0,
  });
  assert.equal(naive.ok, false);
  const tokyo = parseDrawingDetailed({
    schemaVersion: 1,
    id: '11111111-1111-4111-8111-111111111111',
    ticker: 'NVDA',
    range: '1d',
    kind: 'horizontal',
    anchors: [{ time: '2026-07-06T22:30:00+09:00', barKey: '2026-07-06', price: 10 }],
    style: { color: '#2E46E0', width: 2, dash: 'solid' },
    locked: false,
    hidden: false,
    zOrder: 0,
  });
  const ny = parseDrawingDetailed({
    schemaVersion: 1,
    id: '11111111-1111-4111-8111-111111111111',
    ticker: 'NVDA',
    range: '1d',
    kind: 'horizontal',
    anchors: [{ time: '2026-07-06T09:30:00-04:00', barKey: '2026-07-06', price: 10 }],
    style: { color: '#2E46E0', width: 2, dash: 'solid' },
    locked: false,
    hidden: false,
    zOrder: 0,
  });
  const utc = parseDrawingDetailed({
    schemaVersion: 1,
    id: '11111111-1111-4111-8111-111111111111',
    ticker: 'NVDA',
    range: '1d',
    kind: 'horizontal',
    anchors: [{ time: '2026-07-06T13:30:00Z', barKey: '2026-07-06', price: 10 }],
    style: { color: '#2E46E0', width: 2, dash: 'solid' },
    locked: false,
    hidden: false,
    zOrder: 0,
  });
  assert.equal(tokyo.ok && ny.ok && utc.ok, true);
  assert.equal(tokyo.value.anchors[0].time, ny.value.anchors[0].time);
  assert.equal(ny.value.anchors[0].time, utc.value.anchors[0].time);
});

test('cache missing empty and corrupt are distinct; empty cache is authoritative', async (t) => {
  const { loadDrawings: readDrawings, saveDrawings, drawingsFromCache } = await loadDrawings(t);
  const mem = new Map();
  const storage = {
    getItem: (key) => (mem.has(key) ? mem.get(key) : null),
    setItem: (key, value) => { mem.set(key, value); },
    removeItem: (key) => { mem.delete(key); },
  };
  const missing = readDrawings('k', storage);
  assert.equal(missing.ok, false);
  assert.equal(missing.missing, true);
  assert.deepEqual(drawingsFromCache(missing), []);
  saveDrawings('k', [], storage);
  const empty = readDrawings('k', storage);
  assert.equal(empty.ok, true);
  assert.equal(empty.state, 'empty');
  assert.deepEqual(drawingsFromCache(empty), []);
  storage.setItem('k', '{not json');
  const corrupt = readDrawings('k', storage);
  assert.equal(corrupt.ok, false);
  assert.equal(corrupt.error, 'corrupt');
});

test('delayed AAPL job after MSFT setScope is ignored', async (t) => {
  const { DrawingOutbox, applyPersistResponse, jobIsCurrent } = await loadDrawings(t);
  const box = new DrawingOutbox(null);
  const aapl = { identity: 'acct', ticker: 'AAPL', range: '1d', adjustment: 'raw' };
  const msft = { identity: 'acct', ticker: 'MSFT', range: '1d', adjustment: 'raw' };
  box.setScope(aapl);
  const drawing = drawingOf('horizontal', [{ time: '2026-07-06T13:30:00Z', barKey: '2026-07-06', price: 10 }]);
  box.enqueue({ drawingId: drawing.id, type: 'create', drawing });
  const job = box.takeNext(drawing.id);
  const aaplGen = job.scopeGeneration;
  box.setScope(msft);
  assert.equal(jobIsCurrent(job, box.getScope(), box.getScopeGeneration()), false);
  const action = applyPersistResponse({
    job,
    currentScope: box.getScope(),
    currentScopeGeneration: box.getScopeGeneration(),
    latestGenerationForId: box.latestGeneration(drawing.id),
    responseDrawing: { ...drawing, revision: 1 },
  });
  assert.equal(action.action, 'ignore');
  assert.equal(box.getScope().ticker, 'MSFT');
  assert.notEqual(box.getScopeGeneration(), aaplGen);
});

test('outbox hydrate drops cross-scope jobs', async (t) => {
  const { parsePersistJobs } = await loadDrawings(t);
  const scope = { identity: 'acct', ticker: 'MSFT', range: '1d', adjustment: 'raw' };
  const drawing = drawingOf('horizontal', [{ time: '2026-07-06T13:30:00Z', barKey: '2026-07-06', price: 10 }]);
  drawing.ticker = 'AAPL';
  const jobs = parsePersistJobs({
    jobs: [{
      drawingId: drawing.id,
      generation: 1,
      type: 'create',
      drawing,
      scope: { identity: 'acct', ticker: 'AAPL', range: '1d', adjustment: 'raw' },
    }],
  }, scope, 2);
  assert.equal(jobs.length, 0);
});

test('saveLayerSettings swallows quota errors', async (t) => {
  const { saveLayerSettings, settingsFromPreset } = await loadDrawings(t);
  const storage = {
    getItem: () => null,
    setItem: () => { const err = new Error('full'); err.name = 'QuotaExceededError'; throw err; },
    removeItem: () => {},
  };
  saveLayerSettings('acct', settingsFromPreset('minimal'), storage);
});

test('kind-based panes expose MACD three series RSI rails and CLV axis', async (t) => {
  const { panesToOption } = await loadDrawings(t);
  const bars = barsFor(8);
  const panes = panesToOption([
    { id: 'rsi', label: 'RSI', kind: 'rsi', values: { rsi: [30, 50, 70, 40, 55, 60, 45, 50] }, dates: bars.map((bar) => bar.t.slice(0, 10)) },
    { id: 'macd', label: 'MACD', kind: 'macd', values: { macd: [1, 2, 1, 0, -1, 0, 1, 2], signal: [0.5, 1, 1, 0.5, 0, 0, 0.5, 1], histogram: [0.5, 1, 0, -0.5, -1, 0, 0.5, 1] }, dates: bars.map((bar) => bar.t.slice(0, 10)) },
    { id: 'clv', label: 'CLV', kind: 'clv', values: { clv: [0, 0.2, -0.1, 0.4, 0.1, 0, -0.2, 0.3] }, dates: bars.map((bar) => bar.t.slice(0, 10)) },
    { id: 'obv', label: 'OBV', kind: 'obv', values: { obv: [1, 2, 3, 4, 5, 6, 7, 8] }, dates: bars.map((bar) => bar.t.slice(0, 10)) },
  ], bars, '1d');
  const rsi = panes.find((pane) => pane.kind === 'rsi');
  const macd = panes.find((pane) => pane.kind === 'macd');
  const clv = panes.find((pane) => pane.kind === 'clv');
  assert.deepEqual(rsi.markLines, [30, 70]);
  assert.equal(rsi.yMin, 0);
  assert.equal(rsi.yMax, 100);
  assert.equal(macd.series.length, 3);
  assert.deepEqual(macd.series.map((row) => row.key).sort(), ['histogram', 'macd', 'signal']);
  assert.equal(clv.yMin, -1);
  assert.equal(clv.yMax, 1);
  assert.deepEqual(clv.markLines, [0]);
});

test('breakout preset can show invalidated overlays', async (t) => {
  const { filterOverlays, settingsFromPreset } = await loadDrawings(t);
  const overlays = [
    overlayOf('support_trend', { id: 'live', status: 'confirmed', displayPriority: 0.9 }),
    overlayOf('support_trend', { id: 'dead', status: 'invalidated', displayPriority: 0.8 }),
  ];
  const breakout = settingsFromPreset('breakout');
  const kept = filterOverlays(overlays, breakout);
  assert.equal(kept.some((row) => row.id === 'dead'), true);
  const minimal = settingsFromPreset('minimal');
  const hidden = filterOverlays(overlays, minimal);
  assert.equal(hidden.some((row) => row.id === 'dead'), false);
});

test('labelBudget never exceeds maxLabels', async (t) => {
  const { labelBudget, settingsFromPreset } = await loadDrawings(t);
  const overlays = Array.from({ length: 20 }, (_, index) => overlayOf('support_trend', {
    id: `p${index}`,
    displayPriority: 1 - index / 40,
  }));
  const settings = { ...settingsFromPreset('all'), maxLabels: 4, labelDensity: 1 };
  const labels = labelBudget(overlays, settings);
  assert.ok(labels.length <= settings.maxLabels);
});

function persistApi(overrides = {}) {
  const boom = (code, status) => {
    const err = new Error(code);
    err.code = code;
    err.status = status;
    throw err;
  };
  return {
    create: async (drawing, expected) => ({ drawing: { ...drawing, revision: 1 }, scopeRevision: expected + 1 }),
    update: async (drawing, expected) => ({ drawing: { ...drawing, revision: drawing.revision + 1 }, scopeRevision: expected + 1 }),
    remove: async (_id, expected) => ({ scopeRevision: expected + 1 }),
    clearScope: async (_t, _r, expected) => ({ scopeRevision: expected + 1 }),
    replaceScope: async (_t, _r, drawings, expected) => ({ drawings, scopeRevision: expected + 1 }),
    list: async () => ({ drawings: [], scopeRevision: 0 }),
    ...overrides,
    boom,
  };
}

test('confirmed mutation advances the scope base for the next queued job', async (t) => {
  const { DrawingOutbox, drainPersistJob } = await loadDrawings(t);
  const firstDrawing = drawingOf('horizontal', [ANCHOR_ONE]);
  const secondDrawing = drawingOf('horizontal', [ANCHOR_ONE], {
    id: '22222222-2222-4222-8222-222222222222',
  });
  const box = new DrawingOutbox(memStore());
  box.setScope(SCOPE_NVDA);
  box.rebaseBase(5);
  box.enqueue({ drawingId: firstDrawing.id, type: 'create', drawing: firstDrawing });
  box.enqueue({ drawingId: secondDrawing.id, type: 'create', drawing: secondDrawing });

  const firstJob = box.takeNext(firstDrawing.id);
  assert.equal(firstJob.expectedScopeRevision, 5);
  const first = await drainPersistJob({
    outbox: box,
    job: firstJob,
    api: persistApi(),
    drawings: [firstDrawing, secondDrawing],
    lastServer: [],
    revisions: new Map(),
  });
  assert.equal(first.kind, 'success');
  assert.deepEqual(first.lastServer.map((item) => item.id), [firstDrawing.id]);
  assert.equal(box.getBaseScopeRevision(), 6);

  const secondJob = box.takeNext(secondDrawing.id);
  assert.equal(secondJob.expectedScopeRevision, 6);
});

test('successful superseded update advances a queued clear and its rollback snapshot', async (t) => {
  const { DrawingOutbox, SCOPE_JOB_ID, drainPersistJob } = await loadDrawings(t);
  const original = drawingOf('horizontal', [ANCHOR_ONE]);
  const edited = { ...original, locked: true };
  const box = new DrawingOutbox(memStore());
  box.setScope(SCOPE_NVDA);
  box.rebaseBase(5);
  box.enqueue({ drawingId: edited.id, type: 'update', drawing: edited });
  const updateJob = box.takeNext(edited.id);
  box.enqueue({ drawingId: SCOPE_JOB_ID, type: 'clear' });

  const outcome = await drainPersistJob({
    outbox: box,
    job: updateJob,
    api: persistApi(),
    drawings: [edited],
    lastServer: [original],
    revisions: new Map([[original.id, original.revision]]),
  });
  assert.equal(outcome.kind, 'superseded');
  assert.equal(outcome.lastServer.length, 1);
  assert.equal(outcome.lastServer[0].locked, true);
  assert.equal(outcome.lastServer[0].revision, 2);

  const clearJob = box.takeNext(SCOPE_JOB_ID);
  assert.equal(clearJob.type, 'clear');
  assert.equal(clearJob.expectedScopeRevision, 6);
});

test('quota rollback retains a mutation that already succeeded in the same queue', async (t) => {
  const { DrawingOutbox, drainPersistJob } = await loadDrawings(t);
  const accepted = drawingOf('horizontal', [ANCHOR_ONE]);
  const rejected = drawingOf('horizontal', [ANCHOR_ONE], {
    id: '22222222-2222-4222-8222-222222222222',
  });
  const box = new DrawingOutbox(memStore());
  box.setScope(SCOPE_NVDA);
  box.rebaseBase(0);
  box.enqueue({ drawingId: accepted.id, type: 'create', drawing: accepted });
  box.enqueue({ drawingId: rejected.id, type: 'create', drawing: rejected });

  const acceptedJob = box.takeNext(accepted.id);
  const acceptedOutcome = await drainPersistJob({
    outbox: box,
    job: acceptedJob,
    api: persistApi(),
    drawings: [accepted, rejected],
    lastServer: [],
    revisions: new Map(),
  });
  assert.deepEqual(acceptedOutcome.lastServer.map((item) => item.id), [accepted.id]);

  const rejectedJob = box.takeNext(rejected.id);
  const quota = await drainPersistJob({
    outbox: box,
    job: rejectedJob,
    api: persistApi({
      create: async () => {
        const error = new Error('drawings_range_full');
        error.code = 'drawings_range_full';
        error.status = 409;
        throw error;
      },
    }),
    drawings: [accepted, rejected],
    lastServer: acceptedOutcome.lastServer,
    revisions: new Map([[accepted.id, 1]]),
  });
  assert.equal(quota.kind, 'quota');
  assert.deepEqual(quota.apply.drawings.map((item) => item.id), [accepted.id]);
});

test('unrelated remote change safely rebases local create followed by update', async (t) => {
  const { DrawingOutbox, evaluateRemoteVsPending } = await loadDrawings(t);
  const local = drawingOf('horizontal', [ANCHOR_ONE]);
  const edited = { ...local, hidden: true };
  const remoteOnly = drawingOf('horizontal', [ANCHOR_ONE], {
    id: '22222222-2222-4222-8222-222222222222',
  });
  const box = new DrawingOutbox(memStore());
  box.setScope(SCOPE_NVDA);
  box.setScopeRevision(3);
  box.enqueue({ drawingId: local.id, type: 'create', drawing: local });
  box.enqueue({ drawingId: local.id, type: 'update', drawing: edited });

  const evaluation = evaluateRemoteVsPending({
    remoteDrawings: [remoteOnly],
    remoteRevision: 4,
    baseRevision: box.getBaseScopeRevision(),
    jobs: box.snapshot(),
  });
  assert.equal(evaluation.kind, 'merge');
  assert.equal(evaluation.adoptScopeRevision, true);
  assert.equal(evaluation.drawings.find((item) => item.id === local.id).hidden, true);
  assert.equal(evaluation.drawings.some((item) => item.id === remoteOnly.id), true);
});

test('conflict replay keeps replace plus later edits as the visible local intent', async (t) => {
  const { DrawingOutbox, SCOPE_JOB_ID, evaluateRemoteVsPending } = await loadDrawings(t);
  const imported = drawingOf('horizontal', [ANCHOR_ONE]);
  const edited = { ...imported, style: { ...imported.style, color: '#E5484D' } };
  const remoteOnly = drawingOf('horizontal', [ANCHOR_ONE], {
    id: '22222222-2222-4222-8222-222222222222',
  });
  const box = new DrawingOutbox(memStore());
  box.setScope(SCOPE_NVDA);
  box.setScopeRevision(2);
  box.enqueue({
    drawingId: SCOPE_JOB_ID,
    type: 'replace',
    drawings: [imported],
    origin: 'import',
  });
  box.enqueue({ drawingId: imported.id, type: 'update', drawing: edited });

  const evaluation = evaluateRemoteVsPending({
    remoteDrawings: [remoteOnly],
    remoteRevision: 3,
    baseRevision: box.getBaseScopeRevision(),
    jobs: box.snapshot(),
  });
  assert.equal(evaluation.kind, 'conflict');
  assert.deepEqual(evaluation.drawings.map((item) => item.id), [imported.id]);
  assert.equal(evaluation.drawings[0].style.color, '#E5484D');
});

test('keep-local reset removes stale barriers and persists one exact replacement', async (t) => {
  const { DrawingOutbox, SCOPE_JOB_ID, parsePersistJobs, outboxStorageKey } = await loadDrawings(t);
  const store = memStore();
  const desired = drawingOf('horizontal', [ANCHOR_ONE], { hidden: true });
  const box = new DrawingOutbox(store);
  box.setScope(SCOPE_NVDA);
  box.rebaseBase(4);
  box.enqueue({ drawingId: SCOPE_JOB_ID, type: 'clear' });
  box.enqueue({ drawingId: desired.id, type: 'update', drawing: desired });

  const queued = box.replaceWithExactScope([desired], 9, 'conflict_keep');
  assert.ok(queued);
  assert.equal(box.snapshot().length, 1);
  assert.equal(box.snapshot()[0].type, 'replace');
  assert.equal(box.snapshot()[0].origin, 'conflict_keep');
  const flying = box.takeNext(SCOPE_JOB_ID);
  assert.equal(flying.expectedScopeRevision, 9);

  const raw = JSON.parse(store.getItem(outboxStorageKey('acct', 'NVDA', '1d', 'raw')));
  const parsed = parsePersistJobs(raw, SCOPE_NVDA, box.getScopeGeneration());
  assert.equal(parsed.length, 1);
  assert.equal(parsed[0].origin, 'conflict_keep');
});

test('pattern rails carry per-kind color and a single end label', async (t) => {
  const { autoPatternsToMarks } = await loadDrawings(t);
  // bar.t 是 ISO 字符串（projection.nySessionDate 直接 slice），沿用 barsFor 的口径
  const bars = Array.from({ length: 30 }, (_, i) => {
    const day = String(1 + i).padStart(2, '0');
    return { t: `2026-07-${day}T13:30:00Z`, o: 100 + i, h: 101 + i, l: 99 + i, c: 100.5 + i };
  });
  const ctx = { bars, range: '1d', xMin: 0, xMax: 29, yMin: 90, yMax: 140 };
  const stamp = (i) => bars[i].t;
  const key = (i) => `2026-07-${String(1 + i).padStart(2, '0')}`;
  const anchorAt = (i, price) => ({ time: stamp(i), barKey: key(i), price });
  const marks = autoPatternsToMarks([
    {
      id: 'p1', kind: 'support_trend', confidence: 60, status: 'forming',
      anchors: [anchorAt(2, 100), anchorAt(20, 118)],
      color: '#0E9F6E', label: '上升支撑',
    },
    {
      id: 'p2', kind: 'channel', confidence: 60, status: 'forming',
      anchors: [anchorAt(2, 100), anchorAt(20, 118), anchorAt(2, 104), anchorAt(20, 122)],
      color: '#3B59F2', label: '上升通道',
    },
  ], ctx, 0);
  // 此前所有形态线同一根淡灰虚线且无名，图上支撑/阻力/通道边完全分不出来。
  const support = marks.lines[0][0];
  assert.equal(support.lineStyle.color, '#0E9F6E');
  assert.equal(support.label.show, true);
  assert.equal(support.label.formatter, '上升支撑');
  // 标签留在绘图区内（insideEnd*），绝不用 'end'——那会画进 y 轴槽骑在刻度上；
  // 白底药丸保证跨在蜡烛上也读得清（用户截图：「水平箱体」压 190、多形态互叠）。
  assert.equal(support.label.position, 'insideEndTop');
  assert.equal(support.label.backgroundColor, 'rgba(255,255,255,0.88)');
  // 通道两条边同色，但标签只挂第一段——第二条边不重复报名
  const chanA = marks.lines[1][0];
  const chanB = marks.lines[2][0];
  assert.equal(chanA.lineStyle.color, '#3B59F2');
  assert.equal(chanB.lineStyle.color, '#3B59F2');
  assert.equal(chanA.label.show, true);
  // 两个形态落点同价（118）：同价带内顺次换侧防叠印
  assert.equal(chanA.label.position, 'insideEndBottom');
  assert.equal(chanB.label.show, false);
  // 填充也跟线色（不再是灰蒙一层）
  const poly = (marks.polygons ?? [])[0];
  assert.ok(poly, 'channel 应产出填充多边形');
  assert.equal(poly.color, '#3B59F2');
  // 没给 color/label 的形态维持旧行为：灰线、无标签
  const bare = autoPatternsToMarks([
    { id: 'p3', kind: 'support_trend', confidence: 60, status: 'forming', anchors: [anchorAt(2, 100), anchorAt(20, 118)] },
  ], ctx, 0);
  assert.equal(bare.lines[0][0].lineStyle.color, '#5A6788');
  assert.equal(bare.lines[0][0].label.show, false);
});

test('stale quality-gate defaults migrate once and deliberate values survive', async (t) => {
  const { parseLayerSettings, saveLayerSettings, layersStorageKey } = await loadDrawings(t);
  // v1 存量里的 0.70/0.55/0.50 是旧预设常量，不是用户手填的：迁到新默认 0.45，
  // 否则老用户勾着「自动趋势线」也永远看不到线，还以为功能是坏的。
  assert.equal(parseLayerSettings({ minShapeQuality: 0.7 }).minShapeQuality, 0.45);
  assert.equal(parseLayerSettings({ minShapeQuality: 0.55 }).minShapeQuality, 0.45);
  // 手填的其他值原样保留
  assert.equal(parseLayerSettings({ minShapeQuality: 0.6 }).minShapeQuality, 0.6);
  // v2 起迁移封口：用户日后**有意**放回 70% 不能被反复改掉
  assert.equal(parseLayerSettings({ schemaVersion: 2, minShapeQuality: 0.7 }).minShapeQuality, 0.7);
  // 落盘自动盖版本戳，读回后即是 v2 语义
  const store = new Map();
  const storage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) };
  saveLayerSettings('anonymous', { ...parseLayerSettings({}), minShapeQuality: 0.7, preset: 'custom' }, storage);
  const raw = JSON.parse(store.get(layersStorageKey('anonymous')));
  assert.equal(raw.schemaVersion, 2);
  assert.equal(parseLayerSettings(raw).minShapeQuality, 0.7);
});

test('429 drain failure surfaces the server Retry-After so callers can self-heal', async (t) => {
  const { DrawingOutbox, drainPersistJob } = await loadDrawings(t);
  const drawing = drawingOf('horizontal', [ANCHOR_ONE]);
  const box = new DrawingOutbox(memStore());
  box.setScope(SCOPE_NVDA);
  box.rebaseBase(0);
  box.enqueue({ drawingId: drawing.id, type: 'create', drawing });
  const job = box.takeNext(drawing.id);
  const outcome = await drainPersistJob({
    outbox: box,
    job,
    api: persistApi({
      create: async () => {
        const error = new Error('rate_limited');
        error.code = 'rate_limited';
        error.status = 429;
        error.retryAfter = 7;
        throw error;
      },
    }),
    drawings: [drawing],
    lastServer: [],
    revisions: new Map(),
  });
  // 一次 429 之后任务在生产上永远趴在队里等人手点，正是这一族取证反复翻车的
  // 根因：kind 必须仍是 retry（可重放），且把服务器亲口说的等待秒数递出去。
  assert.equal(outcome.kind, 'retry');
  assert.equal(outcome.retryAfterSeconds, 7);
  assert.equal(box.snapshot().length, 1, '429 是限流不是拒绝，任务必须留在队里');
});

test('network drain failure carries no Retry-After and falls back to the ladder', async (t) => {
  const { DrawingOutbox, drainPersistJob, nextDrainRetryDelayMs } = await loadDrawings(t);
  const drawing = drawingOf('horizontal', [ANCHOR_ONE]);
  const box = new DrawingOutbox(memStore());
  box.setScope(SCOPE_NVDA);
  box.rebaseBase(0);
  box.enqueue({ drawingId: drawing.id, type: 'create', drawing });
  const job = box.takeNext(drawing.id);
  const outcome = await drainPersistJob({
    outbox: box,
    job,
    api: persistApi({
      create: async () => {
        throw new TypeError('Failed to fetch');
      },
    }),
    drawings: [drawing],
    lastServer: [],
    revisions: new Map(),
  });
  assert.equal(outcome.kind, 'retry');
  assert.equal(outcome.retryAfterSeconds ?? null, null);
  // 阶梯：5s → 15s → 45s → 60s 封顶；服务器给了秒数就听它的并钳在 1–120s。
  assert.deepEqual(
    [1, 2, 3, 4, 9].map((attempt) => nextDrainRetryDelayMs(attempt)),
    [5_000, 15_000, 45_000, 60_000, 60_000],
  );
  assert.equal(nextDrainRetryDelayMs(1, 7), 7_000);
  assert.equal(nextDrainRetryDelayMs(3, 0), 1_000, '0/负数不允许空转成忙等');
  assert.equal(nextDrainRetryDelayMs(1, 999), 120_000, '超长 Retry-After 不把任务押到天荒地老');
});

test('identity probe failure retries on its own ladder and honors Retry-After', async (t) => {
  const { identityRetryDelayMs } = await loadDrawings(t);
  // /access/status 挂掉的会话没有 60 秒定时兜底（hasPrincipal 门），自愈只能靠
  // 这把梯子；首个探测 429 卡成永久访客时，画的图会静默进访客本地桶、绝不上传。
  assert.deepEqual(
    [1, 2, 3, 4, 9].map((attempt) => identityRetryDelayMs(attempt)),
    [2_000, 5_000, 15_000, 30_000, 30_000],
  );
  assert.equal(identityRetryDelayMs(1, 3), 3_000, '诚实 Retry-After 优先于梯子');
  assert.equal(identityRetryDelayMs(2, 0.4), 1_000, '亚秒值收到 1s，不忙等');
  assert.equal(identityRetryDelayMs(1, 999), 60_000, '上限对齐既有 60s 核验节奏');
  assert.equal(identityRetryDelayMs(1, Number.NaN), 2_000, 'NaN 回落梯子');
});

test('drain releases inflight update so a later clear still runs after 400', async (t) => {
  const { DrawingOutbox, SCOPE_JOB_ID, drainPersistJob } = await loadDrawings(t);
  const drawn = drawingOf('horizontal', [ANCHOR_ONE]);
  const box = new DrawingOutbox(memStore());
  box.setScope(SCOPE_NVDA);
  box.enqueue({ drawingId: drawn.id, type: 'update', drawing: drawn });
  const flying = box.takeNext(drawn.id);
  box.enqueue({ drawingId: SCOPE_JOB_ID, type: 'clear' });
  assert.equal(box.takeNext(SCOPE_JOB_ID), null, 'clear waits for inflight update');
  const outcome = await drainPersistJob({
    outbox: box,
    job: flying,
    api: persistApi({
      update: async () => {
        const err = new Error('invalid_price');
        err.code = 'invalid_price';
        err.status = 400;
        throw err;
      },
    }),
    drawings: [drawn],
    lastServer: [drawn],
    revisions: new Map([[drawn.id, 1]]),
  });
  assert.ok(outcome.kind === 'drop' || outcome.kind === 'superseded');
  assert.notEqual(outcome.status, 'idle');
  assert.ok(outcome.readyIds.includes(SCOPE_JOB_ID));
  const clear = box.takeNext(SCOPE_JOB_ID);
  assert.equal(clear.type, 'clear');
});

test('drain releases inflight create so a later replace still runs after quota', async (t) => {
  const { DrawingOutbox, SCOPE_JOB_ID, drainPersistJob } = await loadDrawings(t);
  const drawn = drawingOf('horizontal', [ANCHOR_ONE]);
  const box = new DrawingOutbox(memStore());
  box.setScope(SCOPE_NVDA);
  box.enqueue({ drawingId: drawn.id, type: 'create', drawing: drawn });
  const flying = box.takeNext(drawn.id);
  box.enqueue({ drawingId: SCOPE_JOB_ID, type: 'replace', drawings: [] });
  assert.equal(box.takeNext(SCOPE_JOB_ID), null, 'replace waits for inflight create');
  const outcome = await drainPersistJob({
    outbox: box,
    job: flying,
    api: persistApi({
      create: async () => {
        const err = new Error('drawings_full');
        err.code = 'drawings_full';
        err.status = 409;
        throw err;
      },
    }),
    drawings: [drawn],
    lastServer: [],
    revisions: new Map(),
  });
  assert.equal(outcome.kind, 'superseded');
  assert.equal(outcome.apply.action, 'none');
  const replace = box.takeNext(SCOPE_JOB_ID);
  assert.equal(replace.type, 'replace');
});

test('update 404 while still local enters conflict and regenerates create/delete', async (t) => {
  const { DrawingOutbox, drainPersistJob, regeneratePersistOps } = await loadDrawings(t);
  const local = drawingOf('horizontal', [ANCHOR_ONE]);
  const serverOnly = drawingOf('horizontal', [ANCHOR_ONE], { id: '22222222-2222-4222-8222-222222222222' });
  const box = new DrawingOutbox(memStore());
  box.setScope(SCOPE_NVDA);
  box.enqueue({ drawingId: local.id, type: 'update', drawing: local });
  const flying = box.takeNext(local.id);
  const outcome = await drainPersistJob({
    outbox: box,
    job: flying,
    api: persistApi({
      update: async () => {
        const err = new Error('drawing_not_found');
        err.code = 'drawing_not_found';
        err.status = 404;
        throw err;
      },
      list: async () => ({ drawings: [serverOnly], scopeRevision: 4 }),
    }),
    drawings: [local],
    lastServer: [serverOnly],
    revisions: new Map([[local.id, 1]]),
  });
  assert.equal(outcome.kind, 'conflict');
  assert.equal(outcome.status, 'conflict');
  assert.notEqual(outcome.status, 'idle');
  assert.ok(outcome.conflict);
  assert.equal(outcome.conflict.scopeRevision, 4);
  const ops = regeneratePersistOps([local], [serverOnly]);
  assert.deepEqual(ops.map((op) => op.type).sort(), ['create', 'delete']);
  assert.equal(box.snapshot().some((job) => job.type === 'create'), false);
});

test('delayed takeServerConflict for another scope is ignored', async (t) => {
  const { DrawingOutbox, conflictSnapshotUsable, applyConflictDecision, SCOPE_JOB_ID } = await loadDrawings(t);
  const box = new DrawingOutbox(null);
  const aapl = { identity: 'acct', ticker: 'AAPL', range: '1d', adjustment: 'raw' };
  const msft = { identity: 'acct', ticker: 'MSFT', range: '1d', adjustment: 'raw' };
  const aaplGen = box.setScope(aapl);
  const snapshot = {
    scope: aapl,
    scopeGeneration: aaplGen,
    scopeRevision: 3,
    drawings: [drawingOf('horizontal', [ANCHOR_ONE], { ticker: 'AAPL' })],
  };
  const msftGen = box.setScope(msft);
  box.enqueue({ drawingId: SCOPE_JOB_ID, type: 'clear' });
  assert.equal(conflictSnapshotUsable(snapshot, box.getScope(), msftGen), false);
  const decision = applyConflictDecision({
    snapshot,
    currentScope: box.getScope(),
    generation: box.getScopeGeneration(),
    intent: 'take',
  });
  assert.equal(decision.action, 'ignore');
  assert.equal(box.isEmpty(), false, 'foreign take must not cancelAll the current Outbox');
  assert.equal(box.getScope().ticker, 'MSFT');
});

test('empty cache + pending clear does not show the previous ticker before list resolves', async (t) => {
  const {
    DrawingOutbox, SCOPE_JOB_ID, previewScopeLoad, completeScopeLoad,
    saveDrawings, loadDrawings: readDrawings, drawingsStorageKey,
  } = await loadDrawings(t);
  const store = memStore();
  const aaplScope = { identity: 'acct', ticker: 'AAPL', range: '1d', adjustment: 'raw' };
  const msftScope = { identity: 'acct', ticker: 'MSFT', range: '1d', adjustment: 'raw' };
  const aapl = drawingOf('horizontal', [ANCHOR_ONE], { ticker: 'AAPL' });
  saveDrawings(drawingsStorageKey('acct', 'AAPL', '1d', 'raw'), [aapl], store);
  saveDrawings(drawingsStorageKey('acct', 'MSFT', '1d', 'raw'), [], store);

  const box = new DrawingOutbox(store);
  const aaplGen = box.setScope(aaplScope);
  box.setScopeRevision(4);
  box.setScope(msftScope);
  box.enqueue({ drawingId: SCOPE_JOB_ID, type: 'clear' });
  const msftGen = box.getScopeGeneration();
  assert.notEqual(msftGen, aaplGen);

  const cached = readDrawings(drawingsStorageKey('acct', 'MSFT', '1d', 'raw'), store);
  assert.equal(cached.ok, true);
  assert.equal(cached.state, 'empty');
  const preview = previewScopeLoad(cached);
  assert.deepEqual(preview.drawings, []);
  assert.equal(preview.drawings.some((row) => row.ticker === 'AAPL'), false);
  assert.equal(preview.status, 'idle');
  assert.equal(box.getScope().ticker, 'MSFT');
  assert.equal(box.getScopeRevision(), 0);
  assert.equal(box.snapshot().some((job) => job.type === 'clear'), true);

  let release;
  const deferred = new Promise((resolve) => { release = resolve; });
  const pending = completeScopeLoad({
    generation: msftGen,
    outbox: box,
    cached,
    list: () => deferred,
  });
  assert.deepEqual(previewScopeLoad(cached).drawings, [], 'still empty while GET is in flight');
  assert.equal(box.isBaselineReady(), false);
  release({ drawings: [aapl], scopeRevision: 9 });
  const outcome = await pending;
  assert.equal(outcome.foreign, false);
  assert.equal(outcome.conflict, true);
  assert.equal(outcome.status, 'conflict');
  assert.equal(outcome.drain, false);
  assert.equal(outcome.drawings.length, 0);
  assert.equal(outcome.drawings.some((row) => row.ticker === 'AAPL'), false);

  let releaseStale;
  const staleList = new Promise((resolve) => { releaseStale = resolve; });
  const stale = completeScopeLoad({
    generation: aaplGen,
    outbox: box,
    cached,
    list: () => staleList,
  });
  releaseStale({ drawings: [aapl], scopeRevision: 1 });
  const foreign = await stale;
  assert.equal(foreign.foreign, true);
  assert.equal(foreign.apply, 'none');
  assert.deepEqual(foreign.drawings, []);
});

test('area mode disables pane and MA layer inputs', async (t) => {
  const { layerInputEnabled, LAYERS } = await loadDrawings(t);
  const byId = Object.fromEntries(LAYERS.map((layer) => [layer.id, layer]));
  for (const id of ['rsi', 'macd', 'ma20']) {
    assert.equal(layerInputEnabled(byId[id], 'area').enabled, false, id);
    assert.equal(layerInputEnabled(byId[id], 'candle').enabled, true, id);
  }
  assert.equal(layerInputEnabled(byId.rsi, 'area').reason, 'area_no_panes_or_ma');
  assert.equal(layerInputEnabled(byId.swings, 'area').enabled, true);
});

test('parseList and parseSaved throw on illegal server bodies', async (t) => {
  const { parseList, parseSaved, DrawingContractError } = await loadDrawings(t);
  const throws = (fn) => {
    try {
      fn();
      return null;
    } catch (error) {
      return error.name;
    }
  };
  assert.equal(throws(() => parseList({})), 'DrawingContractError');
  assert.equal(throws(() => parseList({ drawings: 'nope', scope_revision: 0 })), 'DrawingContractError');
  assert.equal(throws(() => parseList({ drawings: [{ kind: 'nope' }], scope_revision: 0 })), 'DrawingContractError');
  assert.equal(throws(() => parseSaved({ id: 'not-a-uuid' })), 'DrawingContractError');
  assert.equal(DrawingContractError.name, 'DrawingContractError');
});

test('quota rollback stays on the last server snapshot after setScope', async (t) => {
  const { DrawingOutbox, drainPersistJob } = await loadDrawings(t);
  const server = drawingOf('horizontal', [ANCHOR_ONE]);
  const extra = drawingOf('horizontal', [ANCHOR_ONE], { id: '22222222-2222-4222-8222-222222222222' });
  const box = new DrawingOutbox(memStore());
  box.setScope(SCOPE_NVDA);
  box.enqueue({ drawingId: extra.id, type: 'create', drawing: extra });
  const flying = box.takeNext(extra.id);
  const outcome = await drainPersistJob({
    outbox: box,
    job: flying,
    api: persistApi({
      create: async () => {
        const err = new Error('drawings_range_full');
        err.code = 'drawings_range_full';
        err.status = 409;
        throw err;
      },
    }),
    drawings: [server, extra],
    lastServer: [server],
    revisions: new Map([[server.id, 1]]),
  });
  assert.equal(outcome.kind, 'quota');
  assert.equal(outcome.apply.action, 'rollback');
  assert.deepEqual(outcome.apply.drawings.map((item) => item.id), [server.id]);
  const msft = { identity: 'acct', ticker: 'MSFT', range: '1d', adjustment: 'raw' };
  box.setScope(msft);
  const delayed = await drainPersistJob({
    outbox: box,
    job: flying,
    api: persistApi(),
    drawings: [],
    lastServer: [],
    revisions: new Map(),
  });
  assert.equal(delayed.foreign, true);
  assert.equal(delayed.apply.action, 'none');
});

test('datazoom record survives a later setOption rebuild', async (t) => {
  const { insideZoom, zoomFromOption } = await loadDrawings(t);
  const saved = { start: 20, end: 90, pinnedEnd: false };
  const first = insideZoom('1d', 200, [0], saved);
  assert.equal(first[0].startValue, 20);
  assert.equal(first[0].endValue, 90);
  const recorded = zoomFromOption({ dataZoom: first }, 200);
  const rebuilt = insideZoom('1d', 200, [0], recorded);
  assert.equal(rebuilt[0].startValue, first[0].startValue);
  assert.equal(rebuilt[0].endValue, first[0].endValue);
});

test('persisted barrier order survives reload hydrate', async (t) => {
  const { DrawingOutbox, SCOPE_JOB_ID, parsePersistJobs, outboxStorageKey } = await loadDrawings(t);
  const store = memStore();
  const drawn = drawingOf('horizontal', [ANCHOR_ONE]);
  const box = new DrawingOutbox(store);
  box.setScope(SCOPE_NVDA);
  box.enqueue({ drawingId: drawn.id, type: 'update', drawing: drawn });
  const flying = box.takeNext(drawn.id);
  box.enqueue({ drawingId: SCOPE_JOB_ID, type: 'clear' });
  assert.equal(box.takeNext(SCOPE_JOB_ID), null);
  const raw = JSON.parse(store.getItem(outboxStorageKey('acct', 'NVDA', '1d', 'raw')));
  const restored = new DrawingOutbox(store);
  restored.setScope(SCOPE_NVDA);
  const jobs = restored.snapshot();
  assert.ok(jobs.some((job) => job.type === 'update' || job.drawingId === drawn.id));
  assert.ok(jobs.some((job) => job.type === 'clear'));
  const parsed = parsePersistJobs(raw, SCOPE_NVDA, restored.getScopeGeneration());
  const seqs = parsed.map((job) => job.seq);
  assert.deepEqual(seqs, [...seqs].sort((a, b) => a - b));
  assert.equal(restored.getBaseScopeRevision(), 0);
  void flying;
});

test('takeNext does not overwrite an already stamped expectedScopeRevision', async (t) => {
  const { DrawingOutbox } = await loadDrawings(t);
  const drawn = drawingOf('horizontal', [ANCHOR_ONE]);
  const box = new DrawingOutbox(memStore());
  box.setScope(SCOPE_NVDA);
  box.setScopeRevision(3);
  box.enqueue({ drawingId: drawn.id, type: 'update', drawing: drawn });
  assert.equal(box.getBaseScopeRevision(), 3);
  const first = box.takeNext(drawn.id);
  assert.equal(first.expectedScopeRevision, 3);
  box.setScopeRevision(9);
  box.failKeep(drawn.id);
  const again = box.takeNext(drawn.id);
  assert.equal(again.expectedScopeRevision, 3);
});

test('offline update of X against a newer remote revision is conflict', async (t) => {
  const { DrawingOutbox, completeScopeLoad, evaluateRemoteVsPending } = await loadDrawings(t);
  const local = drawingOf('horizontal', [ANCHOR_ONE], { style: { color: '#111111', width: 2, dash: 'solid' } });
  const remote = { ...local, revision: 4, style: { color: '#ff0000', width: 2, dash: 'solid' } };
  const box = new DrawingOutbox(memStore());
  box.setScope(SCOPE_NVDA);
  box.setScopeRevision(2);
  box.enqueue({ drawingId: local.id, type: 'update', drawing: local });
  const evaluation = evaluateRemoteVsPending({
    remoteDrawings: [remote],
    remoteRevision: 5,
    baseRevision: box.getBaseScopeRevision(),
    jobs: box.snapshot(),
  });
  assert.equal(evaluation.kind, 'conflict');
  assert.equal(evaluation.drain, false);
  const outcome = await completeScopeLoad({
    generation: box.getScopeGeneration(),
    outbox: box,
    cached: { ok: true, missing: false, drawings: [local], state: 'ok', recoverable: true, error: null },
    list: async () => ({ drawings: [remote], scopeRevision: 5 }),
  });
  assert.equal(outcome.conflict, true);
  assert.equal(outcome.drain, false);
  assert.equal(box.snapshot()[0].expectedScopeRevision, undefined);
});

test('offline clear vs remote B is conflict and keeps B', async (t) => {
  const { DrawingOutbox, SCOPE_JOB_ID, completeScopeLoad, replayPendingOps } = await loadDrawings(t);
  const extra = drawingOf('horizontal', [ANCHOR_ONE], { id: '22222222-2222-4222-8222-222222222222' });
  const box = new DrawingOutbox(memStore());
  box.setScope(SCOPE_NVDA);
  box.setScopeRevision(1);
  box.enqueue({ drawingId: SCOPE_JOB_ID, type: 'clear' });
  const outcome = await completeScopeLoad({
    generation: box.getScopeGeneration(),
    outbox: box,
    cached: { ok: true, missing: false, drawings: [], state: 'empty', recoverable: true, error: null },
    list: async () => ({ drawings: [extra], scopeRevision: 4 }),
  });
  assert.equal(outcome.conflict, true);
  assert.equal(outcome.drain, false);
  // Local intent is clear: the conflict view is empty. Take-server still has B.
  assert.equal(outcome.drawings.length, 0);
  assert.equal(outcome.lastServer.length, 1);
  assert.equal(outcome.lastServer[0].id, extra.id);
  assert.deepEqual(replayPendingOps([extra], box.snapshot().filter((job) => job.type !== 'clear')).map((row) => row.id), [extra.id]);
});

test('offline update of A plus remote B rebases to both', async (t) => {
  const { DrawingOutbox, completeScopeLoad, replayPendingOps } = await loadDrawings(t);
  const local = drawingOf('horizontal', [ANCHOR_ONE], { style: { color: '#00aa00', width: 2, dash: 'solid' } });
  const extra = drawingOf('horizontal', [ANCHOR_ONE], { id: '22222222-2222-4222-8222-222222222222', ticker: 'NVDA' });
  const box = new DrawingOutbox(memStore());
  box.setScope(SCOPE_NVDA);
  box.setScopeRevision(3);
  box.enqueue({ drawingId: local.id, type: 'update', drawing: { ...local, revision: 1 } });
  const remote = [{ ...local, revision: 1 }, extra];
  const outcome = await completeScopeLoad({
    generation: box.getScopeGeneration(),
    outbox: box,
    cached: { ok: true, missing: false, drawings: [local], state: 'ok', recoverable: true, error: null },
    list: async () => ({ drawings: remote, scopeRevision: 6 }),
  });
  assert.equal(outcome.conflict, false);
  assert.equal(outcome.apply, 'replay');
  const ids = outcome.drawings.map((row) => row.id).sort();
  assert.deepEqual(ids, [local.id, extra.id].sort());
  assert.equal(outcome.drawings.find((row) => row.id === local.id).style.color, '#00aa00');
  assert.equal(outcome.drain, true);
  const replayed = replayPendingOps(remote, box.snapshot());
  assert.equal(replayed.length, 2);
});

test('completeScopeLoad with pending jobs uses remote as baseline not empty cache', async (t) => {
  const { DrawingOutbox, completeScopeLoad } = await loadDrawings(t);
  const local = drawingOf('horizontal', [ANCHOR_ONE]);
  const extra = drawingOf('horizontal', [ANCHOR_ONE], { id: '22222222-2222-4222-8222-222222222222' });
  const box = new DrawingOutbox(memStore());
  box.setScope(SCOPE_NVDA);
  box.setScopeRevision(1);
  box.enqueue({ drawingId: local.id, type: 'create', drawing: local });
  const outcome = await completeScopeLoad({
    generation: box.getScopeGeneration(),
    outbox: box,
    cached: { ok: true, missing: false, drawings: [], state: 'empty', recoverable: true, error: null },
    list: async () => ({ drawings: [extra], scopeRevision: 1 }),
  });
  assert.equal(outcome.apply, 'replay');
  assert.equal(outcome.drawings.some((row) => row.id === extra.id), true);
  assert.equal(outcome.drawings.some((row) => row.id === local.id), true);
});

test('quota rollback snapshot from AAPL cannot paint MSFT', async (t) => {
  const { DrawingOutbox, quotaRollbackDrawings, completeScopeLoad } = await loadDrawings(t);
  const aapl = drawingOf('horizontal', [ANCHOR_ONE], { ticker: 'AAPL' });
  const msft = drawingOf('horizontal', [ANCHOR_ONE], { ticker: 'MSFT', id: '22222222-2222-4222-8222-222222222222' });
  const aaplScope = { identity: 'acct', ticker: 'AAPL', range: '1d', adjustment: 'raw' };
  const msftScope = { identity: 'acct', ticker: 'MSFT', range: '1d', adjustment: 'raw' };
  const box = new DrawingOutbox(memStore());
  const aaplGen = box.setScope(aaplScope);
  const snapshot = { scope: aaplScope, scopeGeneration: aaplGen, drawings: [aapl] };
  box.setScope(msftScope);
  const msftGen = box.getScopeGeneration();
  assert.deepEqual(quotaRollbackDrawings(snapshot, box.getScope(), msftGen), []);
  box.enqueue({ drawingId: msft.id, type: 'create', drawing: msft });
  assert.equal(box.isBaselineReady(), false);
  let release;
  const deferred = new Promise((resolve) => { release = resolve; });
  const pending = completeScopeLoad({
    generation: msftGen,
    outbox: box,
    cached: { ok: true, missing: false, drawings: [msft], state: 'ok', recoverable: true, error: null },
    list: () => deferred,
  });
  assert.equal(box.isBaselineReady(), false);
  release({ drawings: [], scopeRevision: 0 });
  const outcome = await pending;
  assert.equal(outcome.drawings.some((row) => row.ticker === 'AAPL'), false);
  assert.equal(quotaRollbackDrawings(snapshot, box.getScope(), box.getScopeGeneration()).length, 0);
});

test('superseded update 404 after clear does not regenerate create', async (t) => {
  const { DrawingOutbox, SCOPE_JOB_ID, drainPersistJob } = await loadDrawings(t);
  const drawn = drawingOf('horizontal', [ANCHOR_ONE]);
  const box = new DrawingOutbox(memStore());
  box.setScope(SCOPE_NVDA);
  box.markBaselineReady();
  box.enqueue({ drawingId: drawn.id, type: 'update', drawing: drawn });
  const flying = box.takeNext(drawn.id);
  box.enqueue({ drawingId: SCOPE_JOB_ID, type: 'clear' });
  let listed = 0;
  const outcome = await drainPersistJob({
    outbox: box,
    job: flying,
    api: persistApi({
      update: async () => {
        const err = new Error('drawing_not_found');
        err.code = 'drawing_not_found';
        err.status = 404;
        throw err;
      },
      list: async () => {
        listed += 1;
        return { drawings: [drawn], scopeRevision: 9 };
      },
    }),
    drawings: [drawn],
    lastServer: [drawn],
    revisions: new Map([[drawn.id, 1]]),
  });
  assert.equal(outcome.kind, 'superseded');
  assert.equal(listed, 0);
  assert.equal(outcome.conflict, null);
  const clear = box.takeNext(SCOPE_JOB_ID);
  assert.equal(clear.type, 'clear');
  const cleared = await drainPersistJob({
    outbox: box,
    job: clear,
    api: persistApi(),
    drawings: [],
    lastServer: [],
    revisions: new Map(),
  });
  assert.equal(cleared.kind, 'success');
  assert.equal(box.isEmpty(), true);
  assert.equal(box.snapshot().some((job) => job.type === 'create'), false);
});

test('superseded update 404 after replace does not regenerate create', async (t) => {
  const { DrawingOutbox, SCOPE_JOB_ID, drainPersistJob } = await loadDrawings(t);
  const drawn = drawingOf('horizontal', [ANCHOR_ONE]);
  const box = new DrawingOutbox(memStore());
  box.setScope(SCOPE_NVDA);
  box.markBaselineReady();
  box.enqueue({ drawingId: drawn.id, type: 'update', drawing: drawn });
  const flying = box.takeNext(drawn.id);
  box.enqueue({ drawingId: SCOPE_JOB_ID, type: 'replace', drawings: [] });
  let listed = 0;
  const outcome = await drainPersistJob({
    outbox: box,
    job: flying,
    api: persistApi({
      update: async () => {
        const err = new Error('drawing_not_found');
        err.code = 'drawing_not_found';
        err.status = 404;
        throw err;
      },
      list: async () => {
        listed += 1;
        return { drawings: [drawn], scopeRevision: 4 };
      },
    }),
    drawings: [drawn],
    lastServer: [drawn],
    revisions: new Map([[drawn.id, 1]]),
  });
  assert.equal(outcome.kind, 'superseded');
  assert.equal(listed, 0);
  const replace = box.takeNext(SCOPE_JOB_ID);
  const replaced = await drainPersistJob({
    outbox: box,
    job: replace,
    api: persistApi(),
    drawings: [],
    lastServer: [],
    revisions: new Map(),
  });
  assert.equal(replaced.kind, 'success');
  assert.equal(box.isEmpty(), true);
  assert.equal(box.snapshot().some((job) => job.type === 'create'), false);
});

test('import replace drops a pending update so no old PUT remains', async (t) => {
  const { DrawingOutbox, SCOPE_JOB_ID } = await loadDrawings(t);
  const old = drawingOf('horizontal', [ANCHOR_ONE]);
  const incoming = drawingOf('horizontal', [ANCHOR_ONE], { id: '22222222-2222-4222-8222-222222222222' });
  const box = new DrawingOutbox(memStore());
  box.setScope(SCOPE_NVDA);
  box.enqueue({ drawingId: old.id, type: 'update', drawing: old });
  box.enqueue({ drawingId: SCOPE_JOB_ID, type: 'replace', drawings: [incoming] });
  const types = box.snapshot().map((job) => job.type);
  assert.deepEqual(types, ['replace']);
  assert.equal(box.snapshot().some((job) => job.drawingId === old.id), false);
});

test('exportDrawings always uses the current list not a rejected import', async (t) => {
  const { exportDrawings } = await loadDrawings(t);
  const current = drawingOf('horizontal', [ANCHOR_ONE], { style: { color: '#ff0000', width: 2, dash: 'solid' } });
  const payload = JSON.parse(exportDrawings([current]));
  assert.equal(payload.drawings[0].style.color, '#ff0000');
  assert.equal(payload.drawings[0].id, current.id);
});

test('fingerprint mismatch snap candidates omit ungated swings and levels', async (t) => {
  const { snapCandidatesFromOverlays, analysisMatchesChart, filterOverlays, settingsFromPreset } = await loadDrawings(t);
  const bundle = {
    ticker: 'NVDA',
    range: '1d',
    adjustment: 'raw',
    dataThrough: '2026-01-01',
    barFingerprint: 'aaa',
    barCount: 2,
    lastClose: 10,
    dates: ['2026-01-01'],
    overlays: [
      { kind: 'swing', geometry: { anchors: [{ price: 99.5 }] }, group: 'price', id: 's1', sourceId: 'x', algorithmVersion: 'v', status: 'forming', direction: 'neutral', shapeQuality: 1, displayPriority: 1, evidence: {}, formationStart: '', formationEnd: '', dataThrough: '', label: '', detail: '' },
      { kind: 'pivot', geometry: { pivot: 80, invalidation: 70 }, group: 'price', id: 'p1', sourceId: 'x', algorithmVersion: 'v', status: 'forming', direction: 'neutral', shapeQuality: 1, displayPriority: 1, evidence: {}, formationStart: '', formationEnd: '', dataThrough: '', label: '', detail: '' },
    ],
    indicatorPanes: [],
    strengthContext: null,
  };
  assert.equal(analysisMatchesChart(bundle, {
    ticker: 'NVDA', range: '1d', adjustment: 'raw', dataThrough: '2026-01-01',
    barCount: 2, lastClose: 10, fingerprint: 'bbb',
  }), false);
  const gated = analysisMatchesChart(bundle, {
    ticker: 'NVDA', range: '1d', adjustment: 'raw', dataThrough: '2026-01-01',
    barCount: 2, lastClose: 10, fingerprint: 'bbb',
  }) ? filterOverlays(bundle.overlays, settingsFromPreset('all')) : [];
  const candidates = snapCandidatesFromOverlays(gated);
  assert.equal(candidates.some((row) => row.price === 99.5 || row.price === 80), false);
});

test('parseMutation throws without scope_revision', async (t) => {
  const { parseMutation, parseSaved, DrawingContractError } = await loadDrawings(t);
  const throws = (fn) => {
    try {
      fn();
      return null;
    } catch (error) {
      return error.name;
    }
  };
  assert.equal(throws(() => parseMutation({ ok: true })), 'DrawingContractError');
  assert.equal(throws(() => parseSaved({
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
    revision: 1,
    createdAt: '2026-07-06T13:30:00Z',
    updatedAt: '2026-07-06T13:30:00Z',
  })), 'DrawingContractError');
  assert.equal(DrawingContractError.name, 'DrawingContractError');
});

test('offline delete of X against a newer remote revision is conflict', async (t) => {
  const { DrawingOutbox, completeScopeLoad, evaluateRemoteVsPending } = await loadDrawings(t);
  const local = drawingOf('horizontal', [ANCHOR_ONE]);
  const remote = { ...local, revision: 4, style: { color: '#ff0000', width: 2, dash: 'solid' } };
  const box = new DrawingOutbox(memStore());
  box.setScope(SCOPE_NVDA);
  box.setScopeRevision(2);
  box.enqueue({
    drawingId: local.id,
    type: 'delete',
    drawing: local,
    expectedDrawingRevision: local.revision,
  });
  assert.equal(box.snapshot()[0].expectedDrawingRevision, 1);
  const evaluation = evaluateRemoteVsPending({
    remoteDrawings: [remote],
    remoteRevision: 5,
    baseRevision: box.getBaseScopeRevision(),
    jobs: box.snapshot(),
  });
  assert.equal(evaluation.kind, 'conflict');
  assert.equal(evaluation.drain, false);
  assert.equal(evaluation.adoptScopeRevision, false);
  const outcome = await completeScopeLoad({
    generation: box.getScopeGeneration(),
    outbox: box,
    cached: { ok: true, missing: false, drawings: [], state: 'empty', recoverable: true, error: null },
    list: async () => ({ drawings: [remote], scopeRevision: 5 }),
  });
  assert.equal(outcome.conflict, true);
  assert.equal(outcome.drain, false);
  assert.equal(outcome.baselineReady, true);
  const pending = box.takeNext(local.id);
  assert.equal(pending.type, 'delete');
  assert.equal(pending.expectedScopeRevision, 2);
  assert.notEqual(pending.expectedScopeRevision, 5);
  assert.equal(pending.expectedDrawingRevision, 1);
});

test('legacy delete job without drawing revision conflicts when remote X changed', async (t) => {
  const { DrawingOutbox, completeScopeLoad, evaluateRemoteVsPending } = await loadDrawings(t);
  const local = drawingOf('horizontal', [ANCHOR_ONE]);
  const remote = { ...local, revision: 4 };
  const box = new DrawingOutbox(memStore());
  box.setScope(SCOPE_NVDA);
  box.setScopeRevision(2);
  box.enqueue({ drawingId: local.id, type: 'delete' });
  assert.equal(box.snapshot()[0].expectedDrawingRevision, undefined);
  const evaluation = evaluateRemoteVsPending({
    remoteDrawings: [remote],
    remoteRevision: 5,
    baseRevision: box.getBaseScopeRevision(),
    jobs: box.snapshot(),
  });
  assert.equal(evaluation.kind, 'conflict');
  assert.equal(evaluation.drain, false);
  const outcome = await completeScopeLoad({
    generation: box.getScopeGeneration(),
    outbox: box,
    cached: { ok: true, missing: false, drawings: [], state: 'empty', recoverable: true, error: null },
    list: async () => ({ drawings: [remote], scopeRevision: 5 }),
  });
  assert.equal(outcome.conflict, true);
  assert.equal(outcome.drain, false);
  const pending = box.takeNext(local.id);
  assert.equal(pending.expectedScopeRevision, 2);
});

test('delete job persists expectedDrawingRevision across hydrate', async (t) => {
  const { DrawingOutbox, parsePersistJobs, outboxStorageKey } = await loadDrawings(t);
  const local = drawingOf('horizontal', [ANCHOR_ONE]);
  const store = memStore();
  const box = new DrawingOutbox(store);
  box.setScope(SCOPE_NVDA);
  box.setScopeRevision(2);
  box.enqueue({ drawingId: local.id, type: 'delete', drawing: local });
  assert.equal(box.getBaseScopeRevision(), 2);
  assert.equal(box.snapshot()[0].expectedDrawingRevision, 1);
  const raw = JSON.parse(store.getItem(outboxStorageKey('acct', 'NVDA', '1d', 'raw')));
  const parsed = parsePersistJobs(raw, SCOPE_NVDA, 1);
  assert.equal(parsed[0].type, 'delete');
  assert.equal(parsed[0].expectedDrawingRevision, 1);
  const restored = new DrawingOutbox(store);
  restored.setScope(SCOPE_NVDA);
  assert.equal(restored.getBaseScopeRevision(), 2);
  assert.equal(restored.snapshot()[0].expectedDrawingRevision, 1);
});

test('GET failure with pending jobs retries completeScopeLoad not drain', async (t) => {
  const { DrawingOutbox, completeScopeLoad, resolveRetryAction } = await loadDrawings(t);
  const local = drawingOf('horizontal', [ANCHOR_ONE]);
  const box = new DrawingOutbox(memStore());
  box.setScope(SCOPE_NVDA);
  box.setScopeRevision(2);
  box.enqueue({ drawingId: local.id, type: 'update', drawing: local });
  let lists = 0;
  const first = await completeScopeLoad({
    generation: box.getScopeGeneration(),
    outbox: box,
    cached: { ok: true, missing: false, drawings: [local], state: 'ok', recoverable: true, error: null },
    list: async () => {
      lists += 1;
      const err = new Error('offline');
      err.status = 503;
      throw err;
    },
  });
  assert.equal(first.status, 'write_failed');
  assert.equal(first.baselineReady, false);
  assert.equal(first.drain, false);
  assert.equal(box.isBaselineReady(), false);
  assert.equal(box.isEmpty(), false);
  assert.equal(
    resolveRetryAction('write_failed', box.isEmpty(), box.isBaselineReady()),
    'reload',
  );
  const second = await completeScopeLoad({
    generation: box.getScopeGeneration(),
    outbox: box,
    cached: { ok: true, missing: false, drawings: [local], state: 'ok', recoverable: true, error: null },
    list: async () => {
      lists += 1;
      return { drawings: [local], scopeRevision: 2 };
    },
  });
  assert.equal(lists, 2);
  assert.equal(second.baselineReady, true);
  assert.equal(box.isBaselineReady(), true);
  assert.equal(second.status, 'saving');
  assert.equal(second.drain, true);
  assert.equal(
    resolveRetryAction('write_failed', box.isEmpty(), box.isBaselineReady()),
    'replay',
  );
});
