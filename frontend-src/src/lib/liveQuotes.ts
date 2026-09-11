import { t } from '../i18n/core.ts';

/** One browser connection, with per-symbol notifications and no list re-sorting. */
export interface LiveQuote {
  symbol: string;
  price: number | null;
  previous_close: number | null;
  change: number | null;
  change_pct: number | null;
  trade_at: string | null;
  received_at: string | null;
  session: 'regular' | 'premarket' | 'postmarket' | 'closed';
  source: string | null;
  freshness: 'live' | 'stale' | 'snapshot' | 'missing';
  subscription_status: 'live' | 'pending' | 'limited' | 'disabled' | 'unconfigured' | 'unavailable';
  subscription_reason?: string | null;
}
export interface QuoteStatus {
  enabled: boolean;
  configured: boolean;
  public_enabled: boolean;
  allowed?: boolean;
  connected: boolean;
  connection_status: string;
  market_session?: LiveQuote['session'];
  resync_required?: boolean;
}
export interface QuoteEnvelope { quotes: LiveQuote[]; status: QuoteStatus }
export interface RadarUpdate { events: Record<string, unknown>[]; resync_required?: boolean }
const STATUS_FIELDS = ['enabled', 'configured', 'public_enabled', 'allowed', 'connected', 'connection_status', 'market_session', 'resync_required'] as const;
const QUOTE_FIELDS = ['symbol', 'price', 'previous_close', 'change', 'change_pct', 'trade_at', 'received_at', 'session', 'source', 'freshness', 'subscription_status', 'subscription_reason'] as const;
const MAX_STORED_QUOTES = 2048;
const MAX_STORED_RADAR_EVENTS = 512;
const INITIAL_STATUS: QuoteStatus = { enabled: false, configured: false, public_enabled: false, connected: false, connection_status: 'disabled' };
const isRecord = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value);
function isQuoteStatus(value: unknown): value is QuoteStatus {
  return isRecord(value)
    && typeof value.enabled === 'boolean' && typeof value.configured === 'boolean'
    && typeof value.public_enabled === 'boolean' && typeof value.connected === 'boolean'
    && typeof value.connection_status === 'string' && value.connection_status.length > 0
    && (value.allowed === undefined || typeof value.allowed === 'boolean')
    && (value.resync_required === undefined || typeof value.resync_required === 'boolean')
    && (value.market_session === undefined || (typeof value.market_session === 'string' && ['regular', 'premarket', 'postmarket', 'closed'].includes(value.market_session)));
}
function isQuote(value: unknown): value is LiveQuote {
  if (!isRecord(value) || typeof value.symbol !== 'string' || !/^[A-Z][A-Z0-9.-]{0,14}$/i.test(value.symbol)) return false;
  const nullableNumber = (field: unknown) => field === null || (typeof field === 'number' && Number.isFinite(field));
  const nullableTime = (field: unknown) => field === null || (typeof field === 'string' && Number.isFinite(Date.parse(field)));
  return nullableNumber(value.price) && (value.price === null || Number(value.price) > 0)
    && nullableNumber(value.previous_close) && nullableNumber(value.change) && nullableNumber(value.change_pct)
    && nullableTime(value.trade_at) && nullableTime(value.received_at)
    && (value.source === null || typeof value.source === 'string')
    && typeof value.session === 'string' && ['regular', 'premarket', 'postmarket', 'closed'].includes(value.session)
    && typeof value.freshness === 'string' && ['live', 'stale', 'snapshot', 'missing'].includes(value.freshness)
    && typeof value.subscription_status === 'string' && ['live', 'pending', 'limited', 'disabled', 'unconfigured', 'unavailable'].includes(value.subscription_status);
}
function isQuoteEnvelope(value: unknown): value is QuoteEnvelope {
  return isRecord(value) && isQuoteStatus(value.status) && Array.isArray(value.quotes) && value.quotes.every(isQuote);
}
export const MARKET_FUNDS = ['SPY', 'QQQ', 'DIA', 'IWM'];
export function normalizeQuoteSymbols(symbols: readonly string[]): string[] {
  return [...new Set(symbols.map(s => s.trim().toUpperCase()).filter(s => /^[A-Z][A-Z0-9]{0,9}(?:[.-][A-Z0-9]{1,4})?$/.test(s)))];
}
const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;
const timestamp = (value: string | null) => {
  if (!value || DATE_ONLY.test(value.trim())) return 0;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
};
// Allow one minute of client/server clock skew, but never let a future trade
// become the ordering watermark and block all subsequent valid prices.
const MAX_CLOCK_SKEW_MS = 60_000;
const MAX_CLOCK_CALIBRATION_MS = 10 * 60_000;
let clockOffsetMs = 0;
export function quoteClockOffsetMs(): number {
  return clockOffsetMs;
}
function calibrateClock(receivedAt: string | null): void {
  if (!receivedAt) return;
  const parsed = timestamp(receivedAt);
  if (!parsed) return;
  const offset = parsed - Date.now();
  // A 1–2 minute slow device is the case we must keep. Multi-year
  // "future" stamps must not become the clock sample.
  if (!Number.isFinite(offset) || Math.abs(offset) > MAX_CLOCK_CALIBRATION_MS) return;
  clockOffsetMs = offset;
}
export function reliableTimestamp(value: string | null): number {
  const parsed = timestamp(value);
  return parsed && parsed <= Date.now() + clockOffsetMs + MAX_CLOCK_SKEW_MS ? parsed : 0;
}
const quoteDateFormat = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit' });
export function visibleQuoteDate(value?: string | null): string | null {
  if (!value) return null;
  const trimmed = value.trim();
  if (DATE_ONLY.test(trimmed)) {
    const parsed = Date.parse(`${trimmed}T00:00:00Z`);
    return Number.isFinite(parsed) && new Date(parsed).toISOString().slice(0, 10) === trimmed ? trimmed : null;
  }
  const parsed = reliableTimestamp(trimmed);
  return parsed ? quoteDateFormat.format(parsed) : null;
}

/** Never combine a new price with a percentage from a different baseline.
 * A 0.05 percentage-point tolerance accommodates rounded provider fields. */
export function liveQuoteChangePct(quote: LiveQuote | undefined): number | null {
  if (quote?.price == null || !Number.isFinite(quote.price) || quote.price <= 0
    || quote.previous_close == null || !Number.isFinite(quote.previous_close) || quote.previous_close <= 0
    || quote.change_pct == null || !Number.isFinite(quote.change_pct)) return null;
  const expected = (quote.price / quote.previous_close - 1) * 100;
  return Math.abs(quote.change_pct - expected) <= 0.05 ? quote.change_pct : null;
}
type Listener = () => void;
type Stream = Pick<EventSource, 'addEventListener' | 'close' | 'onerror'>;
interface QuoteRuntime {
  fetch: typeof fetch;
  stream: (url: string) => Stream;
}
export class QuoteStore {
  private runtime: QuoteRuntime;
  private quotes = new Map<string, LiveQuote>();
  private pending = new Map<string, LiveQuote>();
  private listeners = new Map<string, Set<Listener>>();
  private statusListeners = new Set<Listener>();
  private radarVersion = 0;
  private radarVersionListeners = new Set<Listener>();
  private radarEvents = new Map<string, Record<string, unknown>>();
  private radarEventListeners = new Map<string, Set<Listener>>();
  private radarListeners = new Set<(update: RadarUpdate) => void>();
  private subscriptionKey = '';
  private consumers = new Map<symbol, { symbols: string[]; focus: string[] }>();
  private status = INITIAL_STATUS;
  private stream: Stream | null = null;
  private connectController: AbortController | null = null;
  private pollController: AbortController | null = null;
  private radarResyncOnConnect = false;
  private generation = 0;
  private started = false;
  private visible = true;
  private permitted = false;
  private owner = false;
  private terminal = false;
  private failures = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private flushTimer: ReturnType<typeof setTimeout> | null = null;
  private pollTimer: ReturnType<typeof setInterval> | null = null;
  constructor(runtime: QuoteRuntime = { fetch: (...args) => fetch(...args), stream: url => new EventSource(url, { withCredentials: true }) }) { this.runtime = runtime; }
  getQuote = (symbol: string) => this.quotes.get(symbol.toUpperCase());
  getStatus = () => this.status;
  getRadarVersion = () => this.radarVersion;
  subscribeRadarVersion = (listener: Listener) => { this.radarVersionListeners.add(listener); return () => { this.radarVersionListeners.delete(listener); }; };
  getRadarEvent = (id: string) => this.radarEvents.get(id);
  subscribeRadarEvent = (id: string, listener: Listener) => {
    const group = this.radarEventListeners.get(id) ?? new Set<Listener>();
    group.add(listener); this.radarEventListeners.set(id, group);
    return () => { group.delete(listener); if (!group.size) this.radarEventListeners.delete(id); };
  };
  private ingestRadar(data: RadarUpdate) {
    if (!Array.isArray(data.events)) return;
    let changed = false;
    const accepted: Record<string, unknown>[] = [];
    for (const event of data.events) {
      if (!isRecord(event) || typeof event.event_id !== 'string' || !event.event_id || event.event_id.length > 256
        || typeof event.state_version !== 'number' || !Number.isSafeInteger(event.state_version) || event.state_version < 0) continue;
      const id = event.event_id;
      if (event.state_version <= Number(this.radarEvents.get(id)?.state_version ?? -1)) continue;
      accepted.push(event);
      changed = true; this.radarEvents.set(id, event); this.radarEventListeners.get(id)?.forEach(fn => fn());
    }
    if (this.radarEvents.size > MAX_STORED_RADAR_EVENTS) {
      for (const id of this.radarEvents.keys()) {
        if (this.radarEvents.size <= MAX_STORED_RADAR_EVENTS) break;
        if (!this.radarEventListeners.has(id)) this.radarEvents.delete(id);
      }
    }
    if (changed) { this.radarVersion++; this.radarVersionListeners.forEach(fn => fn()); }
    this.radarListeners.forEach(fn => fn({ events: accepted, resync_required: data.resync_required === true }));
  }
  subscribe = (symbol: string, listener: Listener) => {
    const key = symbol.toUpperCase();
    const group = this.listeners.get(key) ?? new Set<Listener>();
    group.add(listener); this.listeners.set(key, group);
    return () => { group.delete(listener); if (!group.size) this.listeners.delete(key); };
  };
  subscribeStatus = (listener: Listener) => { this.statusListeners.add(listener); return () => { this.statusListeners.delete(listener); }; };
  subscribeRadar = (listener: (update: RadarUpdate) => void) => { this.radarListeners.add(listener); return () => { this.radarListeners.delete(listener); }; };
  register(symbols: readonly string[], focus: readonly string[] = []) {
    const id = Symbol();
    this.consumers.set(id, { symbols: normalizeQuoteSymbols(symbols), focus: normalizeQuoteSymbols(focus) });
    this.subscriptionsChanged();
    return () => { this.consumers.delete(id); this.subscriptionsChanged(); };
  }
  start(owner: boolean) {
    this.stop(); this.started = true; this.owner = owner; this.terminal = false; this.failures = 0;
    this.radarResyncOnConnect = true;
    this.pollTimer = setInterval(() => {
      if (!this.permitted || !this.visible) return;
      const generation = this.generation;
      void this.snapshot(generation, false, 'poll').catch(() => {
        // A previous identity/page poll may reject after stop() or reconnect.
        if (generation === this.generation && this.started && this.visible && !this.terminal) this.markDisconnected();
      });
    }, 60_000);
    this.schedule(0);
    return () => this.stop();
  }
  stop() {
    this.started = false; this.permitted = false; this.generation++;
    this.closeStream();
    this.connectController?.abort(); this.pollController?.abort();
    this.connectController = this.pollController = null;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    if (this.pollTimer) clearInterval(this.pollTimer);
    if (this.flushTimer) clearTimeout(this.flushTimer);
    this.reconnectTimer = this.pollTimer = this.flushTimer = null;
    clockOffsetMs = 0;
    this.pending.clear(); this.clearQuotes(); this.setStatus(INITIAL_STATUS);
  }
  setVisible(visible: boolean) {
    if (visible === this.visible) return;
    this.visible = visible;
    if (!visible) {
      this.generation++; this.closeStream();
      this.connectController?.abort();
      this.markDisconnected();
    } else {
      this.radarResyncOnConnect = true;
      this.schedule(0);
    }
  }
  private clearQuotes() {
    const ids = [...this.radarEvents.keys()]; this.radarEvents.clear();
    for (const id of ids) this.radarEventListeners.get(id)?.forEach(fn => fn());
    if (ids.length) { this.radarVersion++; this.radarVersionListeners.forEach(fn => fn()); }
    const symbols = [...this.quotes.keys()]; this.quotes.clear();
    for (const symbol of symbols) this.listeners.get(symbol)?.forEach(fn => fn());
  }
  private setStatus(status: QuoteStatus) {
    // Provider heartbeats include volatile as_of/last_message_at counters.
    // They aren't display state and must not redraw every subscribed chart.
    if (STATUS_FIELDS.every(key => status[key] === this.status[key])) return;
    this.status = Object.fromEntries(STATUS_FIELDS.map(key => [key, status[key]])) as unknown as QuoteStatus;
    this.statusListeners.forEach(fn => fn());
  }
  private symbols() {
    const rows = [...this.consumers.values()];
    const all = normalizeQuoteSymbols(rows.flatMap(row => row.symbols));
    const focus = normalizeQuoteSymbols(rows.flatMap(row => row.focus)).filter(symbol => all.includes(symbol));
    const ordered = normalizeQuoteSymbols([...MARKET_FUNDS.filter(symbol => all.includes(symbol)), ...focus, ...all]);
    const symbols = ordered.slice(0, 200);
    return { symbols, focus: focus.filter(symbol => symbols.includes(symbol)), omitted: ordered.slice(200) };
  }
  private subscriptionsChanged() {
    const { symbols, focus, omitted } = this.symbols();
    const key = JSON.stringify([symbols, focus]);
    if (key === this.subscriptionKey) {
      if (this.permitted && omitted.length) this.query();
      return;
    }
    this.subscriptionKey = key;
    this.schedule();
  }
  private query() {
    const { symbols, focus, omitted } = this.symbols();
    if (this.permitted && omitted.length) this.ingest(omitted.map(symbol => ({
      ...(this.pending.get(symbol) ?? this.quotes.get(symbol) ?? { symbol, price: null, previous_close: null, change: null, change_pct: null, trade_at: null, received_at: null, source: null, session: this.status.market_session ?? 'closed' }),
      subscription_status: 'limited', freshness: 'snapshot',
    })));
    return new URLSearchParams({ symbols: symbols.join(','), focus: focus.join(',') }).toString();
  }
  private closeStream() { this.stream?.close(); this.stream = null; }
  private schedule(delay = 100) {
    if (!this.started || !this.visible || this.terminal) return;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = setTimeout(() => { this.reconnectTimer = null; void this.connect(); }, delay);
  }
  private accepts(status: QuoteStatus) { return status.enabled && status.configured && (status.allowed ?? (status.public_enabled || this.owner)); }
  private ingestStatus(status: unknown) {
    if (!isQuoteStatus(status)) return;
    this.setStatus(status);
    if (status.resync_required) this.radarListeners.forEach(fn => fn({ events: [], resync_required: true }));
    if (!this.accepts(status)) {
      this.terminal = true; this.permitted = false; this.closeStream(); this.pending.clear(); this.clearQuotes();
    }
  }
  private retryAfterMs(response: { headers?: { get?: (name: string) => string | null } }, fallback: number): number {
    const raw = response.headers?.get?.('Retry-After');
    const seconds = raw == null || raw === '' ? Number.NaN : Number(raw);
    return Number.isFinite(seconds) && seconds > 0 ? Math.min(300, seconds) * 1000 : fallback;
  }
  private async snapshot(generation: number, probe = false, origin: 'connect' | 'poll' = 'connect') {
    const current = origin === 'poll' ? this.pollController : this.connectController;
    current?.abort();
    const controller = new AbortController();
    if (origin === 'poll') this.pollController = controller;
    else this.connectController = controller;
    const timeout = setTimeout(() => controller.abort(), 10_000);
    try {
      const response = await this.runtime.fetch(`/api/quotes${probe ? '' : `?${this.query()}`}`, { credentials: 'include', signal: controller.signal, cache: 'no-store' });
      if (generation !== this.generation) return false;
      if ([401, 403, 404].includes(response.status)) {
        this.terminal = true; this.permitted = false; this.closeStream(); this.pending.clear(); this.clearQuotes();
        this.setStatus({ ...INITIAL_STATUS, connection_status: 'unavailable' }); return false;
      }
      if (!response.ok) {
        if (origin === 'connect') {
          this.schedule(this.retryAfterMs(response, Math.min(30_000, 2_000 * 2 ** this.failures++)));
        }
        return false;
      }
      let data: unknown;
      try { data = await response.json(); } catch { /* Invalid JSON uses the same safe fallback as an invalid envelope. */ }
      if (generation !== this.generation || !this.visible || !this.started) return false;
      if (!isQuoteEnvelope(data)) {
        // Old deployments and proxies can return JSON that isn't a quote
        // response. Fall back before notifying React with an invalid status.
        this.permitted = false; this.closeStream(); this.pending.clear(); this.clearQuotes();
        this.setStatus({ ...INITIAL_STATUS, connection_status: 'unavailable' });
        this.schedule(Math.min(30_000, 2_000 * 2 ** this.failures++));
        return false;
      }
      this.ingestStatus(data.status);
      if (this.terminal) return false;
      this.permitted = true; this.ingest(data.quotes, 'snapshot'); return true;
    } finally { clearTimeout(timeout); }
  }
  private async connect() {
    if (!this.started || !this.visible || this.terminal) return;
    const generation = ++this.generation;
    this.closeStream();
    try {
      const probe = !this.permitted;
      if (!await this.snapshot(generation, probe)) return;
      if (generation !== this.generation || !this.started || !this.visible) return;
      // Probe contains no symbols; this snapshot starts price loading before SSE arrives.
      if (probe && !await this.snapshot(generation)) return;
      if (generation !== this.generation || !this.started || !this.visible) return;
      const stream = this.runtime.stream(`/api/quotes/stream?${this.query()}`); this.stream = stream;
      let streamReady = false;
      const read = <T,>(callback: (data: T) => void) => (event: Event) => {
        if (generation !== this.generation || this.stream !== stream) return;
        try { streamReady = true; callback(JSON.parse((event as MessageEvent).data) as T); } catch { /* A malformed event does not erase the last quote. */ }
      };
      stream.addEventListener('quotes', read<unknown>(data => {
        if (!isRecord(data)) return;
        if ('status' in data) {
          if (!isQuoteStatus(data.status)) return;
          this.ingestStatus(data.status);
        }
        if (!this.terminal && Array.isArray(data.quotes)) this.ingest(data.quotes.filter(isQuote));
        this.failures = 0;
      }));
      stream.addEventListener('status', read<QuoteStatus>(data => this.ingestStatus(data)));
      stream.addEventListener('radar', read<RadarUpdate>(data => this.ingestRadar(data)));
      if (this.radarResyncOnConnect) {
        this.radarResyncOnConnect = false;
        this.radarListeners.forEach(fn => fn({ events: [], resync_required: true }));
      }
      stream.onerror = () => {
        if (this.stream !== stream || generation !== this.generation) return;
        this.closeStream(); this.markDisconnected();
        this.radarResyncOnConnect = true;
        const backoff = Math.min(30_000, 2_000 * 2 ** this.failures++);
        this.schedule(streamReady ? backoff : Math.max(30_000, backoff));
      };
    } catch {
      if (generation !== this.generation || !this.started || !this.visible) return;
      this.markDisconnected(); this.schedule(Math.min(30_000, 2_000 * 2 ** this.failures++));
    }
  }
  private markDisconnected() {
    this.setStatus({ ...this.status, connected: false, connection_status: 'reconnecting' });
    this.ingest([...this.quotes.values()].map(quote => ({ ...quote, freshness: quote.price == null ? 'missing' : 'stale' })));
  }
  private ingest(quotes: LiveQuote[], kind: 'snapshot' | 'stream' = 'stream') {
    if (!Array.isArray(quotes)) return;
    for (const raw of quotes) {
      if (!raw || typeof raw.symbol !== 'string') continue;
      const symbol = raw.symbol.toUpperCase();
      if (raw.price != null && (!Number.isFinite(raw.price) || raw.price <= 0)) continue;
      if (raw.received_at) calibrateClock(raw.received_at);
      if (raw.price != null && !reliableTimestamp(raw.trade_at)) continue;
      if (raw.received_at && !reliableTimestamp(raw.received_at)) continue;
      if (raw.trade_at && raw.received_at) {
        const tradeAt = timestamp(raw.trade_at);
        const receivedAt = timestamp(raw.received_at);
        if (tradeAt && receivedAt && tradeAt > receivedAt + MAX_CLOCK_SKEW_MS) continue;
      }
      const previous = this.pending.get(symbol) ?? this.quotes.get(symbol);
      // Subscription state may change with an older REST price; keep newest price fields.
      const quote = previous && (timestamp(raw.trade_at) < timestamp(previous.trade_at) || (kind === 'snapshot' && previous.price != null && timestamp(raw.trade_at) === timestamp(previous.trade_at)))
        ? { ...previous, subscription_status: raw.subscription_status, freshness: raw.subscription_status === 'live' && raw.freshness === 'snapshot' ? previous.freshness : raw.freshness }
        : { ...raw, symbol,
          change_pct: liveQuoteChangePct(raw),
          change: raw.price != null && raw.previous_close != null && raw.previous_close > 0
            && raw.change != null && Number.isFinite(raw.change)
            && Math.abs(raw.change - (raw.price - raw.previous_close)) <= 0.01 ? raw.change : null,
        };
      this.pending.set(symbol, quote);
    }
    if (!this.flushTimer) this.flushTimer = setTimeout(() => {
      this.flushTimer = null;
      for (const [symbol, quote] of this.pending) {
        const previous = this.quotes.get(symbol);
        if (previous && QUOTE_FIELDS.every(key => previous[key] === quote[key])) continue;
        this.quotes.set(symbol, quote); this.listeners.get(symbol)?.forEach(fn => fn());
      }
      this.pending.clear();
      // Keep all currently requested symbols; evict only old navigation data.
      if (this.quotes.size > MAX_STORED_QUOTES) {
        const active = new Set(this.symbols().symbols);
        for (const symbol of this.quotes.keys()) {
          if (this.quotes.size <= MAX_STORED_QUOTES) break;
          if (!active.has(symbol) && !this.listeners.has(symbol)) this.quotes.delete(symbol);
        }
      }
    }, 250);
  }
}
export const quoteStore = new QuoteStore();
export function quoteLabel(quote: LiveQuote, currentSession = quote.session): string {
  if (quote.subscription_status === 'unavailable') return t('暂无实时行情 · 定时更新');
  if (quote.subscription_status === 'limited') return t('定时更新');
  if (currentSession === 'closed') return t('休市');
  if (quote.freshness === 'stale') return t('暂无新成交 · 最后报价');
  if (quote.freshness === 'missing') return t('等待报价');
  if (quote.freshness === 'snapshot' || quote.subscription_status !== 'live') return t('定时更新');
  return currentSession === 'premarket' ? t('盘前实时') : currentSession === 'postmarket' ? t('盘后实时') : t('实时');
}

/** Missing placeholders keep the existing page quote. After eviction, newer
 * periodic data wins; while live, an older page snapshot cannot rewind trades. */
export function preferLiveQuote(quote: LiveQuote | undefined, hasFallback: boolean, fallbackAt?: string | null): boolean {
  if (quote?.price == null || !Number.isFinite(quote.price) || quote.price <= 0) return false;
  const tradeAt = reliableTimestamp(quote.trade_at);
  if (!tradeAt) return false;
  if (!hasFallback) return true;
  if (fallbackAt && DATE_ONLY.test(fallbackAt.trim())) {
    const fallbackDay = visibleQuoteDate(fallbackAt);
    if (fallbackDay && fallbackDay > quoteDateFormat.format(Date.now())) return true;
    // A daily bar has a trading date, not a reliable instant. Same-day quotes
    // cannot be ordered against it, including half-days and daylight savings.
    return Boolean(fallbackDay && quoteDateFormat.format(tradeAt) > fallbackDay);
  }
  if (fallbackAt && timestamp(fallbackAt) > Date.now() + MAX_CLOCK_SKEW_MS) return true;
  const fallbackTime = fallbackAt ? reliableTimestamp(fallbackAt) : 0;
  if (fallbackTime) return tradeAt >= fallbackTime;
  if (quote.subscription_status === 'live' && (quote.freshness === 'live' || (quote.freshness === 'stale' && !fallbackAt))) return true;
  // A disconnected stream retains its subscription, not its authority over a
  // newer periodic quote. Keep the last trade only while the fallback is older.
  return false;
}

export type FallbackQuoteKind = 'reference' | 'scan';
export function fallbackQuoteLabel(fallbackAt?: string | null, fallbackKind: FallbackQuoteKind = 'scan'): string {
  if (fallbackKind === 'reference') return t('参考价');
  if (!fallbackAt) return t('扫描价');
  if (DATE_ONLY.test(fallbackAt.trim())) return t('扫描价 · 日线');
  return t('扫描价');
}

/** The label describes the price actually rendered, not a superseded cache. */
export function delayedSessionLabel(session?: LiveQuote['session'] | null): string {
  if (session === 'closed') return t('休市');
  const label = session === 'premarket' ? t('盘前') : session === 'postmarket' ? t('盘后') : t('盘中');
  return t('{label} · 延迟 15 分钟', { label });
}

export function displayedQuoteLabel(
  quote: LiveQuote,
  status: QuoteStatus,
  usesLive: boolean,
  fallbackAt?: string | null,
  fallbackKind: FallbackQuoteKind = 'reference',
): string {
  // Scan fallback is the price on screen; limited only describes a delayed quote.
  if (!usesLive && fallbackKind === 'scan') return fallbackQuoteLabel(fallbackAt, fallbackKind);
  if (quote.subscription_status === 'limited') {
    return delayedSessionLabel(status.market_session ?? quote.session);
  }
  if (!usesLive) return fallbackQuoteLabel(fallbackAt, fallbackKind);
  if (!preferLiveQuote(quote, false)) return t('等待报价');
  if (!status.connected && quote.subscription_status === 'live') return t('行情重连中');
  return quoteLabel(quote, status.market_session);
}
