import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { EChartsInstance } from '@/lib/chart';
import { barKeyOf, nudgeAnchors, snapBarIndex } from './projection.ts';
import { drawingsApi, isAuthError, isConflictError } from './api.ts';
import {
  draftOverlay,
  drawingsToMarks,
  graphicFromOverlay,
  projectToPixels,
  selectionOverlay,
  toProjectedDrawing,
  type BarLike,
  type OverlayGeometry,
  type RenderContext,
} from './renderer.ts';
import { loadDrawings, saveDrawings, anonymousStorageKey, drawingsStorageKey } from './storage.ts';
import { exportDrawings, validateImport, whitelistStyle, whitelistText } from './schema.ts';
import {
  canRedo,
  canUndo,
  createHistory,
  historyPush,
  historyRedo,
  historyReplace,
  historyUndo,
  type HistoryState,
} from './history.ts';
import { hitTestDrawings, type ProjectedDrawing } from './hitTest.ts';
import { ohlcCandidates, snapPointer } from './snap.ts';
import { addDraftPoint, isTextInputTarget, type DrawingTool, type InProgressDraw } from './tools.ts';
import type { ChartAdjustment, ChartDrawing, ChartRange, DrawingKind, DrawingStyle, Point } from './types.ts';
import {
  DrawingOutbox,
  SCOPE_JOB_ID,
  applyPersistResponse,
  diffPersistOps,
  keepLocalWithServerRevisions,
  patchRevision,
  replaceDrawing,
  resolveListApply,
  resolveRetryAction,
  type PersistJob,
  type ScopeKey,
} from './sync.ts';
import { applyPixelShiftConstraint, dragMove, type DragOrigin } from './drag.ts';
import { resolvePaintColor } from './schema.ts';

export type SyncStatus = 'guest' | 'idle' | 'saving' | 'unsynced' | 'conflict';

export interface DrawingIdentity {
  signedIn: boolean;
  key: string;
}

function newId(): string {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID();
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (ch) => {
    const r = (Math.random() * 16) | 0;
    return (ch === 'x' ? r : (r & 0x3) | 0x8).toString(16);
  });
}

function nowIso(): string {
  return new Date().toISOString();
}

export function useDrawingController(args: {
  ticker: string;
  range: ChartRange;
  adjustment?: ChartAdjustment;
  bars: BarLike[] | undefined;
  ma20?: (number | null)[];
  swingPrices?: number[];
  levelPrices?: number[];
  chart: EChartsInstance | null;
  identity: DrawingIdentity;
  measureActive: boolean;
  onCancelMeasure: () => void;
  reducedMotion?: boolean;
}) {
  const adjustment = args.adjustment ?? 'raw';
  const [tool, setToolState] = useState<DrawingTool>('select');
  const [drawings, setDrawings] = useState<ChartDrawing[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [inProgress, setInProgress] = useState<InProgressDraw | null>(null);
  const [history, setHistory] = useState<HistoryState<ChartDrawing[]>>(createHistory([]));
  const [syncStatus, setSyncStatus] = useState<SyncStatus>(args.identity.signedIn ? 'idle' : 'guest');
  const [syncHint, setSyncHint] = useState<string | null>(null);
  const [importError, setImportError] = useState<string | null>(null);
  const [autoPatternsEnabled, setAutoPatternsEnabled] = useState(true);
  const [expanded, setExpanded] = useState(false);
  const [draftText, setDraftText] = useState('');
  const [focusAnchor, setFocusAnchor] = useState<number | null>(null);
  const dragRef = useRef<DragOrigin | null>(null);
  const dragPreviewRef = useRef<ChartDrawing | null>(null);
  const rafRef = useRef(0);
  const styleTimers = useRef(new Map<string, ReturnType<typeof setTimeout>>());
  const drawingsRef = useRef(drawings);
  const outboxRef = useRef(new DrawingOutbox());
  const conflictServerRef = useRef<ChartDrawing[] | null>(null);
  const signedIn = args.identity.signedIn;
  const storageKey = signedIn
    ? drawingsStorageKey(args.identity.key, args.ticker, args.range, adjustment)
    : anonymousStorageKey(args.ticker, args.range, adjustment);

  useEffect(() => {
    drawingsRef.current = drawings;
  }, [drawings]);

  const currentScope = useCallback((): ScopeKey => ({
    identity: args.identity.key,
    ticker: args.ticker,
    range: args.range,
    adjustment,
  }), [adjustment, args.identity.key, args.range, args.ticker]);

  const writeLocal = useCallback((next: ChartDrawing[], recordHistory: boolean) => {
    drawingsRef.current = next;
    setDrawings(next);
    setHistory((prev) => (recordHistory ? historyPush(prev, next) : historyReplace(prev, next)));
    saveDrawings(storageKey, next);
  }, [storageKey]);

  const commitLocal = useCallback((next: ChartDrawing[], recordHistory: boolean) => {
    writeLocal(next, recordHistory);
  }, [writeLocal]);

  const applyJobResult = useCallback((job: PersistJob, saved: ChartDrawing | null) => {
    const outbox = outboxRef.current;
    const action = applyPersistResponse({
      job,
      currentScope: outbox.getScope(),
      currentScopeGeneration: outbox.getScopeGeneration(),
      latestGenerationForId: outbox.latestGeneration(job.drawingId),
      responseDrawing: saved,
    });
    if (action.action === 'ignore') return;
    if (action.action === 'replace') {
      const next = replaceDrawing(drawingsRef.current, action.drawing);
      drawingsRef.current = next;
      setDrawings(next);
      setHistory((hist) => historyReplace(hist, next));
      saveDrawings(storageKey, next);
      return;
    }
    const next = patchRevision(drawingsRef.current, action.id, action.revision);
    drawingsRef.current = next;
    setDrawings(next);
    setHistory((hist) => historyReplace(hist, next));
    saveDrawings(storageKey, next);
  }, [storageKey]);

  const drain = useCallback(async (drawingId: string) => {
    if (!signedIn) return;
    const outbox = outboxRef.current;
    while (true) {
      const job = outbox.takeNext(drawingId);
      if (!job) return;
      setSyncStatus('saving');
      try {
        if (job.type === 'create' && job.drawing) {
          const saved = await drawingsApi.create(job.drawing);
          applyJobResult(job, saved);
        } else if (job.type === 'update' && job.drawing) {
          const local = drawingsRef.current.find((item) => item.id === job.drawing?.id) ?? job.drawing;
          const saved = await drawingsApi.update({ ...job.drawing, revision: local.revision });
          applyJobResult(job, saved);
        } else if (job.type === 'delete') {
          await drawingsApi.remove(job.drawingId);
        } else if (job.type === 'clear') {
          await drawingsApi.clearScope(job.scope.ticker, job.scope.range, job.scope.adjustment);
        } else if (job.type === 'replace' && job.drawings) {
          const listed = await drawingsApi.replaceScope(
            job.scope.ticker,
            job.scope.range,
            job.drawings,
            job.scope.adjustment,
          );
          if (
            outbox.getScopeGeneration() === job.scopeGeneration
            && resolveListApply(true, true)
          ) {
            writeLocal(listed.drawings, false);
          }
        }
        outbox.complete(drawingId, job.generation);
        if (outbox.isEmpty()) {
          setSyncStatus('idle');
          setSyncHint(null);
        }
      } catch (error) {
        outbox.failKeep(drawingId);
        if (isConflictError(error)) {
          setSyncStatus('conflict');
          setSyncHint('conflict');
          try {
            const remote = await drawingsApi.list(job.scope.ticker, job.scope.range, job.scope.adjustment);
            if (outbox.getScopeGeneration() !== job.scopeGeneration) return;
            conflictServerRef.current = remote.drawings;
          } catch {
            conflictServerRef.current = conflictServerRef.current ?? null;
          }
          return;
        }
        setSyncStatus('unsynced');
        setSyncHint('unsynced');
        return;
      }
    }
  }, [applyJobResult, signedIn, writeLocal]);

  const enqueue = useCallback((job: Omit<PersistJob, 'generation' | 'scopeGeneration' | 'scope'>) => {
    if (!signedIn) return;
    const queued = outboxRef.current.enqueue(job);
    if (!queued) return;
    void drain(queued.drawingId);
  }, [drain, signedIn]);

  const loadScope = useCallback(async (generation: number) => {
    setSelectedId(null);
    setInProgress(null);
    dragPreviewRef.current = null;
    conflictServerRef.current = null;
    if (!signedIn) {
      const loaded = loadDrawings(storageKey);
      const list = loaded.ok ? loaded.drawings : [];
      writeLocal(list, false);
      setHistory(createHistory(list));
      setSyncStatus('guest');
      setSyncHint(loaded.ok ? null : 'local_corrupt');
      return;
    }
    const cached = loadDrawings(storageKey);
    try {
      const remote = await drawingsApi.list(args.ticker, args.range, adjustment);
      const outbox = outboxRef.current;
      if (outbox.getScopeGeneration() !== generation) return;
      if (!resolveListApply(outbox.isEmpty(), true)) return;
      writeLocal(remote.drawings, false);
      setHistory(createHistory(remote.drawings));
      setSyncStatus('idle');
      setSyncHint(null);
    } catch (error) {
      if (outboxRef.current.getScopeGeneration() !== generation) return;
      const list = cached.ok ? cached.drawings : [];
      writeLocal(list, false);
      setHistory(createHistory(list));
      if (isAuthError(error)) {
        setSyncStatus('guest');
        return;
      }
      setSyncStatus('unsynced');
      setSyncHint('unsynced');
    }
  }, [adjustment, args.range, args.ticker, signedIn, storageKey, writeLocal]);

  useEffect(() => {
    let cancelled = false;
    for (const timer of styleTimers.current.values()) clearTimeout(timer);
    styleTimers.current.clear();
    const generation = outboxRef.current.setScope(currentScope());
    const run = async () => {
      await Promise.resolve();
      if (cancelled) return;
      await loadScope(generation);
    };
    void run();
    return () => {
      cancelled = true;
    };
  }, [currentScope, loadScope]);

  useEffect(() => () => {
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    for (const timer of styleTimers.current.values()) clearTimeout(timer);
    styleTimers.current.clear();
    outboxRef.current.cancelAll();
  }, []);

  const persistOne = useCallback((drawing: ChartDrawing, mode: 'create' | 'update') => {
    enqueue({ drawingId: drawing.id, type: mode, drawing });
  }, [enqueue]);

  const scheduleUpdate = useCallback((drawing: ChartDrawing) => {
    if (!signedIn) return;
    const prev = styleTimers.current.get(drawing.id);
    if (prev) clearTimeout(prev);
    styleTimers.current.set(drawing.id, setTimeout(() => {
      styleTimers.current.delete(drawing.id);
      persistOne(drawing, 'update');
    }, 400));
  }, [persistOne, signedIn]);

  const setTool = useCallback((next: DrawingTool) => {
    setToolState(next);
    setInProgress(null);
    if (next !== 'select') {
      args.onCancelMeasure();
      setSelectedId(null);
    }
  }, [args]);

  const selected = drawings.find((item) => item.id === selectedId) ?? null;

  const pushDrawings = useCallback((next: ChartDrawing[]) => {
    commitLocal(next, true);
  }, [commitLocal]);

  const completeDrawing = useCallback((kind: DrawingKind, points: InProgressDraw['points']) => {
    if (kind === 'text') {
      setInProgress({ kind, points });
      setDraftText('');
      return;
    }
    const stamp = nowIso();
    const drawing: ChartDrawing = {
      schemaVersion: 1,
      id: newId(),
      ticker: args.ticker,
      range: args.range,
      adjustment,
      kind,
      anchors: points.map((point) => ({ time: point.time, barKey: point.barKey, price: point.price })),
      style: { color: '#2E46E0', width: 2, dash: 'solid', fillOpacity: kind === 'channel' || kind === 'rectangle' ? 0.12 : undefined },
      locked: false,
      hidden: false,
      zOrder: (drawingsRef.current[drawingsRef.current.length - 1]?.zOrder ?? 0) + 1,
      revision: 1,
      createdAt: stamp,
      updatedAt: stamp,
    };
    const next = [...drawingsRef.current, drawing];
    pushDrawings(next);
    setSelectedId(drawing.id);
    setInProgress(null);
    setToolState('select');
    persistOne(drawing, 'create');
  }, [adjustment, args.range, args.ticker, persistOne, pushDrawings]);

  const commitText = useCallback((text: string) => {
    const clean = whitelistText(text);
    if (!clean || !inProgress || inProgress.kind !== 'text') {
      setInProgress(null);
      setDraftText('');
      return;
    }
    const point = inProgress.points[0];
    const stamp = nowIso();
    const drawing: ChartDrawing = {
      schemaVersion: 1,
      id: newId(),
      ticker: args.ticker,
      range: args.range,
      adjustment,
      kind: 'text',
      anchors: [{ time: point.time, barKey: point.barKey, price: point.price }],
      style: { color: '#3D4A68', width: 1, dash: 'solid' },
      text: clean,
      locked: false,
      hidden: false,
      zOrder: (drawingsRef.current[drawingsRef.current.length - 1]?.zOrder ?? 0) + 1,
      revision: 1,
      createdAt: stamp,
      updatedAt: stamp,
    };
    const next = [...drawingsRef.current, drawing];
    pushDrawings(next);
    setSelectedId(drawing.id);
    setInProgress(null);
    setDraftText('');
    setToolState('select');
    persistOne(drawing, 'create');
  }, [adjustment, args.range, args.ticker, inProgress, persistOne, pushDrawings]);

  const deleteDrawing = useCallback((id: string) => {
    const timer = styleTimers.current.get(id);
    if (timer) {
      clearTimeout(timer);
      styleTimers.current.delete(id);
    }
    outboxRef.current.cancelId(id);
    const next = drawingsRef.current.filter((item) => item.id !== id);
    pushDrawings(next);
    if (selectedId === id) {
      setSelectedId(null);
      setFocusAnchor(null);
    }
    enqueue({ drawingId: id, type: 'delete' });
  }, [enqueue, pushDrawings, selectedId]);

  const deleteSelected = useCallback(() => {
    if (!selectedId) return;
    deleteDrawing(selectedId);
  }, [deleteDrawing, selectedId]);

  const clearAll = useCallback(() => {
    for (const timer of styleTimers.current.values()) clearTimeout(timer);
    styleTimers.current.clear();
    outboxRef.current.cancelAll();
    pushDrawings([]);
    setSelectedId(null);
    setFocusAnchor(null);
    enqueue({ drawingId: SCOPE_JOB_ID, type: 'clear' });
  }, [enqueue, pushDrawings]);

  const syncHistoryDiff = useCallback((prev: ChartDrawing[], next: ChartDrawing[]) => {
    if (!signedIn) return;
    for (const op of diffPersistOps(prev, next)) {
      if (op.type === 'delete') enqueue({ drawingId: op.id, type: 'delete' });
      else if (op.type === 'create') enqueue({ drawingId: op.drawing.id, type: 'create', drawing: op.drawing });
      else enqueue({ drawingId: op.drawing.id, type: 'update', drawing: op.drawing });
    }
  }, [enqueue, signedIn]);

  const undo = useCallback(() => {
    setHistory((prev) => {
      if (!canUndo(prev)) return prev;
      const next = historyUndo(prev);
      drawingsRef.current = next.present;
      setDrawings(next.present);
      saveDrawings(storageKey, next.present);
      syncHistoryDiff(prev.present, next.present);
      return next;
    });
  }, [storageKey, syncHistoryDiff]);

  const redo = useCallback(() => {
    setHistory((prev) => {
      if (!canRedo(prev)) return prev;
      const next = historyRedo(prev);
      drawingsRef.current = next.present;
      setDrawings(next.present);
      saveDrawings(storageKey, next.present);
      syncHistoryDiff(prev.present, next.present);
      return next;
    });
  }, [storageKey, syncHistoryDiff]);

  const patchDrawing = useCallback((id: string, patch: Partial<ChartDrawing>, persist: boolean) => {
    const next = drawingsRef.current.map((item) => {
      if (item.id !== id) return item;
      return { ...item, ...patch, updatedAt: nowIso() };
    });
    commitLocal(next, true);
    const updated = next.find((item) => item.id === id);
    if (persist && updated) scheduleUpdate(updated);
  }, [commitLocal, scheduleUpdate]);

  const patchSelected = useCallback((patch: Partial<ChartDrawing>, persist: boolean) => {
    if (!selectedId) return;
    patchDrawing(selectedId, patch, persist);
  }, [patchDrawing, selectedId]);

  const updateStyle = useCallback((style: DrawingStyle) => {
    const clean = whitelistStyle(style);
    if (!clean) return;
    patchSelected({ style: clean }, true);
  }, [patchSelected]);

  const visibleCtx: RenderContext | null = useMemo(() => {
    if (!args.bars?.length) return null;
    const prices = args.bars.flatMap((bar) => [bar.h, bar.l]);
    return {
      bars: args.bars,
      range: args.range,
      xMin: 0,
      xMax: args.bars.length - 1,
      yMin: Math.min(...prices),
      yMax: Math.max(...prices),
    };
  }, [args.bars, args.range]);

  const marks = useMemo(
    () => (visibleCtx
      ? drawingsToMarks(drawings, visibleCtx, { selectedId, inProgress })
      : { lines: [], areas: [], points: [], polygons: [], unresolvedIds: [] }),
    [drawings, inProgress, selectedId, visibleCtx],
  );

  const projected: ProjectedDrawing[] = useMemo(() => {
    if (!visibleCtx) return [];
    return drawings
      .map((drawing) => toProjectedDrawing(drawing, visibleCtx))
      .filter((item): item is ProjectedDrawing => item !== null);
  }, [drawings, visibleCtx]);

  const refreshGraphic = useCallback((chart: EChartsInstance, ctx: RenderContext | null) => {
    if (!chart || chart.isDisposed() || !ctx) return;
    const preview = dragPreviewRef.current;
    const selected = preview
      ?? drawingsRef.current.find((item) => item.id === selectedId && !item.hidden)
      ?? null;
    const selectedGeom = selected ? selectionOverlay(selected, ctx) : null;
    const draftGeom = inProgress && inProgress.points.length ? draftOverlay(inProgress, ctx) : null;
    const overlay: OverlayGeometry = {
      anchors: [...(selectedGeom?.anchors ?? []), ...(draftGeom?.anchors ?? [])],
      segments: [...(selectedGeom?.segments ?? []), ...(draftGeom?.segments ?? [])],
      fills: [...(selectedGeom?.fills ?? []), ...(draftGeom?.fills ?? [])],
    };
    const toPixel = (point: Point): Point | null => {
      const px = chart.convertToPixel({ gridIndex: 0 }, [point.x, point.y]) as number[] | null;
      if (!px || !Number.isFinite(px[0]) || !Number.isFinite(px[1])) return null;
      return { x: px[0], y: px[1] };
    };
    const color = preview ? resolvePaintColor(preview.style.color) : '#2E46E0';
    chart.setOption(
      { graphic: graphicFromOverlay(overlay, toPixel, color, { solid: Boolean(preview) }) },
      { lazyUpdate: true, replaceMerge: ['graphic'] },
    );
  }, [inProgress, selectedId]);

  useEffect(() => {
    const chart = args.chart;
    const bars = args.bars;
    if (!chart || chart.isDisposed() || !bars?.length) return;
    const zr = chart.getZr();

    const readPoint = (offsetX: number, offsetY: number, alt: boolean, shift: boolean) => {
      const inGrid = chart.containPixel({ gridIndex: 0 }, [offsetX, offsetY]);
      if (!inGrid) return null;
      const converted = chart.convertFromPixel({ gridIndex: 0 }, [offsetX, offsetY]) as number[] | null;
      if (!converted || !Number.isFinite(converted[0]) || !Number.isFinite(converted[1])) return null;
      const barIndex = snapBarIndex(converted[0], bars.length);
      if (barIndex === null) return null;
      const bar = bars[barIndex];
      const pixelOf = (price: number) => {
        const px = chart.convertToPixel({ gridIndex: 0 }, [barIndex, price]) as number[];
        return px?.[1] ?? 0;
      };
      const candidates = [
        ...ohlcCandidates(bar),
        ...(args.ma20?.[barIndex] != null ? [{ price: args.ma20[barIndex] as number, kind: 'ma20' as const }] : []),
        ...(args.swingPrices ?? []).map((price) => ({ price, kind: 'swing' as const })),
        ...(args.levelPrices ?? []).map((price) => ({ price, kind: 'level' as const })),
        ...drawingsRef.current.flatMap((item) => item.anchors.map((anchor) => ({ price: anchor.price, kind: 'anchor' as const }))),
      ];
      const snapped = snapPointer({
        x: converted[0],
        y: offsetY,
        barCount: bars.length,
        pointerPrice: converted[1],
        candidates,
        priceToY: pixelOf,
        thresholdPx: 10,
        alt,
      });
      const price = snapped.price;
      let next = {
        barIndex: snapped.barIndex ?? barIndex,
        price,
        time: bar.t,
        barKey: barKeyOf(bar, args.range),
      };
      if (shift && inProgress?.points.length) {
        const last = inProgress.points[inProgress.points.length - 1];
        const originPx = chart.convertToPixel({ gridIndex: 0 }, [last.barIndex, last.price]) as number[] | null;
        if (originPx && Number.isFinite(originPx[0]) && Number.isFinite(originPx[1])) {
          const shifted = applyPixelShiftConstraint({
            originPx: { x: originPx[0], y: originPx[1] },
            pointerPx: { x: offsetX, y: offsetY },
            fromPixel: (x, y) => {
              const px = chart.convertFromPixel({ gridIndex: 0 }, [x, y]) as number[] | null;
              if (!px || !Number.isFinite(px[0]) || !Number.isFinite(px[1])) return null;
              const idx = snapBarIndex(px[0], bars.length);
              if (idx === null) return null;
              return { barIndex: idx, price: px[1] };
            },
            bars,
            range: args.range,
          });
          if (shifted) next = shifted;
        }
      }
      return next;
    };

    const onDown = (event: { offsetX: number; offsetY: number; pointerType?: string; event?: { altKey?: boolean; shiftKey?: boolean; button?: number } }) => {
      if (args.measureActive) return;
      const alt = Boolean(event.event?.altKey);
      const shift = Boolean(event.event?.shiftKey);
      const point = readPoint(event.offsetX, event.offsetY, alt, shift);
      if (tool !== 'select') {
        if (!point) return;
        if (tool === 'horizontal') {
          const result = addDraftPoint(null, 'horizontal', point);
          if (result.status === 'complete') completeDrawing('horizontal', result.points);
        } else {
          const result = addDraftPoint(inProgress, tool as DrawingKind, point);
          if (result.status === 'complete') completeDrawing(tool as DrawingKind, result.points);
          else setInProgress(result.draft);
        }
        return;
      }
      const prices = bars.flatMap((bar) => [bar.h, bar.l]);
      const ctx: RenderContext = {
        bars,
        range: args.range,
        xMin: 0,
        xMax: bars.length - 1,
        yMin: Math.min(...prices),
        yMax: Math.max(...prices),
      };
      const toPixel = (pt: Point): Point | null => {
        const px = chart.convertToPixel({ gridIndex: 0 }, [pt.x, pt.y]) as number[] | null;
        if (!px || !Number.isFinite(px[0]) || !Number.isFinite(px[1])) return null;
        return { x: px[0], y: px[1] };
      };
      const hits = drawingsRef.current
        .map((drawing) => toProjectedDrawing(drawing, ctx))
        .filter((item): item is ProjectedDrawing => item !== null)
        .map((item) => projectToPixels(item, toPixel));
      const hit = hitTestDrawings(
        hits,
        { x: event.offsetX, y: event.offsetY },
        event.pointerType === 'touch' ? 'touch' : 'mouse',
        selectedId,
      );
      if (!hit) {
        setSelectedId(null);
        setFocusAnchor(null);
        return;
      }
      setSelectedId(hit.id);
      setFocusAnchor(hit.kind === 'anchor' ? hit.anchorIndex : null);
      const target = drawingsRef.current.find((item) => item.id === hit.id);
      if (!target || target.locked) return;
      const startConverted = chart.convertFromPixel({ gridIndex: 0 }, [event.offsetX, event.offsetY]) as number[] | null;
      const startIdx = startConverted ? snapBarIndex(startConverted[0], bars.length) : null;
      dragRef.current = {
        id: hit.id,
        mode: hit.kind === 'anchor' ? 'anchor' : 'whole',
        anchorIndex: hit.anchorIndex,
        origin: JSON.parse(JSON.stringify(target)) as ChartDrawing,
        startPixel: { x: event.offsetX, y: event.offsetY },
        startData: {
          barIndex: startIdx ?? 0,
          price: startConverted?.[1] ?? target.anchors[0]?.price ?? 0,
        },
      };
      dragPreviewRef.current = target;
    };

    const onMove = (event: { offsetX: number; offsetY: number }) => {
      const drag = dragRef.current;
      if (!drag) return;
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = 0;
        if (!dragRef.current) return;
        const converted = chart.convertFromPixel({ gridIndex: 0 }, [event.offsetX, event.offsetY]) as number[] | null;
        if (!converted) return;
        const idx = snapBarIndex(converted[0], bars.length);
        if (idx === null) return;
        const snapshot = drawingsRef.current;
        const moved = dragMove({
          drawings: snapshot,
          drag,
          pointer: { barIndex: idx, price: converted[1] },
          bars,
          range: args.range,
        });
        if (moved.drawings !== snapshot) return;
        dragPreviewRef.current = moved.preview;
        const prices = bars.flatMap((bar) => [bar.h, bar.l]);
        refreshGraphic(chart, {
          bars,
          range: args.range,
          xMin: 0,
          xMax: bars.length - 1,
          yMin: Math.min(...prices),
          yMax: Math.max(...prices),
        });
      });
    };

    const onUp = () => {
      const drag = dragRef.current;
      const preview = dragPreviewRef.current;
      dragRef.current = null;
      dragPreviewRef.current = null;
      if (!drag || !preview) return;
      const next = drawingsRef.current.map((item) => (
        item.id === preview.id ? { ...preview, updatedAt: nowIso() } : item
      ));
      pushDrawings(next);
      const updated = next.find((item) => item.id === preview.id);
      if (updated) persistOne(updated, 'update');
    };

    zr.on('mousedown', onDown);
    zr.on('mousemove', onMove);
    zr.on('mouseup', onUp);
    zr.on('globalout', onUp);
    return () => {
      if (chart.isDisposed()) return;
      zr.off('mousedown', onDown);
      zr.off('mousemove', onMove);
      zr.off('mouseup', onUp);
      zr.off('globalout', onUp);
    };
  }, [args.bars, args.chart, args.levelPrices, args.ma20, args.measureActive, args.range, args.swingPrices, completeDrawing, inProgress, persistOne, pushDrawings, refreshGraphic, selectedId, tool]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (isTextInputTarget(event.target)) return;
      if (event.key === 'Escape') {
        if (inProgress) {
          setInProgress(null);
          event.preventDefault();
          return;
        }
        if (tool !== 'select') {
          setToolState('select');
          event.preventDefault();
          return;
        }
        if (expanded) {
          setExpanded(false);
          event.preventDefault();
          return;
        }
        if (selectedId) {
          setSelectedId(null);
          event.preventDefault();
        }
        return;
      }
      if ((event.key === 'Delete' || event.key === 'Backspace') && selectedId) {
        event.preventDefault();
        deleteSelected();
        return;
      }
      if (
        selectedId
        && (event.key === 'ArrowUp' || event.key === 'ArrowDown' || event.key === 'ArrowLeft' || event.key === 'ArrowRight')
      ) {
        event.preventDefault();
        const target = drawingsRef.current.find((item) => item.id === selectedId);
        if (!target || target.locked || !args.bars?.length) return;
        const nextAnchors = nudgeAnchors(
          target.anchors,
          event.key,
          event.shiftKey,
          args.bars,
          args.range,
          focusAnchor,
        );
        patchDrawing(selectedId, { anchors: nextAnchors }, true);
      }
      const meta = event.metaKey || event.ctrlKey;
      if (meta && event.key.toLowerCase() === 'z') {
        event.preventDefault();
        if (event.shiftKey) redo();
        else undo();
        return;
      }
      if (meta && event.key.toLowerCase() === 'y') {
        event.preventDefault();
        redo();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [args.bars, args.range, deleteSelected, expanded, focusAnchor, inProgress, patchDrawing, redo, selectedId, tool, undo]);

  useEffect(() => {
    const chart = args.chart;
    if (!chart || chart.isDisposed() || !visibleCtx) return;
    const refresh = () => refreshGraphic(chart, visibleCtx);
    refresh();
    chart.on('datazoom', refresh);
    const dom = chart.getDom();
    const ro = typeof ResizeObserver !== 'undefined' && dom
      ? new ResizeObserver(() => refresh())
      : null;
    if (dom && ro) ro.observe(dom);
    return () => {
      if (chart.isDisposed()) return;
      chart.off('datazoom', refresh);
      ro?.disconnect();
    };
  }, [args.chart, drawings, inProgress, refreshGraphic, selectedId, visibleCtx]);

  const retry = useCallback(() => {
    if (!signedIn) return;
    const outbox = outboxRef.current;
    if (syncStatus === 'conflict') return;
    if (resolveRetryAction(outbox.isEmpty()) === 'replay') {
      outbox.restoreForRetry(outbox.snapshot());
      for (const id of outbox.readyIds()) void drain(id);
    }
  }, [drain, signedIn, syncStatus]);

  const keepLocalConflict = useCallback(async () => {
    if (!signedIn) return;
    const outbox = outboxRef.current;
    let server = conflictServerRef.current;
    if (!server) {
      const generation = outbox.getScopeGeneration();
      try {
        const remote = await drawingsApi.list(args.ticker, args.range, adjustment);
        if (outbox.getScopeGeneration() !== generation) return;
        server = remote.drawings;
        conflictServerRef.current = server;
      } catch {
        setSyncHint('conflict');
        return;
      }
    }
    const next = keepLocalWithServerRevisions(drawingsRef.current, server);
    writeLocal(next, false);
    outbox.stampRevisions(server);
    setSyncStatus('saving');
    setSyncHint(null);
    for (const id of outbox.readyIds()) void drain(id);
  }, [adjustment, args.range, args.ticker, drain, signedIn, writeLocal]);

  const takeServerConflict = useCallback(async () => {
    if (!signedIn) return;
    const outbox = outboxRef.current;
    let server = conflictServerRef.current;
    if (!server) {
      try {
        const remote = await drawingsApi.list(args.ticker, args.range, adjustment);
        server = remote.drawings;
      } catch {
        setSyncHint('conflict');
        return;
      }
    }
    outbox.cancelAll();
    conflictServerRef.current = null;
    writeLocal(server, false);
    setHistory(createHistory(server));
    setSyncStatus('idle');
    setSyncHint(null);
  }, [adjustment, args.range, args.ticker, signedIn, writeLocal]);

  const importJson = useCallback((raw: unknown) => {
    const parsed = validateImport(raw);
    if (!parsed.ok) {
      setImportError(parsed.error);
      return parsed.error;
    }
    const incoming = parsed.value.map((item) => ({
      ...item,
      ticker: args.ticker,
      range: args.range,
      adjustment,
      revision: 1,
      id: item.id || newId(),
    }));
    setImportError(null);
    pushDrawings(incoming);
    if (signedIn) {
      enqueue({ drawingId: SCOPE_JOB_ID, type: 'replace', drawings: incoming });
    }
    return null;
  }, [adjustment, args.range, args.ticker, enqueue, pushDrawings, signedIn]);

  const importFromText = useCallback((text: string) => {
    try {
      return importJson(JSON.parse(text) as unknown);
    } catch {
      setImportError('invalid_json');
      return 'invalid_json';
    }
  }, [importJson]);

  const importAnonymous = useCallback(() => {
    const loaded = loadDrawings(anonymousStorageKey(args.ticker, args.range, adjustment));
    if (!loaded.ok) {
      setImportError(loaded.error);
      return loaded.error;
    }
    return importJson({ schemaVersion: 1, drawings: loaded.drawings });
  }, [adjustment, args.range, args.ticker, importJson]);

  return {
    tool,
    setTool,
    drawings,
    selected,
    selectedId,
    setSelectedId,
    inProgress,
    draftText,
    setDraftText,
    commitText,
    syncStatus,
    syncHint,
    importError,
    retry,
    keepLocalConflict,
    takeServerConflict,
    autoPatternsEnabled,
    setAutoPatternsEnabled,
    expanded,
    setExpanded,
    marks,
    unresolvedIds: marks.unresolvedIds,
    canUndo: canUndo(history),
    canRedo: canRedo(history),
    undo,
    redo,
    deleteSelected,
    deleteDrawing,
    clearAll,
    patchSelected,
    patchDrawing,
    updateStyle,
    exportJson: () => exportDrawings(drawings),
    importJson,
    importFromText,
    importAnonymous,
    projected,
  };
}

export type DrawingController = ReturnType<typeof useDrawingController>;
export { parseDrawing } from './schema.ts';
