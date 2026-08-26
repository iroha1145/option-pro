import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { EChartsInstance } from '@/lib/chart';
import { barKeyOf, resolveAnchor, snapBarIndex } from './projection.ts';
import { drawingsApi, isAuthError, isConflictError } from './api.ts';
import { drawingsToMarks, type BarLike, type RenderContext } from './renderer.ts';
import { loadDrawings, saveDrawings, anonymousStorageKey, drawingsStorageKey } from './storage.ts';
import { parseDrawing, exportDrawings, validateImport, whitelistStyle, whitelistText } from './schema.ts';
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
import { addDraftPoint, applyShiftToDraft, isTextInputTarget, type DrawingTool, type InProgressDraw } from './tools.ts';
import { moveChannelAnchor, moveChannelWhole, constrainByShift } from './geometry.ts';
import type { ChartAdjustment, ChartDrawing, ChartRange, DrawingKind, DrawingStyle, Point } from './types.ts';

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
  const [autoPatternsEnabled, setAutoPatternsEnabled] = useState(true);
  const [expanded, setExpanded] = useState(false);
  const [draftText, setDraftText] = useState('');
  const dragRef = useRef<{
    id: string;
    mode: 'anchor' | 'whole';
    anchorIndex: number;
    last: Point;
    origin: ChartDrawing;
  } | null>(null);
  const rafRef = useRef(0);
  const styleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const drawingsRef = useRef(drawings);
  const signedIn = args.identity.signedIn;

  useEffect(() => {
    drawingsRef.current = drawings;
  }, [drawings]);

  const storageKey = signedIn
    ? drawingsStorageKey(args.identity.key, args.ticker, args.range, adjustment)
    : anonymousStorageKey(args.ticker, args.range, adjustment);

  const commitLocal = useCallback((next: ChartDrawing[], recordHistory: boolean) => {
    setDrawings(next);
    setHistory((prev) => (recordHistory ? historyPush(prev, next) : historyReplace(prev, next)));
    saveDrawings(storageKey, next);
  }, [storageKey]);

  const loadScope = useCallback(async () => {
    setSelectedId(null);
    setInProgress(null);
    if (!signedIn) {
      const loaded = loadDrawings(storageKey);
      const list = loaded.ok ? loaded.drawings : [];
      setDrawings(list);
      setHistory(createHistory(list));
      setSyncStatus('guest');
      setSyncHint(loaded.ok ? null : 'local_corrupt');
      return;
    }
    const cached = loadDrawings(storageKey);
    try {
      const remote = await drawingsApi.list(args.ticker, args.range, adjustment);
      setDrawings(remote.drawings);
      setHistory(createHistory(remote.drawings));
      saveDrawings(storageKey, remote.drawings);
      setSyncStatus('idle');
      setSyncHint(null);
    } catch (error) {
      if (isAuthError(error)) {
        const list = cached.ok ? cached.drawings : [];
        setDrawings(list);
        setHistory(createHistory(list));
        setSyncStatus('guest');
        return;
      }
      const list = cached.ok ? cached.drawings : [];
      setDrawings(list);
      setHistory(createHistory(list));
      setSyncStatus('unsynced');
      setSyncHint('unsynced');
    }
  }, [adjustment, args.range, args.ticker, signedIn, storageKey]);

  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      await Promise.resolve();
      if (cancelled) return;
      await loadScope();
    };
    void run();
    return () => {
      cancelled = true;
    };
  }, [loadScope]);

  useEffect(() => () => {
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    if (styleTimer.current) clearTimeout(styleTimer.current);
  }, []);

  const persistOne = useCallback(async (drawing: ChartDrawing, mode: 'create' | 'update') => {
    if (!signedIn) return;
    setSyncStatus('saving');
    try {
      const saved = mode === 'create'
        ? await drawingsApi.create(drawing)
        : await drawingsApi.update(drawing);
      if (saved) {
        setDrawings((prev) => {
          const next = prev.map((item) => (item.id === drawing.id ? saved : item));
          saveDrawings(storageKey, next);
          setHistory((hist) => historyReplace(hist, next));
          return next;
        });
      }
      setSyncStatus('idle');
      setSyncHint(null);
    } catch (error) {
      if (isConflictError(error)) {
        setSyncStatus('conflict');
        setSyncHint('conflict');
        void loadScope();
        return;
      }
      setSyncStatus('unsynced');
      setSyncHint('unsynced');
    }
  }, [loadScope, signedIn, storageKey]);

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
    void persistOne(drawing, 'create');
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
    void persistOne(drawing, 'create');
  }, [adjustment, args.range, args.ticker, inProgress, persistOne, pushDrawings]);

  const deleteSelected = useCallback(() => {
    if (!selectedId) return;
    const target = drawingsRef.current.find((item) => item.id === selectedId);
    const next = drawingsRef.current.filter((item) => item.id !== selectedId);
    pushDrawings(next);
    setSelectedId(null);
    if (signedIn && target) {
      void drawingsApi.remove(target.id).catch(() => setSyncStatus('unsynced'));
    }
  }, [pushDrawings, selectedId, signedIn]);

  const clearAll = useCallback(() => {
    pushDrawings([]);
    setSelectedId(null);
    if (signedIn) {
      void drawingsApi.clearScope(args.ticker, args.range, adjustment).catch(() => setSyncStatus('unsynced'));
    }
  }, [adjustment, args.range, args.ticker, pushDrawings, signedIn]);

  const undo = useCallback(() => {
    setHistory((prev) => {
      if (!canUndo(prev)) return prev;
      const next = historyUndo(prev);
      setDrawings(next.present);
      saveDrawings(storageKey, next.present);
      return next;
    });
  }, [storageKey]);

  const redo = useCallback(() => {
    setHistory((prev) => {
      if (!canRedo(prev)) return prev;
      const next = historyRedo(prev);
      setDrawings(next.present);
      saveDrawings(storageKey, next.present);
      return next;
    });
  }, [storageKey]);

  const patchSelected = useCallback((patch: Partial<ChartDrawing>, persist: boolean) => {
    if (!selectedId) return;
    const next = drawingsRef.current.map((item) => {
      if (item.id !== selectedId) return item;
      return { ...item, ...patch, updatedAt: nowIso() };
    });
    commitLocal(next, true);
    const updated = next.find((item) => item.id === selectedId);
    if (persist && updated && signedIn) {
      if (styleTimer.current) clearTimeout(styleTimer.current);
      styleTimer.current = setTimeout(() => void persistOne(updated, 'update'), 400);
    }
  }, [commitLocal, persistOne, selectedId, signedIn]);

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
    () => (visibleCtx ? drawingsToMarks(drawings, visibleCtx) : { lines: [], areas: [], points: [], unresolvedIds: [] }),
    [drawings, visibleCtx],
  );

  const projected: ProjectedDrawing[] = useMemo(() => {
    if (!visibleCtx) return [];
    return drawings.map((drawing) => {
      const geom = drawingsToMarks([drawing], visibleCtx);
      const anchors = drawing.anchors.map((anchor) => {
        const index = resolveAnchor(visibleCtx.bars, anchor, visibleCtx.range);
        return { x: index, y: anchor.price };
      }).filter((point) => point.x >= 0);
      return {
        id: drawing.id,
        zOrder: drawing.zOrder,
        locked: drawing.locked,
        hidden: drawing.hidden,
        anchors,
        segments: [],
        fills: [],
        _geom: geom,
      } as ProjectedDrawing;
    });
  }, [drawings, visibleCtx]);

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
        next = applyShiftToDraft(inProgress.kind, inProgress.points, next, true);
        const constrained = constrainByShift(
          { x: inProgress.points[inProgress.points.length - 1].barIndex, y: inProgress.points[inProgress.points.length - 1].price },
          { x: next.barIndex, y: next.price },
        );
        next = { ...next, barIndex: constrained.x, price: constrained.y };
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
        if (tool === 'horizontal' && point) {
          const result = addDraftPoint(null, 'horizontal', point);
          if (result.status === 'complete') completeDrawing('horizontal', result.points);
        } else {
          const result = addDraftPoint(inProgress, tool as DrawingKind, point);
          if (result.status === 'complete') completeDrawing(tool as DrawingKind, result.points);
          else setInProgress(result.draft);
        }
        return;
      }
      // select tool: hit-test in pixel space
      const hits = drawingsRef.current.map((drawing) => {
        const anchors = drawing.anchors.map((anchor) => {
          const index = resolveAnchor(bars, anchor, args.range);
          if (index < 0) return { x: -1e6, y: -1e6 };
          const px = chart.convertToPixel({ gridIndex: 0 }, [index, anchor.price]) as number[];
          return { x: px?.[0] ?? -1e6, y: px?.[1] ?? -1e6 };
        });
        const segments = [] as { a: Point; b: Point }[];
        if (anchors.length >= 2 && anchors.every((item) => item.x > -1e5)) {
          segments.push({ a: anchors[0], b: anchors[1] });
        }
        if (drawing.kind === 'horizontal' && anchors[0] && anchors[0].x > -1e5) {
          const left = chart.convertToPixel({ gridIndex: 0 }, [0, drawing.anchors[0].price]) as number[];
          const right = chart.convertToPixel({ gridIndex: 0 }, [bars.length - 1, drawing.anchors[0].price]) as number[];
          segments.push({ a: { x: left[0], y: left[1] }, b: { x: right[0], y: right[1] } });
        }
        return {
          id: drawing.id,
          zOrder: drawing.zOrder,
          locked: drawing.locked,
          hidden: drawing.hidden,
          anchors,
          segments,
          fills: [],
        } satisfies ProjectedDrawing;
      });
      const hit = hitTestDrawings(
        hits,
        { x: event.offsetX, y: event.offsetY },
        event.pointerType === 'touch' ? 'touch' : 'mouse',
        selectedId,
      );
      if (!hit) {
        setSelectedId(null);
        return;
      }
      setSelectedId(hit.id);
      const target = drawingsRef.current.find((item) => item.id === hit.id);
      if (!target || target.locked) return;
      dragRef.current = {
        id: hit.id,
        mode: hit.kind === 'anchor' ? 'anchor' : 'whole',
        anchorIndex: hit.anchorIndex,
        last: { x: event.offsetX, y: event.offsetY },
        origin: JSON.parse(JSON.stringify(target)) as ChartDrawing,
      };
    };

    const onMove = (event: { offsetX: number; offsetY: number; event?: { altKey?: boolean } }) => {
      const drag = dragRef.current;
      if (!drag) return;
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = 0;
        const converted = chart.convertFromPixel({ gridIndex: 0 }, [event.offsetX, event.offsetY]) as number[] | null;
        if (!converted) return;
        const current = drawingsRef.current.find((item) => item.id === drag.id);
        if (!current) return;
        const origin = drag.origin;
        if (drag.mode === 'whole') {
          const start = chart.convertFromPixel({ gridIndex: 0 }, [drag.last.x, drag.last.y]) as number[] | null;
          if (!start) return;
          const dIndex = converted[0] - start[0];
          const dPrice = converted[1] - start[1];
          if (current.kind === 'channel' && origin.anchors.length === 3) {
            const moved = moveChannelWhole(
              { x: 0, y: origin.anchors[0].price },
              { x: 1, y: origin.anchors[1].price },
              { x: 2, y: origin.anchors[2].price },
              0,
              dPrice,
            );
            // shift bar keys by rounded dIndex via re-resolving
            const nextAnchors = origin.anchors.map((anchor, index) => {
              const idx = resolveAnchor(bars, anchor, args.range);
              const nextIdx = Math.max(0, Math.min(bars.length - 1, Math.round(idx + dIndex)));
              const bar = bars[nextIdx];
              return {
                time: bar.t,
                barKey: barKeyOf(bar, args.range),
                price: moved[index].y,
              };
            });
            setDrawings((prev) => prev.map((item) => item.id === current.id ? { ...item, anchors: nextAnchors } : item));
            return;
          }
          const nextAnchors = origin.anchors.map((anchor) => {
            const idx = resolveAnchor(bars, anchor, args.range);
            const nextIdx = Math.max(0, Math.min(bars.length - 1, Math.round(idx + dIndex)));
            const bar = bars[nextIdx];
            return { time: bar.t, barKey: barKeyOf(bar, args.range), price: anchor.price + dPrice };
          });
          setDrawings((prev) => prev.map((item) => item.id === current.id ? { ...item, anchors: nextAnchors } : item));
          return;
        }
        const idx = snapBarIndex(converted[0], bars.length);
        if (idx === null) return;
        const bar = bars[idx];
        const nextAnchor = { time: bar.t, barKey: barKeyOf(bar, args.range), price: converted[1] };
        if (current.kind === 'channel' && origin.anchors.length === 3) {
          const pts = origin.anchors.map((anchor) => {
            const i = resolveAnchor(bars, anchor, args.range);
            return { x: i, y: anchor.price };
          });
          const moved = moveChannelAnchor(pts[0], pts[1], pts[2], drag.anchorIndex as 0 | 1 | 2, { x: idx, y: converted[1] });
          const nextAnchors = moved.map((point) => {
            const barAt = bars[Math.max(0, Math.min(bars.length - 1, Math.round(point.x)))];
            return { time: barAt.t, barKey: barKeyOf(barAt, args.range), price: point.y };
          });
          setDrawings((prev) => prev.map((item) => item.id === current.id ? { ...item, anchors: nextAnchors } : item));
          return;
        }
        setDrawings((prev) => prev.map((item) => {
          if (item.id !== current.id) return item;
          const anchors = item.anchors.map((anchor, index) => index === drag.anchorIndex ? nextAnchor : anchor);
          return { ...item, anchors };
        }));
      });
    };

    const onUp = () => {
      const drag = dragRef.current;
      dragRef.current = null;
      if (!drag) return;
      const current = drawingsRef.current.find((item) => item.id === drag.id);
      if (!current) return;
      pushDrawings(drawingsRef.current);
      void persistOne(current, 'update');
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
  }, [args.bars, args.chart, args.levelPrices, args.ma20, args.measureActive, args.range, args.swingPrices, completeDrawing, inProgress, persistOne, pushDrawings, selectedId, tool]);

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
  }, [deleteSelected, expanded, inProgress, redo, selectedId, tool, undo]);

  const importJson = useCallback((raw: unknown) => {
    const parsed = validateImport(raw);
    if (!parsed.ok) return parsed.error;
    const incoming = parsed.value.map((item) => ({
      ...item,
      ticker: args.ticker,
      range: args.range,
      adjustment,
      revision: 1,
      id: item.id || newId(),
    }));
    pushDrawings(incoming);
    incoming.forEach((item) => void persistOne(item, 'create'));
    return null;
  }, [adjustment, args.range, args.ticker, persistOne, pushDrawings]);

  const importAnonymous = useCallback(() => {
    const loaded = loadDrawings(anonymousStorageKey(args.ticker, args.range, adjustment));
    if (!loaded.ok) return loaded.error;
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
    retry: loadScope,
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
    clearAll,
    patchSelected,
    updateStyle,
    exportJson: () => exportDrawings(drawings),
    importJson,
    importAnonymous,
    projected,
  };
}

export type DrawingController = ReturnType<typeof useDrawingController>;
export { parseDrawing };
