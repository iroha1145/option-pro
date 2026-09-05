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
const INITIAL_STATUS: QuoteStatus = { enabled: false, configured: false, public_enabled: false, connected: false, connection_status: 'disabled' };
export const MARKET_FUNDS = ['SPY', 'QQQ', 'DIA', 'IWM'];
export function normalizeQuoteSymbols(symbols: readonly string[]): string[] {
  return [...new Set(symbols.map(s => s.trim().toUpperCase()).filter(s => /^[A-Z][A-Z0-9]{0,9}(?:[.-][A-Z0-9]{1,4})?$/.test(s)))];
}
const timestamp = (value: string | null) => value ? Date.parse(value) || 0 : 0;
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
  private consumers = new Map<symbol, { symbols: string[]; focus: string[] }>();
  private status = INITIAL_STATUS;
  private stream: Stream | null = null;
  private controller: AbortController | null = null;
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
    for (const event of data.events) {
      const id = String(event.event_id ?? '');
      if (!id || Number(event.state_version ?? 0) <= Number(this.radarEvents.get(id)?.state_version ?? -1)) continue;
      changed = true; this.radarEvents.set(id, event); this.radarEventListeners.get(id)?.forEach(fn => fn());
    }
    if (changed) { this.radarVersion++; this.radarVersionListeners.forEach(fn => fn()); }
    this.radarListeners.forEach(fn => fn(data));
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
    this.schedule();
    return () => { this.consumers.delete(id); this.schedule(); };
  }
  start(owner: boolean) {
    this.stop(); this.started = true; this.owner = owner; this.terminal = false; this.failures = 0;
    this.pollTimer = setInterval(() => { if (this.permitted && this.visible) void this.snapshot(this.generation).catch(() => this.markDisconnected()); }, 60_000);
    this.schedule(0);
    return () => this.stop();
  }
  stop() {
    this.started = false; this.permitted = false; this.generation++;
    this.closeStream(); this.controller?.abort(); this.controller = null;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    if (this.pollTimer) clearInterval(this.pollTimer);
    if (this.flushTimer) clearTimeout(this.flushTimer);
    this.reconnectTimer = this.pollTimer = this.flushTimer = null;
    this.pending.clear(); this.clearQuotes(); this.setStatus(INITIAL_STATUS);
  }
  setVisible(visible: boolean) {
    this.visible = visible;
    if (!visible) { this.generation++; this.closeStream(); this.controller?.abort(); this.markDisconnected(); }
    else this.schedule(0);
  }
  private clearQuotes() {
    const ids = [...this.radarEvents.keys()]; this.radarEvents.clear();
    for (const id of ids) this.radarEventListeners.get(id)?.forEach(fn => fn());
    if (ids.length) { this.radarVersion++; this.radarVersionListeners.forEach(fn => fn()); }
    const symbols = [...this.quotes.keys()]; this.quotes.clear();
    for (const symbol of symbols) this.listeners.get(symbol)?.forEach(fn => fn());
  }
  private setStatus(status: QuoteStatus) {
    if (JSON.stringify(status) === JSON.stringify(this.status)) return;
    this.status = status; this.statusListeners.forEach(fn => fn());
  }
  private symbols() {
    const rows = [...this.consumers.values()];
    const all = normalizeQuoteSymbols(rows.flatMap(row => row.symbols));
    const focus = normalizeQuoteSymbols(rows.flatMap(row => row.focus)).filter(symbol => all.includes(symbol));
    const ordered = normalizeQuoteSymbols([...MARKET_FUNDS.filter(symbol => all.includes(symbol)), ...focus, ...all]);
    const symbols = ordered.slice(0, 200);
    return { symbols, focus: focus.filter(symbol => symbols.includes(symbol)), omitted: ordered.slice(200) };
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
  private ingestStatus(status: QuoteStatus) {
    this.setStatus(status);
    if (status.resync_required) this.radarListeners.forEach(fn => fn({ events: [], resync_required: true }));
    if (!this.accepts(status)) {
      this.terminal = true; this.permitted = false; this.closeStream(); this.pending.clear(); this.clearQuotes();
    }
  }
  private async snapshot(generation: number, probe = false) {
    this.controller?.abort(); const controller = new AbortController(); this.controller = controller;
    const timeout = setTimeout(() => controller.abort(), 10_000);
    try {
      const response = await this.runtime.fetch(`/api/quotes${probe ? '' : `?${this.query()}`}`, { credentials: 'include', signal: controller.signal, cache: 'no-store' });
      if (generation !== this.generation) return false;
      if ([401, 403, 404].includes(response.status)) {
        this.terminal = true; this.permitted = false; this.closeStream(); this.pending.clear(); this.clearQuotes();
        this.setStatus({ ...INITIAL_STATUS, connection_status: 'unavailable' }); return false;
      }
      if (!response.ok) throw new Error('Quote snapshot unavailable');
      const data = await response.json() as QuoteEnvelope;
      if (generation !== this.generation || !this.visible || !this.started) return false;
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
      const read = <T,>(callback: (data: T) => void) => (event: Event) => {
        if (generation !== this.generation || this.stream !== stream) return;
        try { callback(JSON.parse((event as MessageEvent).data) as T); } catch { /* A malformed event does not erase the last quote. */ }
      };
      stream.addEventListener('quotes', read<QuoteEnvelope>(data => { if (data.status) this.ingestStatus(data.status); if (!this.terminal) this.ingest(data.quotes); this.failures = 0; }));
      stream.addEventListener('status', read<QuoteStatus>(data => this.ingestStatus(data)));
      stream.addEventListener('radar', read<RadarUpdate>(data => this.ingestRadar(data)));
      if (!probe) this.radarListeners.forEach(fn => fn({ events: [], resync_required: true }));
      stream.onerror = () => {
        if (this.stream !== stream || generation !== this.generation) return;
        this.closeStream(); this.markDisconnected(); this.schedule(Math.min(30_000, 2_000 * 2 ** this.failures++));
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
      const previous = this.pending.get(symbol) ?? this.quotes.get(symbol);
      // Subscription state may change with an older REST price; keep newest price fields.
      const quote = previous && (timestamp(raw.trade_at) < timestamp(previous.trade_at) || (kind === 'snapshot' && previous.price != null && timestamp(raw.trade_at) === timestamp(previous.trade_at)))
        ? { ...previous, subscription_status: raw.subscription_status, freshness: raw.subscription_status === 'live' && raw.freshness === 'snapshot' ? previous.freshness : raw.freshness }
        : { ...raw, symbol };
      this.pending.set(symbol, quote);
    }
    if (!this.flushTimer) this.flushTimer = setTimeout(() => {
      this.flushTimer = null;
      for (const [symbol, quote] of this.pending) {
        if (JSON.stringify(this.quotes.get(symbol)) === JSON.stringify(quote)) continue;
        this.quotes.set(symbol, quote); this.listeners.get(symbol)?.forEach(fn => fn());
      }
      this.pending.clear();
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
  if (!hasFallback) return true;
  if (quote.subscription_status === 'live' && quote.freshness !== 'snapshot') return true;
  return Boolean(fallbackAt && timestamp(quote.trade_at) > timestamp(fallbackAt));
}
