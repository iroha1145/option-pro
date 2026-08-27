import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { EChartsInstance } from '@/lib/chart';
import { barKeyOf, nudgeAnchors, snapBarIndex } from './projection.ts';
import { drawingErrorCode, drawingErrorStatus, drawingsApi } from './api.ts';
import { drainPersistJob } from './drain.ts';
import {
  applyConflictDecision,
  completeScopeLoad,
  previewScopeLoad,
} from './scopeLoad.ts';
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
import {
  loadDrawings,
  quarantineDrawings,
  saveDrawings,
  anonymousStorageKey,
  drawingsStorageKey,
} from './storage.ts';
import { exportDrawings, parseDrawing, validateImport, whitelistStyle, whitelistText } from './schema.ts';
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
import { hitTestDrawings, isLockedDragBlocked, type PointerKind, type ProjectedDrawing } from './hitTest.ts';
import { ohlcCandidates, snapPointer, type SnapCandidate } from './snap.ts';
import { quotaRollbackDrawings, type ServerSnapshot } from './merge.ts';
import {
  addDraftPoint,
  escapeHandledByOverlay,
  isTextInputTarget,
  pointerKindFromEvent,
  type DrawingTool,
  type InProgressDraw,
} from './tools.ts';
import type { ChartAdjustment, ChartDrawing, ChartRange, DrawingKind, DrawingStyle, Point } from './types.ts';
import {
  DrawingOutbox,
  SCOPE_JOB_ID,
  applyKnownRevisions,
  applyPersistResponse,
  diffPersistOps,
  mutableFieldsDiffer,
  patchRevision,
  replaceDrawing,
  resolveRetryAction,
  type PersistJob,
  type ScopeKey,
  nextDrainRetryDelayMs,
} from './sync.ts';
import { clampDragPoint, dragExceedsThreshold, applyPixelShiftConstraint, dragMove, type DragOrigin } from './drag.ts';
import { resolvePaintColor } from './schema.ts';

export type SyncStatus = 'guest' | 'idle' | 'saving' | 'unsynced' | 'load_failed' | 'write_failed' | 'conflict';

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

/** 同一图形在这个窗口内的连续编辑合成一条历史记录（略大于 400ms 的写防抖）。 */
const COALESCE_WINDOW_MS = 700;

/** Escape 让路时要数的覆盖层（工作区自身也是其中之一）。 */
const MODAL_SELECTOR = '[role="dialog"],[role="alertdialog"],[aria-modal="true"]';

function openModalCount(): number {
  if (typeof document === 'undefined') return 0;
  return document.querySelectorAll(MODAL_SELECTOR).length;
}

/** 拖动期间关掉 inside dataZoom 的漫游；图上没有 dataZoom 就什么都别加。 */
function setChartRoam(chart: EChartsInstance, enabled: boolean): void {
  if (chart.isDisposed()) return;
  const option = chart.getOption() as { dataZoom?: unknown[] } | null | undefined;
  const zooms = Array.isArray(option?.dataZoom) ? option.dataZoom : [];
  if (!zooms.length) return;
  chart.setOption(
    { dataZoom: zooms.map(() => ({ moveOnMouseMove: enabled })) } as Parameters<EChartsInstance['setOption']>[0],
    { lazyUpdate: true },
  );
}

export function useDrawingController(args: {
  ticker: string;
  range: ChartRange;
  adjustment?: ChartAdjustment;
  bars: BarLike[] | undefined;
  ma20?: (number | null)[];
  snapCandidates?: SnapCandidate[];
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
  const [rejectedImport, setRejectedImport] = useState<string | null>(null);
  const [autoPatternsEnabled, setAutoPatternsEnabled] = useState(true);
  const [expanded, setExpanded] = useState(false);
  const [draftText, setDraftText] = useState('');
  const [focusAnchor, setFocusAnchor] = useState<number | null>(null);
  const dragRef = useRef<DragOrigin | null>(null);
  const dragPreviewRef = useRef<ChartDrawing | null>(null);
  const pointerKindRef = useRef<PointerKind>('mouse');
  const rafRef = useRef(0);
  const styleTimers = useRef(new Map<string, ReturnType<typeof setTimeout>>());
  /** 防抖窗口里还没入队的编辑：服务器回声不许盖掉它们。 */
  const pendingEdits = useRef(new Map<string, ChartDrawing>());
  /* 429/断网后的自动重放：定时器 + 第几次尝试。成功、人工重试、切 scope 都清零。 */
  const autoRetryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const autoRetryAttempt = useRef(0);
  /** 服务器确认过的 revision；历史快照里的旧号一律不发。 */
  const revisionsRef = useRef(new Map<string, number>());
  const localSaveRef = useRef<{ key: string; list: ChartDrawing[]; timer: ReturnType<typeof setTimeout> } | null>(null);
  const coalesceRef = useRef<{ id: string; at: number } | null>(null);
  const drawingsRef = useRef(drawings);
  const outboxRef = useRef(new DrawingOutbox());
  const lastServerRef = useRef<ServerSnapshot | null>(null);
  const conflictServerRef = useRef<{
    scope: ScopeKey;
    scopeGeneration: number;
    scopeRevision: number;
    drawings: ChartDrawing[];
  } | null>(null);
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

  /** 落盘待写的稿子（切 scope / 卸载前必须调用，键随 scope 变）。 */
  const flushLocalSave = useCallback(() => {
    const pending = localSaveRef.current;
    if (!pending) return;
    clearTimeout(pending.timer);
    localSaveRef.current = null;
    saveDrawings(pending.key, pending.list);
  }, []);

  const writeLocal = useCallback((
    next: ChartDrawing[],
    recordHistory: boolean,
    options?: { persist?: 'now' | 'debounced' | 'skip' },
  ) => {
    drawingsRef.current = next;
    setDrawings(next);
    setHistory((prev) => (recordHistory ? historyPush(prev, next) : historyReplace(prev, next)));
    const mode = options?.persist ?? 'now';
    const pending = localSaveRef.current;
    if (pending && (mode !== 'debounced' || pending.key !== storageKey)) {
      clearTimeout(pending.timer);
      localSaveRef.current = null;
      // 只有「同一个键马上要写更新的一份」才可以丢掉待写稿子，其余一律落盘。
      if (!(mode === 'now' && pending.key === storageKey)) saveDrawings(pending.key, pending.list);
    }
    if (mode === 'now') saveDrawings(storageKey, next);
    else if (mode === 'debounced') {
      // 逐字编辑不该每一键都把整张表 stringify 进 localStorage。
      const timer = setTimeout(() => {
        localSaveRef.current = null;
        saveDrawings(storageKey, next);
      }, 250);
      localSaveRef.current = { key: storageKey, list: next, timer };
    }
  }, [storageKey]);

  const commitLocal = useCallback((
    next: ChartDrawing[],
    recordHistory: boolean,
    options?: { persist?: 'now' | 'debounced' | 'skip' },
  ) => {
    writeLocal(next, recordHistory, options);
  }, [writeLocal]);

  const applyJobResult = useCallback((job: PersistJob, saved: ChartDrawing | null) => {
    const outbox = outboxRef.current;
    const action = applyPersistResponse({
      job,
      currentScope: outbox.getScope(),
      currentScopeGeneration: outbox.getScopeGeneration(),
      latestGenerationForId: outbox.latestGeneration(job.drawingId),
      responseDrawing: saved,
      // 防抖窗口里的编辑还没入队，任何 generation 都反映不了它：回声只能吃 revision。
      localDirty: pendingEdits.current.has(job.drawingId),
    });
    if (action.action === 'ignore') return;
    if (action.action === 'replace') {
      revisionsRef.current.set(action.drawing.id, action.drawing.revision);
      writeLocal(replaceDrawing(drawingsRef.current, action.drawing), false);
      return;
    }
    revisionsRef.current.set(action.id, action.revision);
    writeLocal(patchRevision(drawingsRef.current, action.id, action.revision), false);
  }, [writeLocal]);

  const drain = useCallback(async (drawingId: string) => {
    if (!signedIn) return;
    const outbox = outboxRef.current;
    if (!outbox.isBaselineReady()) return;
    while (true) {
      const job = outbox.takeNext(drawingId);
      if (!job) return;
      setSyncStatus('saving');
      const rolled = quotaRollbackDrawings(
        lastServerRef.current,
        outbox.getScope(),
        outbox.getScopeGeneration(),
      );
      const outcome = await drainPersistJob({
        outbox,
        job,
        api: drawingsApi,
        drawings: drawingsRef.current,
        lastServer: rolled,
        revisions: revisionsRef.current,
        localDirty: pendingEdits.current.has(job.drawingId),
        errorInfo: (error) => ({ code: drawingErrorCode(error), status: drawingErrorStatus(error) }),
      });
      if (outcome.foreign) return;
      if (outcome.lastServer && outbox.getScope()) {
        lastServerRef.current = {
          scope: outbox.getScope() as ScopeKey,
          scopeGeneration: outbox.getScopeGeneration(),
          drawings: outcome.lastServer,
        };
        for (const item of outcome.lastServer) revisionsRef.current.set(item.id, item.revision);
      }
      if (outcome.conflict) conflictServerRef.current = outcome.conflict;
      if (outcome.apply.action === 'drawing') applyJobResult(job, outcome.apply.drawing);
      else if (outcome.apply.action === 'replaceList') {
        for (const item of outcome.apply.drawings) revisionsRef.current.set(item.id, item.revision);
        writeLocal(outcome.apply.drawings, false);
      } else if (outcome.apply.action === 'rollback') {
        const restored = quotaRollbackDrawings(
          lastServerRef.current,
          outbox.getScope(),
          outbox.getScopeGeneration(),
        );
        revisionsRef.current.clear();
        for (const item of restored) revisionsRef.current.set(item.id, item.revision);
        writeLocal(restored, false);
        setHistory(createHistory(restored));
        if (job.type === 'replace' && job.drawings && job.origin === 'import') {
          setRejectedImport(JSON.stringify({ schemaVersion: 1, drawings: job.drawings }));
        }
      } else if (outcome.apply.action === 'deleteRevision') {
        revisionsRef.current.delete(outcome.apply.id);
      } else if (outcome.apply.action === 'clearRevisions') {
        revisionsRef.current.clear();
      }
      if (outcome.status) setSyncStatus(outcome.status);
      if (outcome.kind === 'quota' || outcome.kind === 'conflict' || outcome.kind === 'retry') {
        setSyncHint(outcome.hint);
      } else if (outcome.status === 'idle' || outcome.status === 'saving') {
        setSyncHint(outcome.hint);
      }
      if (outcome.kind === 'retry') {
        /* 可重试失败（429/断网/5xx）不能只试一次就搁成 unsynced 等人手点——
           取证与线上都抓到过：一次 429 之后任务永远趴在队里。429 听服务器的
           Retry-After，其余按 5s→15s→45s→60s 退避；重试同步按钮照旧可用，
           人工点了会先走这里的清零再排新一轮。 */
        autoRetryAttempt.current += 1;
        const delay = nextDrainRetryDelayMs(autoRetryAttempt.current, outcome.retryAfterSeconds);
        if (autoRetryTimer.current) clearTimeout(autoRetryTimer.current);
        const generation = outbox.getScopeGeneration();
        autoRetryTimer.current = setTimeout(() => {
          autoRetryTimer.current = null;
          const box = outboxRef.current;
          if (box.getScopeGeneration() !== generation) return;
          if (box.isEmpty() || !box.isBaselineReady()) return;
          for (const id of box.readyIds()) void drainRef.current(id);
        }, delay);
      } else {
        autoRetryAttempt.current = 0;
        if (autoRetryTimer.current) {
          clearTimeout(autoRetryTimer.current);
          autoRetryTimer.current = null;
        }
      }
      for (const other of outcome.readyIds) {
        if (other !== drawingId) void drainRef.current(other);
      }
      if (outcome.kind === 'quota' || outcome.kind === 'conflict' || outcome.kind === 'retry') return;
      if (outcome.reconcile && outbox.isEmpty()) {
        const generation = outbox.getScopeGeneration();
        const scope = outbox.getScope();
        try {
          const remote = await drawingsApi.list(args.ticker, args.range, adjustment);
          if (outbox.getScopeGeneration() !== generation || !outbox.isEmpty()) return;
          writeLocal(remote.drawings, false);
          if (scope) {
            lastServerRef.current = {
              scope,
              scopeGeneration: generation,
              drawings: remote.drawings,
            };
          }
          outbox.setScopeRevision(remote.scopeRevision);
          outbox.clearBase();
          setSyncStatus('idle');
          setSyncHint(null);
        } catch {
          setSyncStatus('load_failed');
          setSyncHint('unsynced');
        }
        return;
      }
    }
  }, [adjustment, applyJobResult, args.range, args.ticker, signedIn, writeLocal]);

  const drainRef = useRef(drain);
  drainRef.current = drain;

  const enqueue = useCallback((job: Omit<PersistJob, 'generation' | 'scopeGeneration' | 'scope'>) => {
    if (!signedIn) return;
    const queued = outboxRef.current.enqueue(job);
    if (!queued) return;
    if (!outboxRef.current.isBaselineReady()) return;
    void drain(queued.drawingId);
  }, [drain, signedIn]);

  const loadScope = useCallback(async (generation: number) => {
    /* 这里不清 selectedId/inProgress/拖拽预览：交互态重置属于**换 scope**，
       由切换 effect 自己做（它本来就做了）。loadScope 还会被后台自愈调用
       （限流/断网后的定时重载）——后台恢复把用户正选中的图形踢掉，Inspector
       会当着用户的面消失（CI 取证等「颜色 红色」超时抓到的就是这个）。 */
    conflictServerRef.current = null;
    if (!signedIn) {
      const loaded = loadDrawings(storageKey);
      // 一行坏掉只丢那一行；解析全灭时先把原文留一份，再决定要不要改写。
      if (!loaded.ok) quarantineDrawings(storageKey);
      const list = loaded.drawings;
      writeLocal(list, false, { persist: loaded.ok || list.length ? 'now' : 'skip' });
      setHistory(createHistory(list));
      setSyncStatus('guest');
      setSyncHint(loaded.ok ? null : 'local_corrupt');
      return;
    }
    const cached = loadDrawings(storageKey);
    if (!cached.ok && !cached.missing) quarantineDrawings(storageKey);
    const preview = previewScopeLoad(cached);
    // Apply cache (including authoritative empty) before GET so AAPL rows
    // cannot stay editable under the MSFT storageKey while the list is in flight.
    writeLocal(preview.drawings, false, { persist: preview.persist });
    setHistory(createHistory(preview.drawings));
    setSyncStatus(preview.status === 'load_failed' ? 'load_failed' : 'saving');
    setSyncHint(preview.hint);
    const outcome = await completeScopeLoad({
      generation,
      outbox: outboxRef.current,
      cached,
      list: () => drawingsApi.list(args.ticker, args.range, adjustment),
      errorInfo: (error) => ({ code: drawingErrorCode(error), status: drawingErrorStatus(error) }),
    });
    if (outcome.foreign) return;
    if (outcome.lastServer && outboxRef.current.getScope()) {
      lastServerRef.current = {
        scope: outboxRef.current.getScope() as ScopeKey,
        scopeGeneration: outboxRef.current.getScopeGeneration(),
        drawings: outcome.lastServer,
      };
      for (const item of outcome.lastServer) revisionsRef.current.set(item.id, item.revision);
    }
    if (outcome.conflict && outcome.lastServer && outboxRef.current.getScope()) {
      conflictServerRef.current = {
        scope: outboxRef.current.getScope() as ScopeKey,
        scopeGeneration: outboxRef.current.getScopeGeneration(),
        scopeRevision: outcome.scopeRevision ?? 0,
        drawings: outcome.lastServer,
      };
    }
    if (outcome.apply !== 'none') {
      writeLocal(outcome.drawings, false, { persist: outcome.persist });
      setHistory(createHistory(outcome.drawings));
    }
    setSyncStatus(outcome.status);
    setSyncHint(outcome.hint);
    if (outcome.drain && outboxRef.current.isBaselineReady()) {
      for (const id of outboxRef.current.readyIds()) void drainRef.current(id);
    }
    if (!outcome.baselineReady && (outcome.status === 'load_failed' || outcome.status === 'write_failed')) {
      /* 列表被 429/断网挡下时也要自愈：baseline 建立不起来，drain 会一直跳过，
         挂载撞上限流热窗的页面若不自动重载就永远定身在 load_failed——离线画的
         东西连出手的机会都没有（取证抓到的零 POST 死局就是这么来的）。 */
      autoRetryAttempt.current += 1;
      const delay = nextDrainRetryDelayMs(autoRetryAttempt.current, outcome.retryAfterSeconds);
      if (autoRetryTimer.current) clearTimeout(autoRetryTimer.current);
      autoRetryTimer.current = setTimeout(() => {
        autoRetryTimer.current = null;
        const box = outboxRef.current;
        if (box.getScopeGeneration() !== generation) return;
        void loadScopeRef.current(generation);
      }, delay);
    } else if (outcome.baselineReady) {
      autoRetryAttempt.current = 0;
    }
  }, [adjustment, args.range, args.ticker, signedIn, storageKey, writeLocal]);

  const loadScopeRef = useRef(loadScope);
  loadScopeRef.current = loadScope;

  /** 把防抖窗口里的编辑立刻入队（只入队不发送），再清空计时器。 */
  const flushPendingEdits = useCallback(() => {
    for (const timer of styleTimers.current.values()) clearTimeout(timer);
    styleTimers.current.clear();
    const edits = [...pendingEdits.current.values()];
    pendingEdits.current.clear();
    if (!signedIn) return;
    for (const drawing of edits) {
      outboxRef.current.enqueue({ drawingId: drawing.id, type: 'update', drawing });
    }
  }, [signedIn]);

  /** 丢弃防抖窗口里的编辑（用户选了「用服务器版本」）。 */
  const discardPendingEdits = useCallback(() => {
    for (const timer of styleTimers.current.values()) clearTimeout(timer);
    styleTimers.current.clear();
    pendingEdits.current.clear();
  }, []);

  useEffect(() => {
    let cancelled = false;
    // 切 scope 前先把未发出的活收好：直接清计时器 + 清队列等于把离线改动删掉。
    flushPendingEdits();
    flushLocalSave();
    lastServerRef.current = null;
    revisionsRef.current.clear();
    conflictServerRef.current = null;
    setRejectedImport(null);
    setSelectedId(null);
    setFocusAnchor(null);
    setInProgress(null);
    dragPreviewRef.current = null;
    if (autoRetryTimer.current) {
      clearTimeout(autoRetryTimer.current);
      autoRetryTimer.current = null;
    }
    autoRetryAttempt.current = 0;
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
  }, [currentScope, flushLocalSave, flushPendingEdits, loadScope]);

  /* 断网恢复即刻重放：浏览器亲口说 online 了还让任务趴在队里等退避计时，
     等于把「网络回来了」这个最强信号扔掉。走事件而不是缩短退避间隔。 */
  useEffect(() => {
    if (!signedIn) return;
    const onOnline = () => {
      const box = outboxRef.current;
      if (autoRetryTimer.current) {
        clearTimeout(autoRetryTimer.current);
        autoRetryTimer.current = null;
      }
      autoRetryAttempt.current = 0;
      if (!box.isBaselineReady()) {
        // 断网期间连列表都没拉到：恢复后先重建 baseline，再由 loadScope 决定 drain。
        void loadScopeRef.current(box.getScopeGeneration());
        return;
      }
      if (box.isEmpty()) return;
      for (const id of box.readyIds()) void drainRef.current(id);
    };
    window.addEventListener('online', onOnline);
    return () => window.removeEventListener('online', onOnline);
  }, [signedIn]);

  useEffect(() => () => {
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    if (autoRetryTimer.current) clearTimeout(autoRetryTimer.current);
    flushPendingEdits();
    flushLocalSave();
    outboxRef.current.persist();
  }, [flushLocalSave, flushPendingEdits]);

  const persistOne = useCallback((drawing: ChartDrawing, mode: 'create' | 'update') => {
    setRejectedImport(null);
    enqueue({ drawingId: drawing.id, type: mode, drawing });
  }, [enqueue]);

  const scheduleUpdate = useCallback((drawing: ChartDrawing) => {
    if (!signedIn) return;
    const prev = styleTimers.current.get(drawing.id);
    if (prev) clearTimeout(prev);
    pendingEdits.current.set(drawing.id, drawing);
    styleTimers.current.set(drawing.id, setTimeout(() => {
      styleTimers.current.delete(drawing.id);
      pendingEdits.current.delete(drawing.id);
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
    // 结构性改动开一条新的历史记录，不再与上一串逐字编辑合并。
    coalesceRef.current = null;
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
    pendingEdits.current.delete(id);
    outboxRef.current.cancelId(id);
    setRejectedImport(null);
    const target = drawingsRef.current.find((item) => item.id === id);
    const next = drawingsRef.current.filter((item) => item.id !== id);
    pushDrawings(next);
    if (selectedId === id) {
      setSelectedId(null);
      setFocusAnchor(null);
    }
    enqueue({
      drawingId: id,
      type: 'delete',
      ...(target
        ? { drawing: target, expectedDrawingRevision: target.revision }
        : {}),
    });
  }, [enqueue, pushDrawings, selectedId]);

  const deleteSelected = useCallback(() => {
    if (!selectedId) return;
    deleteDrawing(selectedId);
  }, [deleteDrawing, selectedId]);

  const clearAll = useCallback(() => {
    discardPendingEdits();
    setRejectedImport(null);
    pushDrawings([]);
    setSelectedId(null);
    setFocusAnchor(null);
    enqueue({ drawingId: SCOPE_JOB_ID, type: 'clear' });
  }, [discardPendingEdits, enqueue, pushDrawings]);

  const syncHistoryDiff = useCallback((prev: ChartDrawing[], next: ChartDrawing[]) => {
    if (!signedIn) return;
    for (const op of diffPersistOps(prev, next)) {
      if (op.type === 'delete') {
        enqueue({
          drawingId: op.id,
          type: 'delete',
          drawing: op.drawing,
          expectedDrawingRevision: op.drawing.revision,
        });
      } else if (op.type === 'create') enqueue({ drawingId: op.drawing.id, type: 'create', drawing: op.drawing });
      else enqueue({ drawingId: op.drawing.id, type: 'update', drawing: op.drawing });
    }
  }, [enqueue, signedIn]);

  /**
   * 历史快照里连 revision 一起克隆，撤销会把很旧的版本号一起搬回来。版本号是
   * 服务器记账，不是用户可撤销的状态：恢复时一律换成最新已知值。
   */
  const restoreHistory = useCallback((list: ChartDrawing[]): ChartDrawing[] => (
    applyKnownRevisions(list, revisionsRef.current)
  ), []);

  const undo = useCallback(() => {
    setHistory((prev) => {
      if (!canUndo(prev)) return prev;
      // Drop the style debounce: a lock/color PUT queued before undo must not
      // fire after the history diff has already enqueued the reverted payload.
      discardPendingEdits();
      const stepped = historyUndo(prev);
      const present = restoreHistory(stepped.present);
      const next = { ...stepped, present };
      coalesceRef.current = null;
      drawingsRef.current = present;
      setDrawings(present);
      flushLocalSave();
      saveDrawings(storageKey, present);
      syncHistoryDiff(prev.present, present);
      return next;
    });
  }, [discardPendingEdits, flushLocalSave, restoreHistory, storageKey, syncHistoryDiff]);

  const redo = useCallback(() => {
    setHistory((prev) => {
      if (!canRedo(prev)) return prev;
      discardPendingEdits();
      const stepped = historyRedo(prev);
      const present = restoreHistory(stepped.present);
      const next = { ...stepped, present };
      coalesceRef.current = null;
      drawingsRef.current = present;
      setDrawings(present);
      flushLocalSave();
      saveDrawings(storageKey, present);
      syncHistoryDiff(prev.present, present);
      return next;
    });
  }, [discardPendingEdits, flushLocalSave, restoreHistory, storageKey, syncHistoryDiff]);

  /**
   * 逐字编辑合并成一条历史记录：不合并的话每一键都深拷两份图形表、整表写一次
   * localStorage，撤销还得一个字符一个字符往回走、每步发一次 PUT。
   */
  const patchDrawing = useCallback((
    id: string,
    patch: Partial<ChartDrawing>,
    persist: boolean,
    options?: { coalesce?: boolean },
  ) => {
    const next = drawingsRef.current.map((item) => {
      if (item.id !== id) return item;
      return { ...item, ...patch, updatedAt: nowIso() };
    });
    const now = Date.now();
    const last = coalesceRef.current;
    const canCoalesce = (options?.coalesce ?? persist)
      && last !== null
      && last.id === id
      && now - last.at <= COALESCE_WINDOW_MS;
    coalesceRef.current = (options?.coalesce ?? persist) ? { id, at: now } : null;
    commitLocal(next, !canCoalesce, { persist: canCoalesce ? 'debounced' : 'now' });
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
    // 视窗极值只算一次：指针路径不再每个事件 flatMap + 展开求 min/max。
    const ctx: RenderContext = visibleCtx ?? {
      bars,
      range: args.range,
      xMin: 0,
      xMax: bars.length - 1,
      yMin: Math.min(...bars.map((bar) => bar.l)),
      yMax: Math.max(...bars.map((bar) => bar.h)),
    };

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
        ...(args.snapCandidates ?? []),
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

    const onDown = (event: {
      offsetX: number;
      offsetY: number;
      zrByTouch?: boolean;
      event?: { altKey?: boolean; shiftKey?: boolean; button?: number; pointerType?: string; type?: string };
    }) => {
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
      const toPixel = (pt: Point): Point | null => {
        const px = chart.convertToPixel({ gridIndex: 0 }, [pt.x, pt.y]) as number[] | null;
        if (!px || !Number.isFinite(px[0]) || !Number.isFinite(px[1])) return null;
        return { x: px[0], y: px[1] };
      };
      const hits = drawingsRef.current
        .map((drawing) => toProjectedDrawing(drawing, ctx))
        .filter((item): item is ProjectedDrawing => item !== null)
        .map((item) => projectToPixels(item, toPixel));
      const pointerKind = pointerKindFromEvent(event);
      const hit = hitTestDrawings(
        hits,
        { x: event.offsetX, y: event.offsetY },
        pointerKind,
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
      if (!target || isLockedDragBlocked(target)) return;
      const startConverted = chart.convertFromPixel({ gridIndex: 0 }, [event.offsetX, event.offsetY]) as number[] | null;
      const startIdx = startConverted ? snapBarIndex(startConverted[0], bars.length) : null;
      pointerKindRef.current = pointerKind;
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
        moved: false,
      };
      dragPreviewRef.current = target;
      // 拖图形时先关掉 inside dataZoom 的拖动漫游，否则图会在手底下平移，
      // 松手提交的是平移之后落在光标下的那根 K 线。
      setChartRoam(chart, false);
    };

    const onMove = (event: { offsetX: number; offsetY: number }) => {
      const drag = dragRef.current;
      if (!drag) return;
      if (dragExceedsThreshold(drag.startPixel, { x: event.offsetX, y: event.offsetY }, pointerKindRef.current)) {
        drag.moved = true;
      }
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = 0;
        if (!dragRef.current) return;
        const converted = chart.convertFromPixel({ gridIndex: 0 }, [event.offsetX, event.offsetY]) as number[] | null;
        if (!converted) return;
        // 夹回网格与合法价区：不夹的话拖出图外会算出 price<=0，
        // 前端 schema 拒收、后端 400，队列里留下一条永远重放的坏任务。
        const pointer = clampDragPoint(
          { barIndex: snapBarIndex(converted[0], bars.length) ?? converted[0], price: converted[1] },
          bars.length,
        );
        const snapshot = drawingsRef.current;
        const moved = dragMove({
          drawings: snapshot,
          drag,
          pointer,
          bars,
          range: args.range,
        });
        if (moved.drawings !== snapshot) return;
        dragPreviewRef.current = moved.preview;
        refreshGraphic(chart, ctx);
      });
    };

    const onUp = () => {
      const drag = dragRef.current;
      const preview = dragPreviewRef.current;
      dragRef.current = null;
      dragPreviewRef.current = null;
      if (!drag) return;
      setChartRoam(chart, true);
      // 纯选中的一次点击不该提交任何东西：不然会多一条只差 updatedAt 的历史记录
      // （撤销看不出变化也不发任何操作），还会白白 PUT 一次把 revision 顶上去。
      const changed = Boolean(preview) && drag.moved && mutableFieldsDiffer(drag.origin, preview as ChartDrawing);
      if (!preview || !changed) {
        refreshGraphic(chart, ctx);
        return;
      }
      const committed = { ...preview, updatedAt: nowIso() };
      if (!parseDrawing(committed)) {
        // 提交前再过一遍 schema：非法锚点既不该进本地存档，也不该进出队列。
        refreshGraphic(chart, ctx);
        return;
      }
      const next = drawingsRef.current.map((item) => (
        item.id === committed.id ? committed : item
      ));
      pushDrawings(next);
      const updated = next.find((item) => item.id === committed.id);
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
  }, [args.bars, args.chart, args.ma20, args.measureActive, args.range, args.snapCandidates, completeDrawing, inProgress, persistOne, pushDrawings, refreshGraphic, selectedId, tool, visibleCtx]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (isTextInputTarget(event.target)) return;
      if (event.key === 'Escape') {
        // 覆盖层先吃这次 Escape：Drawer / ConfirmDialog 挂在 document 上且不阻止冒泡，
        // 不让路的话一次 Escape 会同时关弹层和重置工具、收起全屏工作区。
        if (escapeHandledByOverlay({
          defaultPrevented: event.defaultPrevented,
          openModals: openModalCount(),
          workspaceExpanded: expanded,
        })) return;
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
    if (autoRetryTimer.current) {
      clearTimeout(autoRetryTimer.current);
      autoRetryTimer.current = null;
    }
    autoRetryAttempt.current = 0;
    if (!signedIn) return;
    const outbox = outboxRef.current;
    const failure = syncStatus === 'load_failed'
      ? 'load_failed'
      : syncStatus === 'conflict'
        ? 'conflict'
        : syncStatus === 'write_failed' || syncStatus === 'unsynced'
          ? 'write_failed'
          : null;
    const action = resolveRetryAction(failure, outbox.isEmpty(), outbox.isBaselineReady());
    if (action === 'reload') {
      void loadScope(outbox.getScopeGeneration());
      return;
    }
    if (action === 'replay') {
      outbox.restoreForRetry(outbox.snapshot());
      for (const id of outbox.readyIds()) void drain(id);
    }
  }, [drain, loadScope, signedIn, syncStatus]);

  const keepLocalConflict = useCallback(async () => {
    if (!signedIn) return;
    const outbox = outboxRef.current;
    const generation = outbox.getScopeGeneration();
    const current = outbox.getScope();
    let snapshot = conflictServerRef.current;
    const keepDecision = applyConflictDecision({
      snapshot,
      currentScope: current,
      generation,
      intent: 'keep',
    });
    if (keepDecision.action === 'ignore') return;
    if (!snapshot) {
      try {
        const remote = await drawingsApi.list(args.ticker, args.range, adjustment);
        if (outbox.getScopeGeneration() !== generation) return;
        snapshot = {
          scope: current ?? {
            identity: args.identity.key,
            ticker: args.ticker,
            range: args.range,
            adjustment,
          },
          scopeGeneration: generation,
          scopeRevision: remote.scopeRevision,
          drawings: remote.drawings,
        };
        conflictServerRef.current = snapshot;
      } catch {
        setSyncHint('conflict');
        return;
      }
    }
    if (snapshot.scopeGeneration !== outbox.getScopeGeneration()) return;
    const server = snapshot.drawings;
    const desired = drawingsRef.current;
    // The local state already contains debounced edits. Cancel their timers,
    // then express the complete desired state as one atomic scope replacement.
    discardPendingEdits();
    setRejectedImport(null);
    revisionsRef.current.clear();
    for (const item of server) revisionsRef.current.set(item.id, item.revision);
    const queued = outbox.replaceWithExactScope(
      desired,
      snapshot.scopeRevision,
      'conflict_keep',
    );
    if (!queued) return;
    conflictServerRef.current = null;
    lastServerRef.current = {
      scope: snapshot.scope,
      scopeGeneration: outbox.getScopeGeneration(),
      drawings: server,
    };
    setSyncStatus('saving');
    setSyncHint(null);
    void drain(queued.drawingId);
  }, [adjustment, args.identity.key, args.range, args.ticker, discardPendingEdits, drain, signedIn]);

  const takeServerConflict = useCallback(async () => {
    if (!signedIn) return;
    const outbox = outboxRef.current;
    const generation = outbox.getScopeGeneration();
    let snapshot = conflictServerRef.current;
    const takeDecision = applyConflictDecision({
      snapshot,
      currentScope: outbox.getScope(),
      generation,
      intent: 'take',
    });
    if (takeDecision.action === 'ignore') return;
    if (!snapshot) {
      try {
        const remote = await drawingsApi.list(args.ticker, args.range, adjustment);
        if (outbox.getScopeGeneration() !== generation) return;
        snapshot = {
          scope: outbox.getScope() ?? {
            identity: args.identity.key,
            ticker: args.ticker,
            range: args.range,
            adjustment,
          },
          scopeGeneration: generation,
          scopeRevision: remote.scopeRevision,
          drawings: remote.drawings,
        };
      } catch {
        setSyncHint('conflict');
        return;
      }
    }
    if (snapshot.scopeGeneration !== outbox.getScopeGeneration()) return;
    const server = snapshot.drawings;
    outbox.cancelAll();
    // 「用服务器版本」也要停掉防抖里的样式/文字改动：否则 400ms 后它照样发出去，
    // 而且拿的是刚刷新的 revision，PUT 会成功——用户丢弃的编辑又被写了回去。
    discardPendingEdits();
    setRejectedImport(null);
    conflictServerRef.current = null;
    revisionsRef.current.clear();
    outbox.setScopeRevision(snapshot.scopeRevision);
    outbox.clearBase();
    outbox.markBaselineReady();
    lastServerRef.current = {
      scope: snapshot.scope,
      scopeGeneration: snapshot.scopeGeneration,
      drawings: server,
    };
    for (const item of server) revisionsRef.current.set(item.id, item.revision);
    writeLocal(server, false);
    setHistory(createHistory(server));
    setSyncStatus('idle');
    setSyncHint(null);
  }, [adjustment, args.identity.key, args.range, args.ticker, discardPendingEdits, signedIn, writeLocal]);

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
    discardPendingEdits();
    setSelectedId(null);
    setFocusAnchor(null);
    setImportError(null);
    setRejectedImport(null);
    pushDrawings(incoming);
    if (signedIn) {
      enqueue({
        drawingId: SCOPE_JOB_ID,
        type: 'replace',
        drawings: incoming,
        origin: 'import',
      });
    }
    return null;
  }, [adjustment, args.range, args.ticker, discardPendingEdits, enqueue, pushDrawings, signedIn]);

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
    // 坏行已经在读取时丢掉：还有能用的图形就照常导入，只在一条都不剩时报错。
    if (!loaded.ok && !loaded.drawings.length) {
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
    exportRejectedImport: () => rejectedImport,
    hasRejectedImport: Boolean(rejectedImport),
    importJson,
    importFromText,
    importAnonymous,
  };
}

export type DrawingController = ReturnType<typeof useDrawingController>;
export { parseDrawing } from './schema.ts';
