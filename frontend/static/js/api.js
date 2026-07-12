const API_BASE = '/api';

// ───────── Cache ─────────
// In-memory cache per API call. Cleared only when page reloads.
const cache = new Map();      // key → { data, ts }
const inflight = new Map();   // key → Promise (dedup concurrent requests)
const latestCancellableRequest = new Map();
let cancellableRequestSequence = 0;

// Hard cap on in-memory cache to prevent unbounded growth in long sessions.
const CACHE_MAX = 200;

function markClientCached(data) {
  if (!data || typeof data !== 'object' || Array.isArray(data)) return data;
  return { ...data, _client_cached: true };
}

function remember(key, data) {
  if (cache.size >= CACHE_MAX) {
    const oldestKey = cache.keys().next().value;
    if (oldestKey) cache.delete(oldestKey);
  }
  cache.set(key, { data, ts: Date.now() });
  return data;
}

function createAbortError() {
  if (typeof DOMException === 'function') return new DOMException('Request aborted', 'AbortError');
  const error = new Error('Request aborted');
  error.name = 'AbortError';
  return error;
}

function cached(key, ttl, fn, signal = null) {
  const now = Date.now();
  const entry = cache.get(key);
  if (entry && now - entry.ts < ttl) return Promise.resolve(markClientCached(entry.data));
  if (signal?.aborted) return Promise.reject(createAbortError());

  // A page-scoped request must own its fetch so leaving that page can truly
  // abort network and server work. Shared, non-cancellable calls still use the
  // inflight map below for cross-component de-duplication.
  if (signal) {
    const requestId = ++cancellableRequestSequence;
    latestCancellableRequest.set(key, requestId);
    return fn(signal).then(
      (data) => {
        // Detail-page requests may overlap when the ticker, timeframe or
        // visibility changes quickly. Only the newest request may update the
        // shared cache; every caller still receives its own response.
        if (latestCancellableRequest.get(key) !== requestId) return data;
        latestCancellableRequest.delete(key);
        return remember(key, data);
      },
      (error) => {
        if (latestCancellableRequest.get(key) === requestId) latestCancellableRequest.delete(key);
        throw error;
      }
    );
  }

  if (inflight.has(key)) return inflight.get(key);
  const p = fn()
    .then(data => {
      inflight.delete(key);
      return remember(key, data);
    })
    .catch(err => { inflight.delete(key); throw err; });
  inflight.set(key, p);
  return p;
}

export function invalidateCache(key = '', options = {}) {
  if (!key) {
    cache.clear();
    return;
  }
  if (options.prefix === true) {
    for (const cachedKey of cache.keys()) {
      if (cachedKey.startsWith(key)) cache.delete(cachedKey);
    }
    return;
  }
  cache.delete(key);
}

// Hard request timeout — prevents inflight Map from getting clogged with
// dead promises if the server hangs (then subsequent same-key requests
// would await forever).
const REQUEST_TIMEOUT_MS = 90 * 1000;

function getAuthHeaders(initHeaders = {}) {
  const headers = new Headers(initHeaders || {});
  let token = '';
  try {
    token = sessionStorage.getItem('optix.app.token') || '';
  } catch (_) {
    // sessionStorage can be unavailable in some privacy modes.
  }
  try {
    // One-time migration from older builds. Keep the token for this tab only
    // and remove the persistent copy immediately.
    const legacyToken = localStorage.getItem('optix.app.token') || '';
    if (!token && legacyToken) {
      token = legacyToken;
      try { sessionStorage.setItem('optix.app.token', legacyToken); } catch (_) { /* tab storage unavailable */ }
    }
    if (legacyToken) localStorage.removeItem('optix.app.token');
  } catch (_) {
    // Legacy storage may be unavailable; the session token still works.
  }
  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  return headers;
}

async function fetchPayload(url, init, timeoutMs, readResponse) {
  const controller = new AbortController();
  const { signal: externalSignal, ...requestInit } = init || {};
  let timedOut = false;
  const abortFromPage = () => controller.abort();
  if (externalSignal?.aborted) controller.abort();
  else externalSignal?.addEventListener('abort', abortFromPage, { once: true });
  const tid = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  try {
    const headers = getAuthHeaders(requestInit.headers);
    const response = await fetch(url, { ...requestInit, headers, signal: controller.signal });
    if (!response.ok) {
      if (response.status === 401) {
        throw new Error('401 Unauthorized: set sessionStorage optix.app.token to your APP_AUTH_TOKEN');
      }
      throw new Error(`${response.status} ${response.statusText}`);
    }
    // Keep the timeout and external abort listener alive while the response
    // body is still downloading/decoding, not only until headers arrive.
    return await readResponse(response);
  } catch (e) {
    if (externalSignal?.aborted) throw createAbortError();
    if (e.name === 'AbortError' && timedOut) {
      throw new Error(`Request timeout (${timeoutMs / 1000}s): ${url}`);
    }
    throw e;
  } finally {
    clearTimeout(tid);
    externalSignal?.removeEventListener('abort', abortFromPage);
  }
}

async function fetchJson(url, init, timeoutMs = REQUEST_TIMEOUT_MS) {
  return fetchPayload(url, init, timeoutMs, (response) => response.json());
}

async function fetchBlob(url, init, timeoutMs = 15 * 1000) {
  return fetchPayload(url, init, timeoutMs, (response) => response.blob());
}

function enc(ticker) { return encodeURIComponent(String(ticker).toUpperCase()); }

const WATCHLIST_TICKER_PATTERN = /^(?:\^[A-Z0-9][A-Z0-9.^_=-]{0,30}|[A-Z0-9][A-Z0-9.^_=-]{0,31})$/;
const WATCHLIST_TICKER_LIMIT = 100;

function normalizeWatchlistTickers(values) {
  if (!Array.isArray(values)) return [];
  const tickers = [];
  const seen = new Set();
  for (const value of values) {
    const ticker = String(value || '').trim().toUpperCase();
    if (!WATCHLIST_TICKER_PATTERN.test(ticker)) {
      throw new Error(`Invalid watchlist ticker: ${ticker || '(empty)'}`);
    }
    if (seen.has(ticker)) continue;
    seen.add(ticker);
    tickers.push(ticker);
    if (tickers.length > WATCHLIST_TICKER_LIMIT) {
      throw new Error(`Watchlist supports at most ${WATCHLIST_TICKER_LIMIT} tickers`);
    }
  }
  return tickers;
}

// TTL constants (ms)
const T = {
  PRICES:  60 * 1000,        // 1 min — live-ish
  WATCHLIST: 5 * 60 * 1000,  // 5 min — aligned with the backend snapshot
  CHART:   60 * 1000,        // 1 min
  SIGNALS: 5  * 60 * 1000,   // 5 min — daily data
  SLOW:    10 * 60 * 1000,   // 10 min — expensive endpoints
  STRENGTH: 15 * 60 * 1000,   // 15 min — full screener scan
  BREAKOUT: 30 * 1000,        // 30 s — completed radar snapshots
  STATIC:  60 * 60 * 1000,   // 1 hour — rarely changes
  OPTION:  30 * 1000,        // 30 s — for chain updates
};

export const api = {
  // Live-ish data — short cache
  watchlist(options = {}) {
    const isTargeted = Array.isArray(options.tickers);
    const tickers = normalizeWatchlistTickers(options.tickers);
    if (isTargeted && tickers.length === 0) {
      return Promise.resolve({ groups: [], attempted: 0, succeeded: 0, source_status: 'empty' });
    }
    const cacheScope = tickers.length ? [...tickers].sort().join(',') : 'all';
    const query = tickers.length ? `?tickers=${encodeURIComponent(tickers.join(','))}` : '';
    return cached(`wl:${cacheScope}`, T.WATCHLIST, (signal) => fetchJson(`${API_BASE}/stocks/watchlist${query}`, { signal }), options.signal);
  },
  stock(ticker, options = {}) { return cached(`s:${enc(ticker)}`, T.PRICES, (signal) => fetchJson(`${API_BASE}/stocks/${enc(ticker)}`, { signal }), options.signal); },
  stockLogo(ticker, options = {}) { return fetchBlob(`${API_BASE}/stocks/${enc(ticker)}/logo`, { signal: options.signal }); },
  chart(ticker, range = '1d', options = {}) {
    const adjustment = options.adjustment === 'adjusted' ? 'adjusted' : 'raw';
    const query = new URLSearchParams({ range, adjustment });
    return cached(
      `c:${enc(ticker)}:${range}:${adjustment}`,
      T.CHART,
      (signal) => fetchJson(`${API_BASE}/stocks/${enc(ticker)}/chart?${query}`, { signal }),
      options.signal,
    );
  },
  search(q, options = {}) { return fetchJson(`${API_BASE}/stocks/search?q=${encodeURIComponent(q)}`, { signal: options.signal }); },

  // Signals — daily aggregation
  signals(ticker, options = {}) { return cached(`sig:${enc(ticker)}`, T.SIGNALS, (signal) => fetchJson(`${API_BASE}/signals/stock/${enc(ticker)}`, { signal }), options.signal); },
  topBottomSignals(ticker, options = {}) { return cached(`tb:${enc(ticker)}`, T.SIGNALS, (signal) => fetchJson(`${API_BASE}/signals/stock/${enc(ticker)}`, { signal }), options.signal); },
  signalsMarket(options = {}) {
    const key = 'signals-market';
    if (options.refresh) invalidateCache(key);
    return cached(
      key,
      T.SIGNALS,
      (signal) => fetchJson(`${API_BASE}/signals/market`, { signal }),
      options.signal,
    );
  },
  indexTechnicalSignals(ticker, options = {}) {
    const key = `index-signals:${enc(ticker)}`;
    if (options.refresh) invalidateCache(key);
    return cached(
      key,
      T.SIGNALS,
      (signal) => fetchJson(`${API_BASE}/stocks/${enc(ticker)}/signals`, { signal }),
      options.signal,
    );
  },
  signalAI(ticker, options = {}) { return fetchJson(`${API_BASE}/signals/stock/${enc(ticker)}/ai-analysis`, { method:'POST', headers:{'Content-Type':'application/json'}, signal: options.signal }); },
  analyzeTopBottomSignals(ticker, options = {}) { return fetchJson(`${API_BASE}/signals/stock/${enc(ticker)}/ai-analysis`, { method:'POST', headers:{'Content-Type':'application/json'}, signal: options.signal }); },

  // Options — cache, but short
  expirations(ticker, options = {}) { return cached(`exp:${enc(ticker)}`, T.SIGNALS, (signal) => fetchJson(`${API_BASE}/options/${enc(ticker)}/expirations`, { signal }), options.signal); },
  optionChain(ticker, exp, options = {}) {
    const q = exp ? `?expiration=${encodeURIComponent(exp)}` : '';
    return cached(`oc:${enc(ticker)}:${exp || ''}`, T.OPTION, (signal) => fetchJson(`${API_BASE}/options/${enc(ticker)}/chain${q}`, { signal }), options.signal);
  },

  // AI — never cached (user explicitly requests)
  analyzeAlerts(arg1, alertsOrOptions = [], extra = {}) {
    const objectPayload = typeof arg1 === 'object' && arg1 !== null && !Array.isArray(arg1);
    const options = objectPayload && !Array.isArray(alertsOrOptions) ? alertsOrOptions : {};
    const body = objectPayload
      ? arg1
      : { ticker: String(arg1).toUpperCase(), alerts: alertsOrOptions, ...extra };
    return fetchJson(`${API_BASE}/ai/analyze-alerts`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: options.signal,
    });
  },

  // Sectors — slow endpoints, longer cache
  sectors()             { return cached('sectors', T.STATIC, () => fetchJson(`${API_BASE}/sectors`)); },
  sectorIV(sectorId)    { return cached(`siv:${sectorId}`, T.SLOW, () => fetchJson(`${API_BASE}/sectors/${encodeURIComponent(sectorId)}/iv-ranking`)); },
  sectorHeatmap(sectorId) { return cached(`shm:${sectorId}`, T.SLOW, () => fetchJson(`${API_BASE}/sectors/${encodeURIComponent(sectorId)}/heatmap`)); },

  // Status
  marketStatus(options = {}) { return cached('mkt', T.PRICES, (signal) => fetchJson(`${API_BASE}/market/status`, { signal }), options.signal); },
  marketIndices()       { return cached('mkt-idx', T.PRICES, () => fetchJson(`${API_BASE}/market/indices`)); },
  earnings()            { return cached('earn', T.STATIC, () => fetchJson(`${API_BASE}/earnings/upcoming`)); },

  earningsCorrelation() {
    return fetchJson(`${API_BASE}/ai/earnings-correlation`);
  },

  earningsImpact(ticker) {
    return cached(`earn-impact:${ticker}`, T.STATIC, () =>
      fetchJson(`${API_BASE}/ai/earnings-impact/${encodeURIComponent(ticker)}`));
  },

  strengthScan(params = {}, options = {}) {
    const query = new URLSearchParams();
    const scanParams = { ...params };
    Object.entries(scanParams).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') query.set(key, value);
    });
    const suffix = query.toString() ? `?${query}` : '';
    return cached(
      `strength:${suffix}`,
      T.STRENGTH,
      (signal) => fetchJson(`${API_BASE}/strength/scan${suffix}`, { signal }, 180 * 1000),
      options.signal,
    );
  },

  strengthMarket(options = {}) {
    const key = 'strength-market';
    if (options.refresh) invalidateCache(key);
    return cached(
      key,
      T.STRENGTH,
      (signal) => fetchJson(`${API_BASE}/strength/market`, { signal }, 180 * 1000),
      options.signal,
    );
  },

  strengthStock(ticker, profile = 'balanced') {
    return cached(`strength-stock:${enc(ticker)}:${profile}`, T.SLOW, () =>
      fetchJson(`${API_BASE}/strength/stocks/${enc(ticker)}?profile=${encodeURIComponent(profile)}`));
  },

  strengthSectors(period = '3mo') {
    return cached(`strength-sectors:${period}`, T.SLOW, () =>
      fetchJson(`${API_BASE}/strength/sectors?period=${encodeURIComponent(period)}`));
  },

  strengthProfiles(options = {}) {
    return cached(
      'strength-profiles',
      T.STATIC,
      (signal) => fetchJson(`${API_BASE}/strength/profiles`, { signal }),
      options.signal,
    );
  },

  // Breakout Radar — read-only completed snapshots. The worker owns scanning.
  breakoutStatus(options = {}) {
    const key = 'breakouts:status';
    if (options.refresh) invalidateCache(key);
    return cached(
      key,
      T.BREAKOUT,
      (signal) => fetchJson(`${API_BASE}/breakouts/status`, { signal }),
      options.signal,
    );
  },

  breakoutCurrent(options = {}) {
    const key = 'breakouts:current';
    if (options.refresh) invalidateCache(key);
    return cached(
      key,
      T.BREAKOUT,
      (signal) => fetchJson(`${API_BASE}/breakouts/current`, { signal }),
      options.signal,
    );
  },

  breakoutEvents(params = {}, options = {}) {
    const query = new URLSearchParams();
    const allowed = [
      'date', 'ticker', 'setup_type', 'lifecycle_state', 'session',
      'min_priority', 'limit', 'cursor',
    ];
    allowed.forEach((key) => {
      const value = params[key];
      if (value !== undefined && value !== null && value !== '') query.set(key, String(value));
    });
    const suffix = query.toString();
    const key = `breakouts:events:${suffix}`;
    if (options.refresh) invalidateCache(key);
    return cached(
      key,
      T.BREAKOUT,
      (signal) => fetchJson(`${API_BASE}/breakouts/events${suffix ? `?${suffix}` : ''}`, { signal }),
      options.signal,
    );
  },

  breakoutEvent(eventId, options = {}) {
    const id = encodeURIComponent(String(eventId || ''));
    const key = `breakouts:event:${id}`;
    if (options.refresh) invalidateCache(key);
    return cached(
      key,
      T.PRICES,
      (signal) => fetchJson(`${API_BASE}/breakouts/events/${id}`, { signal }),
      options.signal,
    );
  },

  breakoutTicker(ticker, options = {}) {
    const symbol = enc(ticker);
    const key = `breakouts:ticker:${symbol}`;
    if (options.refresh) invalidateCache(key);
    return cached(
      key,
      T.PRICES,
      (signal) => fetchJson(`${API_BASE}/breakouts/tickers/${symbol}`, { signal }),
      options.signal,
    );
  },
};

export function safe(p) {
  return p.then(d => d).catch(e => ({ __error: true, message: e.message }));
}
