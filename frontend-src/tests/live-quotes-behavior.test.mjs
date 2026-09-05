import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const enabled = { enabled: true, configured: true, public_enabled: true, allowed: true, connected: true, connection_status: 'connected', market_session: 'regular' };
const quote = (symbol, price, seconds = 0, extra = {}) => ({ symbol, price, previous_close: 99, change: price - 99, change_pct: (price / 99 - 1) * 100, trade_at: new Date(Date.UTC(2026, 8, 4, 14, 30, seconds)).toISOString(), received_at: new Date(Date.UTC(2026, 8, 4, 14, 30, seconds)).toISOString(), session: 'regular', source: 'finnhub', freshness: 'live', subscription_status: 'live', ...extra });

function harness() {
  let now = 0; let serial = 0;
  const timers = new Map(); const intervals = new Set(); const streams = []; const requests = [];
  const schedule = (fn, delay) => { const id = ++serial; timers.set(id, { fn, at: now + delay }); return id; };
  const clear = id => { timers.delete(id); intervals.delete(id); };
  const exports = {};
  const context = vm.createContext({ exports, require: () => ({ t: text => text }), AbortController, URLSearchParams, console,
    setTimeout: schedule, clearTimeout: clear,
    setInterval(fn, delay) { const id = ++serial; intervals.add(id); const repeat = () => { fn(); if (intervals.has(id)) timers.set(id, { fn: repeat, at: now + delay }); }; timers.set(id, { fn: repeat, at: now + delay }); return id; },
    clearInterval: clear,
  });
  vm.runInContext(ts.transpileModule(fs.readFileSync(new URL('../src/lib/liveQuotes.ts', import.meta.url), 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText, context);
  let responder = async url => ({ status: 200, body: { quotes: (new URL(url, 'https://local').searchParams.get('symbols') ?? '').split(',').filter(Boolean).map(symbol => quote(symbol, 100)), status: enabled } });
  const store = new exports.QuoteStore({
    async fetch(url, init) {
      requests.push({ url, init }); const response = await responder(url, init);
      return { ok: response.status === 200, status: response.status, json: async () => {
        if (response.jsonError) throw new SyntaxError('Invalid proxy JSON');
        return response.body;
      } };
    },
    stream(url) {
      const listeners = new Map();
      const stream = { url, closed: false, onerror: null, addEventListener(type, fn) { listeners.set(type, fn); }, close() { this.closed = true; }, emit(type, data) { listeners.get(type)?.({ data: JSON.stringify(data) }); } };
      streams.push(stream); return stream;
    },
  });
  const settle = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
  async function tick(ms) {
    const target = now + ms; let guard = 0;
    while (true) {
      await settle(); const entry = [...timers.entries()].sort((a, b) => a[1].at - b[1].at).find(([, row]) => row.at <= target);
      if (!entry) break;
      assert.ok(++guard < 1000, 'timers must not busy loop');
      const [id, row] = entry; now = row.at; timers.delete(id); row.fn();
    }
    now = target; await settle();
  }
  return { store, streams, requests, tick, respond(fn) { responder = fn; }, ...exports };
}

test('whole-page consumers share one stream, deduplicate symbols and release on cleanup', async () => {
  const h = harness();
  const dropA = h.store.register([' aapl ', 'MSFT'], ['aapl']);
  h.store.register(['AAPL', 'NVDA']); h.store.start(false);
  await h.tick(1000);
  assert.equal(h.streams.length, 1);
  let url = new URL(h.streams[0].url, 'https://local');
  assert.equal(url.searchParams.get('symbols'), 'AAPL,MSFT,NVDA');
  assert.equal(url.searchParams.get('focus'), 'AAPL');
  assert.equal(h.requests[0].url, '/api/quotes', 'permission is probed before requesting prices');
  assert.equal(h.requests[0].init.credentials, 'include');
  dropA(); await h.tick(100);
  assert.equal(h.streams[0].closed, true);
  url = new URL(h.streams.at(-1).url, 'https://local');
  assert.equal(url.searchParams.get('symbols'), 'AAPL,NVDA');
  assert.equal(url.searchParams.get('focus'), '');
  assert.equal(h.streams.filter(s => !s.closed).length, 1);
  h.store.stop();
});

test('trade bursts notify only affected symbols once per 250 ms and ignore older REST prices', async () => {
  const h = harness(); h.store.register(['AAPL', 'MSFT']); h.store.start(false); await h.tick(1000);
  let a = 0; let m = 0;
  h.store.subscribe('AAPL', () => a++); h.store.subscribe('MSFT', () => m++);
  const stream = h.streams[0];
  stream.emit('quotes', { quotes: [quote('AAPL', 101, 1)] });
  stream.emit('quotes', { quotes: [quote('AAPL', 102, 2)] });
  await h.tick(249); assert.equal(a, 0); assert.equal(h.store.getQuote('AAPL').price, 100);
  await h.tick(1); assert.equal(a, 1); assert.equal(m, 0); assert.equal(h.store.getQuote('AAPL').price, 102);
  stream.emit('quotes', { quotes: [quote('AAPL', 102, 2)] }); await h.tick(250); assert.equal(a, 1, 'identical messages do not replay');
  await h.tick(60_000); assert.equal(h.store.getQuote('AAPL').price, 102, 'older polling snapshot cannot roll price back');
  h.store.stop();
});

test('hidden page closes immediately, rejects queued events, then restores from snapshot', async () => {
  const h = harness(); h.store.register(['AAPL']); h.store.start(false); await h.tick(1000);
  const oldStream = h.streams[0]; h.store.setVisible(false); assert.equal(oldStream.closed, true);
  oldStream.emit('quotes', { quotes: [quote('AAPL', 999, 59)] }); await h.tick(30_000);
  assert.equal(h.streams.length, 1); assert.equal(h.store.getQuote('AAPL').price, 100);
  h.store.setVisible(true); await h.tick(1000);
  assert.equal(h.streams.length, 2); assert.equal(h.store.getQuote('AAPL').price, 100);
  h.store.stop();
});

test('disabled, unconfigured and unauthorized accounts do not reconnect or retain quotes', async () => {
  for (const status of [{ ...enabled, enabled: false }, { ...enabled, configured: false }, { ...enabled, allowed: false }]) {
    const h = harness(); h.respond(async () => ({ status: 200, body: { quotes: [quote('AAPL', 123)], status } }));
    h.store.register(['AAPL']); h.store.start(false); await h.tick(120_000);
    assert.equal(h.streams.length, 0); assert.equal(h.requests.length, 1); assert.equal(h.store.getQuote('AAPL'), undefined); h.store.stop();
  }
  for (const status of [401, 403, 404]) {
    const h = harness(); h.respond(async () => ({ status })); h.store.start(false); await h.tick(120_000);
    assert.equal(h.requests.length, 1); assert.equal(h.streams.length, 0); h.store.stop();
  }
});

test('malformed quote probes always expose a safe status to page subscribers', async () => {
  const malformed = [
    {}, null, [],
    { quotes: [] },
    { quotes: [], status: null },
    { quotes: [], status: {} },
    { quotes: [], status: false },
    { quotes: [], status: { ...enabled, enabled: 'true' } },
    { quotes: [], status: { ...enabled, configured: null } },
    { quotes: [], status: { ...enabled, allowed: 'true' } },
    { quotes: [], status: { ...enabled, market_session: ['regular'] } },
    { status: enabled },
    { quotes: null, status: enabled },
    { quotes: {}, status: enabled },
    { quotes: 'unavailable', status: enabled },
    { quotes: [null], status: enabled },
    { quotes: [{ symbol: 'AAPL' }], status: enabled },
    { quotes: [quote('AAPL', 100, 0, { change: 'bad' })], status: enabled },
  ];
  for (const body of malformed) {
    const h = harness(); const notifications = [];
    // Read enabled exactly as IndexTape does while responding to a store
    // notification; setStatus(undefined) previously broke this render path.
    h.store.subscribeStatus(() => notifications.push(h.store.getStatus().enabled));
    h.respond(async () => ({ status: 200, body }));
    h.store.register(['AAPL']); h.store.start(false); await h.tick(120_000);
    assert.equal(h.store.getStatus().enabled, false, JSON.stringify(body));
    assert.equal(h.store.getStatus().configured, false);
    assert.equal(h.store.getStatus().connected, false);
    assert.equal(h.store.getStatus().connection_status, 'unavailable');
    assert.ok(notifications.every(value => typeof value === 'boolean'));
    assert.equal(h.store.getQuote('AAPL'), undefined);
    assert.equal(h.streams.length, 0);
    assert.ok(h.requests.length > 1 && h.requests.length < 12, 'malformed responses retry with bounded backoff');
    h.store.stop();
  }
});

test('malformed initial price snapshot falls back after a valid permission probe', async () => {
  for (const body of [{}, null, { quotes: [], status: 'bad' }, { status: enabled }]) {
    const h = harness(); let count = 0;
    h.respond(async () => ({ status: 200, body: ++count === 2 ? body : { quotes: [quote('AAPL', 100)], status: enabled } }));
    h.store.register(['AAPL']); h.store.start(false); await h.tick(1000);
    assert.equal(h.requests.length, 2);
    assert.equal(h.streams.length, 0);
    assert.equal(h.store.getStatus().enabled, false);
    assert.equal(h.store.getQuote('AAPL'), undefined);
    await h.tick(3000);
    assert.equal(h.requests.length, 4, 'recovery probes permission again before loading prices');
    assert.equal(h.streams.length, 1);
    assert.equal(h.store.getStatus().enabled, true);
    assert.equal(h.store.getQuote('AAPL').price, 100);
    h.store.stop();
  }
});

test('invalid JSON responses recover through the same permission probe without refreshing the page', async () => {
  const h = harness(); let count = 0;
  h.respond(async () => ++count === 1
    ? { status: 200, jsonError: true }
    : { status: 200, body: { quotes: [quote('AAPL', 100)], status: enabled } });
  h.store.register(['AAPL']); h.store.start(false); await h.tick(1000);
  assert.equal(h.store.getStatus().enabled, false);
  assert.equal(h.store.getStatus().connection_status, 'unavailable');
  assert.equal(h.streams.length, 0);
  await h.tick(3000);
  assert.equal(h.requests.length, 3);
  assert.equal(h.store.getStatus().enabled, true);
  assert.equal(h.store.getQuote('AAPL').price, 100);
  assert.equal(h.streams.length, 1);
  h.store.stop();
});

test('malformed polling snapshots close live delivery and preserve the page fallback', async () => {
  const h = harness(); h.store.register(['AAPL']); h.store.start(false); await h.tick(1000);
  assert.equal(h.store.getQuote('AAPL').price, 100);
  const stream = h.streams[0];
  // A scheduled fetch must discard pending live batches as well as rendered
  // quotes so the page resumes its ordinary timed price source.
  h.respond(async () => ({ status: 200, body: {} }));
  await h.tick(59_000);
  stream.emit('quotes', { quotes: [quote('AAPL', 999, 50)] }); await h.tick(1000);
  assert.equal(stream.closed, true);
  assert.equal(h.store.getStatus().enabled, false);
  assert.equal(h.store.getQuote('AAPL'), undefined);
  assert.equal(h.streams.length, 1);
  h.respond(async () => ({ status: 200, body: { quotes: [quote('AAPL', 101, 1)], status: enabled } }));
  await h.tick(3000);
  assert.equal(h.store.getStatus().enabled, true);
  assert.equal(h.store.getQuote('AAPL').price, 101);
  assert.equal(h.streams.length, 2, 'a temporary malformed polling response must recover without a page refresh');
  h.store.stop();
});

test('invalid streamed status never poisons a valid store or replaces its last quote', async () => {
  const h = harness(); h.store.register(['AAPL']); h.store.start(false); await h.tick(1000);
  const stream = h.streams[0];
  for (const status of [null, {}, false, [], { ...enabled, enabled: 'yes' }]) {
    assert.doesNotThrow(() => stream.emit('status', status));
    assert.doesNotThrow(() => stream.emit('quotes', { quotes: [quote('AAPL', 999, 50)], status }));
  }
  stream.emit('quotes', null); stream.emit('quotes', {});
  await h.tick(250);
  assert.equal(h.store.getStatus().enabled, true);
  assert.equal(h.store.getStatus().configured, true);
  assert.equal(h.store.getQuote('AAPL').price, 100);
  assert.equal(stream.closed, false);
  stream.emit('quotes', { quotes: [quote('AAPL', 101, 1)] }); await h.tick(250);
  assert.equal(h.store.getQuote('AAPL').price, 101, 'valid quote-only stream messages remain supported');
  h.store.stop();
});

test('session expiration while a batch is pending cannot publish private prices again', async () => {
  const h = harness(); h.store.register(['AAPL']); h.store.start(true); await h.tick(1000);
  const stream = h.streams[0]; stream.emit('quotes', { quotes: [quote('AAPL', 105, 3)] });
  stream.emit('status', { ...enabled, allowed: false }); await h.tick(10_000);
  assert.equal(stream.closed, true); assert.equal(h.store.getQuote('AAPL'), undefined); assert.equal(h.streams.length, 1); h.store.stop();
});

test('stream reconnect rechecks access and keeps only one live connection', async () => {
  const h = harness(); h.store.register(['AAPL']); h.store.start(false); await h.tick(1000);
  h.streams[0].onerror(); assert.equal(h.streams[0].closed, true);
  h.respond(async () => ({ status: 403 })); await h.tick(60_000);
  assert.equal(h.streams.length, 1); assert.equal(h.store.getQuote('AAPL'), undefined); h.store.stop();
});

test('limited symbols remain visible and quote session is separate from current market session', async () => {
  const h = harness(); h.store.register(['AAPL']); h.store.start(false); await h.tick(1000);
  const limited = quote('AAPL', 101, 1, { subscription_status: 'limited', freshness: 'snapshot' });
  h.streams[0].emit('quotes', { quotes: [limited] }); await h.tick(250);
  assert.equal(h.store.getQuote('AAPL').price, 101); assert.equal(h.quoteLabel(limited), '定时更新');
  assert.equal(h.quoteLabel(quote('AAPL', 101), 'closed'), '休市');
  assert.equal(h.quoteLabel(quote('AAPL', 101, 1, { freshness: 'stale' })), '暂无新成交 · 最后报价');
  h.store.stop();
});

test('radar updates are independent of quotes, versioned and resync-aware', async () => {
  const h = harness(); h.store.register(['AAPL']); h.store.start(false); await h.tick(1000);
  let changes = 0; let resyncs = 0;
  h.store.subscribeRadarEvent('event-1', () => changes++);
  h.store.subscribeRadar(update => { if (update.resync_required) resyncs++; });
  h.streams[0].emit('quotes', { quotes: [quote('AAPL', 200, 3)] }); await h.tick(250);
  assert.equal(h.store.getRadarEvent('event-1'), undefined, 'price alone never confirms a signal');
  const event = { event_id: 'event-1', state_version: 2, lifecycle_state: 'TRIGGERED', trigger_source: 'finnhub' };
  h.streams[0].emit('radar', { events: [event] });
  h.streams[0].emit('radar', { events: [{ ...event, state_version: 1, lifecycle_state: 'WATCHING' }, event] });
  assert.equal(changes, 1); assert.equal(h.store.getRadarEvent('event-1').lifecycle_state, 'TRIGGERED');
  h.streams[0].emit('status', { ...enabled, resync_required: true }); assert.equal(resyncs, 1);
  h.store.stop(); assert.equal(h.store.getRadarEvent('event-1'), undefined);
});

test('more than 200 page symbols retains funds and focus; omitted rows get timed fallback', async () => {
  const h = harness(); const symbols = Array.from({ length: 240 }, (_, i) => `S${i}`);
  h.store.register(symbols, ['S239']); h.store.register(['SPY', 'QQQ', 'DIA', 'IWM']); h.store.start(false); await h.tick(1000);
  const url = new URL(h.streams[0].url, 'https://local'); const sent = url.searchParams.get('symbols').split(',');
  assert.equal(sent.length, 200); assert.deepEqual(sent.slice(0, 5), ['SPY', 'QQQ', 'DIA', 'IWM', 'S239']);
  assert.equal(url.searchParams.get('focus'), 'S239'); assert.equal(h.store.getQuote('S238').subscription_status, 'limited');
  h.store.stop();
});

test('pending quotes keep price and change fallbacks; evicted symbols accept newer periodic data', () => {
  const h = harness();
  assert.equal(h.preferLiveQuote(quote('AAPL', null, 0, { freshness: 'missing', subscription_status: 'pending' }), true), false);
  assert.equal(h.quoteLabel(quote('AAPL', null, 0, { freshness: 'missing', subscription_status: 'unavailable' })), '暂无实时行情 · 定时更新');
  const evicted = quote('AAPL', 105, 10, { freshness: 'snapshot', subscription_status: 'limited' });
  assert.equal(h.preferLiveQuote(evicted, true, quote('AAPL', 100, 0).trade_at), true, 'a known older page snapshot does not rewind the last price');
  assert.equal(h.preferLiveQuote(evicted, true, quote('AAPL', 108, 20).trade_at), false, 'new periodic snapshots replace an evicted cached quote');
  assert.equal(h.preferLiveQuote(evicted, true), false, 'untimestamped periodic lists remain able to refresh after eviction');
  assert.equal(h.preferLiveQuote(quote('AAPL', 105, 10), true), true, 'active live trades remain authoritative over untimestamped list data');
});

test('REST at the same trade timestamp cannot overwrite the newest streamed trade', async () => {
  const h = harness(); h.store.register(['AAPL']); h.store.start(false); await h.tick(1000);
  h.streams[0].emit('quotes', { quotes: [quote('AAPL', 106)] }); await h.tick(250); assert.equal(h.store.getQuote('AAPL').price, 106);
  await h.tick(60_000); assert.equal(h.store.getQuote('AAPL').price, 106); h.store.stop();
});

test('reconnecting a visible stream requests radar reconciliation immediately', async () => {
  const h = harness(); h.store.register(['AAPL']); h.store.start(false); await h.tick(1000);
  let resyncs = 0; h.store.subscribeRadar(update => { if (update.resync_required) resyncs++; });
  h.store.setVisible(false); h.store.setVisible(true); await h.tick(1000);
  assert.equal(resyncs, 1); h.store.stop();
});


test('an aborted polling request cannot change a stopped or restarted identity', async () => {
  const h = harness(); h.store.register(['AAPL']); h.store.start(false); await h.tick(1000);
  h.respond(async (_url, init) => new Promise((_resolve, reject) => {
    init.signal.addEventListener('abort', () => reject(new Error('aborted')));
  }));
  await h.tick(59_000); // Leave the regular minute poll pending.
  h.store.stop(); await h.tick(0);
  assert.equal(h.store.getStatus().connection_status, 'disabled');
  assert.equal(h.store.getQuote('AAPL'), undefined);
});

test('a disconnected stale trade yields to a newer timestamped page quote', () => {
  const h = harness();
  const stale = quote('AAPL', 105, 10, { freshness: 'stale' });
  assert.equal(h.preferLiveQuote(stale, true, quote('AAPL', 108, 20).trade_at), false);
  assert.equal(h.preferLiveQuote(stale, true, quote('AAPL', 100, 0).trade_at), true);
  assert.equal(h.preferLiveQuote(stale, true), true, 'an undated fallback cannot rewind a disconnected quote');
  assert.equal(h.preferLiveQuote(stale, false), true, 'retain the last quote when no fallback exists');
});

test('a malformed radar row cannot suppress valid revisions in the same frame', async () => {
  const h = harness(); h.store.register(['AAPL']); h.store.start(false); await h.tick(1000);
  h.streams[0].emit('radar', { events: [null, { event_id: 'bad', state_version: 'NaN' }, { event_id: 'ok', state_version: 1, lifecycle_state: 'TRIGGERED' }] });
  assert.equal(h.store.getRadarEvent('bad'), undefined);
  assert.equal(h.store.getRadarEvent('ok')?.state_version, 1);
  h.store.stop();
});


test('price labels distinguish a rendered fallback from the disconnected cached quote', () => {
  const h = harness(); const disconnected = { ...enabled, connected: false };
  const stale = quote('AAPL', 105, 10, { freshness: 'stale' });
  assert.equal(h.displayedQuoteLabel(stale, disconnected, false), '定时更新');
  assert.equal(h.displayedQuoteLabel(stale, disconnected, true), '行情重连中');
  assert.equal(h.displayedQuoteLabel(quote('AAPL', 105), enabled, true), '实时');
});
