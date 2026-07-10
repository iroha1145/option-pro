const API_BASE = '/api';

// ───────── Cache ─────────
// In-memory cache per API call. Cleared only when page reloads.
const cache = new Map();      // key → { data, ts }
const inflight = new Map();   // key → Promise (dedup concurrent requests)

// Hard cap on in-memory cache to prevent unbounded growth in long sessions.
const CACHE_MAX = 200;

function markClientCached(data) {
  if (!data || typeof data !== 'object' || Array.isArray(data)) return data;
  return { ...data, _client_cached: true };
}

function cached(key, ttl, fn) {
  const now = Date.now();
  const entry = cache.get(key);
  if (entry && now - entry.ts < ttl) return Promise.resolve(markClientCached(entry.data));
  if (inflight.has(key)) return inflight.get(key);
  const p = fn()
    .then(data => {
      // Simple LRU-ish: drop oldest when over cap
      if (cache.size >= CACHE_MAX) {
        const oldestKey = cache.keys().next().value;
        if (oldestKey) cache.delete(oldestKey);
      }
      cache.set(key, { data, ts: Date.now() });
      inflight.delete(key);
      return data;
    })
    .catch(err => { inflight.delete(key); throw err; });
  inflight.set(key, p);
  return p;
}

export function invalidateCache(prefix = '') {
  for (const k of cache.keys()) if (k.startsWith(prefix)) cache.delete(k);
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

async function fetchJson(url, init, timeoutMs = REQUEST_TIMEOUT_MS) {
  const controller = new AbortController();
  const tid = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const headers = getAuthHeaders(init?.headers);
    const response = await fetch(url, { ...init, headers, signal: controller.signal });
    if (!response.ok) {
      if (response.status === 401) {
        throw new Error('401 Unauthorized: set sessionStorage optix.app.token to your APP_AUTH_TOKEN');
      }
      throw new Error(`${response.status} ${response.statusText}`);
    }
    return await response.json();
  } catch (e) {
    if (e.name === 'AbortError') {
      throw new Error(`Request timeout (${timeoutMs / 1000}s): ${url}`);
    }
    throw e;
  } finally {
    clearTimeout(tid);
  }
}

function enc(ticker) { return encodeURIComponent(String(ticker).toUpperCase()); }

// TTL constants (ms)
const T = {
  PRICES:  60 * 1000,        // 1 min — live-ish
  CHART:   60 * 1000,        // 1 min
  SIGNALS: 5  * 60 * 1000,   // 5 min — daily data
  SLOW:    10 * 60 * 1000,   // 10 min — expensive endpoints
  STRENGTH: 15 * 60 * 1000,   // 15 min — full screener scan
  STATIC:  60 * 60 * 1000,   // 1 hour — rarely changes
  OPTION:  30 * 1000,        // 30 s — for chain updates
};

export const api = {
  // Live-ish data — short cache
  watchlist()           { return cached('wl', T.PRICES, () => fetchJson(`${API_BASE}/stocks/watchlist`)); },
  stock(ticker)         { return cached(`s:${enc(ticker)}`, T.PRICES, () => fetchJson(`${API_BASE}/stocks/${enc(ticker)}`)); },
  chart(ticker, range = '1d') { return cached(`c:${enc(ticker)}:${range}`, T.CHART, () => fetchJson(`${API_BASE}/stocks/${enc(ticker)}/chart?range=${encodeURIComponent(range)}`)); },
  search(q)             { return fetchJson(`${API_BASE}/stocks/search?q=${encodeURIComponent(q)}`); },

  // Signals — daily aggregation
  signals(ticker)       { return cached(`sig:${enc(ticker)}`, T.SIGNALS, () => fetchJson(`${API_BASE}/signals/stock/${enc(ticker)}`)); },
  topBottomSignals(ticker) { return cached(`tb:${enc(ticker)}`, T.SIGNALS, () => fetchJson(`${API_BASE}/signals/stock/${enc(ticker)}`)); },
  signalAI(ticker)      { return fetchJson(`${API_BASE}/signals/stock/${enc(ticker)}/ai-analysis`, { method:'POST', headers:{'Content-Type':'application/json'} }); },
  analyzeTopBottomSignals(ticker) { return fetchJson(`${API_BASE}/signals/stock/${enc(ticker)}/ai-analysis`, { method:'POST', headers:{'Content-Type':'application/json'} }); },

  // Options — cache, but short
  expirations(ticker)   { return cached(`exp:${enc(ticker)}`, T.SIGNALS, () => fetchJson(`${API_BASE}/options/${enc(ticker)}/expirations`)); },
  optionChain(ticker, exp) {
    const q = exp ? `?expiration=${encodeURIComponent(exp)}` : '';
    return cached(`oc:${enc(ticker)}:${exp || ''}`, T.OPTION, () => fetchJson(`${API_BASE}/options/${enc(ticker)}/chain${q}`));
  },

  // AI — never cached (user explicitly requests)
  analyzeAlerts(arg1, alerts = [], extra = {}) {
    const body = (typeof arg1 === 'object' && arg1 !== null && !Array.isArray(arg1))
      ? arg1
      : { ticker: String(arg1).toUpperCase(), alerts, ...extra };
    return fetchJson(`${API_BASE}/ai/analyze-alerts`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
  },

  // Sectors — slow endpoints, longer cache
  sectors()             { return cached('sectors', T.STATIC, () => fetchJson(`${API_BASE}/sectors`)); },
  sectorIV(sectorId)    { return cached(`siv:${sectorId}`, T.SLOW, () => fetchJson(`${API_BASE}/sectors/${encodeURIComponent(sectorId)}/iv-ranking`)); },
  sectorHeatmap(sectorId) { return cached(`shm:${sectorId}`, T.SLOW, () => fetchJson(`${API_BASE}/sectors/${encodeURIComponent(sectorId)}/heatmap`)); },

  // Status
  marketStatus()        { return cached('mkt', T.PRICES, () => fetchJson(`${API_BASE}/market/status`)); },
  marketIndices()       { return cached('mkt-idx', T.PRICES, () => fetchJson(`${API_BASE}/market/indices`)); },
  earnings()            { return cached('earn', T.STATIC, () => fetchJson(`${API_BASE}/earnings/upcoming`)); },

  earningsCorrelation() {
    return fetchJson(`${API_BASE}/ai/earnings-correlation`);
  },

  earningsImpact(ticker) {
    return cached(`earn-impact:${ticker}`, T.STATIC, () =>
      fetchJson(`${API_BASE}/ai/earnings-impact/${encodeURIComponent(ticker)}`));
  },

  strengthScan(params = {}) {
    const query = new URLSearchParams();
    const scanParams = { ...params };
    if (!scanParams.cache_ttl) scanParams.cache_ttl = 15 * 60;
    Object.entries(scanParams).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') query.set(key, value);
    });
    const suffix = query.toString() ? `?${query}` : '';
    return cached(`strength:${suffix}`, T.STRENGTH, () => fetchJson(`${API_BASE}/strength/scan${suffix}`, undefined, 180 * 1000));
  },

  strengthStock(ticker, profile = 'balanced') {
    return cached(`strength-stock:${enc(ticker)}:${profile}`, T.SLOW, () =>
      fetchJson(`${API_BASE}/strength/stocks/${enc(ticker)}?profile=${encodeURIComponent(profile)}`));
  },

  strengthSectors(period = '3mo') {
    return cached(`strength-sectors:${period}`, T.SLOW, () =>
      fetchJson(`${API_BASE}/strength/sectors?period=${encodeURIComponent(period)}`));
  },

  strengthProfiles() {
    return cached('strength-profiles', T.STATIC, () => fetchJson(`${API_BASE}/strength/profiles`));
  },
};

export function safe(p) {
  return p.then(d => d).catch(e => ({ __error: true, message: e.message }));
}
