import assert from 'node:assert/strict';
import test from 'node:test';

import { api, invalidateCache } from '../static/js/api.js';
import {
  earningsImpactErrorMessage,
  formatEarningsEps,
  formatEarningsMoney,
} from '../static/js/pages/earnings.js';
import { earningsCoverage, earningsRefreshNotice } from '../static/js/utils/frontendState.js';

test('earnings coverage exposes exact degraded X/Y counts', () => {
  assert.deepEqual(earningsCoverage({ attempted: 67, succeeded: 63, source_status: 'degraded' }), {
    attempted: 67,
    succeeded: 63,
    degraded: true,
  });
  assert.equal(earningsCoverage({ attempted: 67, succeeded: 67, source_status: 'active' }).degraded, false);
});

test('missing earnings estimates stay missing while real zero remains visible', () => {
  assert.equal(formatEarningsEps(null), '—');
  assert.equal(formatEarningsEps(undefined), '—');
  assert.equal(formatEarningsEps(''), '—');
  assert.equal(formatEarningsEps(0), '$0.00');
  assert.equal(formatEarningsMoney(null), '—');
  assert.equal(formatEarningsMoney(0), '$0');
});

test('soft AI impact failures cannot be presented as a loaded result', () => {
  assert.equal(earningsImpactErrorMessage({ summary: 'ok' }), '');
  assert.match(
    earningsImpactErrorMessage({ error: 'ai_busy' }),
    /繁忙/,
  );
  assert.match(
    earningsImpactErrorMessage({ error: 'ai_unavailable' }),
    /暂不可用/,
  );
});

test('HTTP 200 stale fallback and cooldown responses keep data but expose a visible warning state', () => {
  const retainedPayload = {
    earnings: [{ ticker: 'AAPL', earnings_date: '2026-07-20' }],
    attempted: 67,
    succeeded: 67,
    source_status: 'stale',
    refresh_status: 'failed_stale',
    refresh_retry_after_seconds: 60,
  };
  const failed = earningsRefreshNotice(retainedPayload);
  assert.equal(retainedPayload.earnings.length, 1);
  assert.equal(failed.retained, true);
  assert.equal(failed.tone, 'error');
  assert.match(failed.title, /继续显示上次数据/);
  assert.equal(failed.retryAfterSeconds, 60);

  const cooldown = earningsRefreshNotice({
    ...retainedPayload,
    source_status: 'active',
    refresh_status: 'cooldown',
    refresh_retry_after_seconds: 27.2,
  });
  assert.equal(cooldown.retained, true);
  assert.equal(cooldown.tone, 'warning');
  assert.match(cooldown.title, /继续显示现有数据/);
  assert.equal(cooldown.retryAfterSeconds, 28);
});

test('explicit API refresh bypasses the browser-memory earnings cache', async (t) => {
  const originalFetch = globalThis.fetch;
  let calls = 0;
  const urls = [];
  globalThis.fetch = async (url) => {
    calls += 1;
    urls.push(String(url));
    return new Response(JSON.stringify({ earnings: [], sequence: calls }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    });
  };
  t.after(() => {
    globalThis.fetch = originalFetch;
    invalidateCache('earn');
  });

  invalidateCache('earn');
  const first = await api.earnings();
  const cached = await api.earnings();
  const refreshed = await api.earnings({ refresh: true });

  assert.equal(calls, 2);
  assert.equal(first.sequence, 1);
  assert.equal(cached._client_cached, true);
  assert.equal(refreshed.sequence, 2);
  assert.equal(urls[0], '/api/earnings/upcoming');
  assert.equal(urls[1], '/api/earnings/upcoming?refresh=true');
});

test('a slower pre-refresh response cannot overwrite the newer refreshed cache', async (t) => {
  const originalFetch = globalThis.fetch;
  const pending = [];
  let calls = 0;
  globalThis.fetch = () => {
    calls += 1;
    const sequence = calls;
    return new Promise((resolve) => {
      pending.push(() => resolve(new Response(JSON.stringify({ earnings: [], sequence }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })));
    });
  };
  t.after(() => {
    globalThis.fetch = originalFetch;
    invalidateCache('earn');
  });

  invalidateCache('earn');
  const olderRequest = api.earnings();
  await new Promise((resolve) => setImmediate(resolve));
  const refreshRequest = api.earnings({ refresh: true });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(pending.length, 2);
  pending[1]();
  const refreshed = await refreshRequest;
  pending[0]();
  const older = await olderRequest;
  const cached = await api.earnings();

  assert.equal(refreshed.sequence, 2);
  assert.equal(older.sequence, 1);
  assert.equal(cached.sequence, 2);
  assert.equal(cached._client_cached, true);
  assert.equal(calls, 2);
});
