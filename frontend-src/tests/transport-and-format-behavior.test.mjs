import test from 'node:test';
import assert from 'node:assert/strict';
import { setTimeout as delay } from 'node:timers/promises';
import {
  apiHeaders, fetchBuffered, parseRetryAfter, ResponseLimitError, TransportTimeoutError,
} from '../src/api/transport.ts';
import { ApiError, idFromLocation, postCreate, request, requestRaw } from '../src/api/client.ts';
import { fmtPrice, fmtSigned, fmtPct, fmtCompact, fmtCountdown, fmtNyTime, fmtTimeHHMMSS } from '../src/lib/format.ts';

for (const status of [200, 503]) {
  test(`the request deadline covers an unfinished ${status} response body`, async (t) => {
    let cancelled = false;
    t.mock.method(globalThis, 'fetch', async () => new Response(new ReadableStream({
      start(controller) { controller.enqueue(new TextEncoder().encode('{')); },
      cancel() { cancelled = true; },
    }), { status }));
    await assert.rejects(request('/test', { timeoutMs: 30 }), (error) =>
      error instanceof ApiError && error.code === 408 && error.bizCode === 'request_timeout');
    await delay(0);
    assert.equal(cancelled, true, 'both cloned body branches release the underlying stream');
  });
}

test('small recurring body fragments do not reset the total deadline', async (t) => {
  let stopped = false;
  let fragments = 0;
  t.mock.method(globalThis, 'fetch', async () => new Response(new ReadableStream({
    async pull(controller) {
      await delay(5);
      if (stopped) return;
      fragments++;
      controller.enqueue(new Uint8Array([32]));
    },
    cancel() { stopped = true; },
  })));
  await assert.rejects(fetchBuffered('/test', {}, 40), TransportTimeoutError);
  assert.ok(fragments > 0);
  assert.equal(stopped, true);
});

test('caller cancellation still works after headers arrive and with timeout disabled', async (t) => {
  const caller = new AbortController();
  let cancelled = false;
  t.mock.method(globalThis, 'fetch', async () => {
    queueMicrotask(() => caller.abort());
    return new Response(new ReadableStream({ cancel() { cancelled = true; } }));
  });
  await assert.rejects(fetchBuffered('/test', { signal: caller.signal }, 0), { name: 'AbortError' });
  await delay(0);
  assert.equal(cancelled, true);
});

test('an already-cancelled request never starts a fetch', async (t) => {
  const fetchMock = t.mock.method(globalThis, 'fetch', async () => new Response('unexpected'));
  const caller = new AbortController();
  caller.abort();
  await assert.rejects(fetchBuffered('/test', { signal: caller.signal }, 0), { name: 'AbortError' });
  assert.equal(fetchMock.mock.callCount(), 0);
});

test('a late response from an adapter ignoring abort has its body cancelled', async (t) => {
  let resolveFetch;
  let cancelled = false;
  t.mock.method(globalThis, 'fetch', () => new Promise((resolve) => { resolveFetch = resolve; }));
  await assert.rejects(fetchBuffered('/test', {}, 20), TransportTimeoutError);
  resolveFetch(new Response(new ReadableStream({ cancel() { cancelled = true; } })));
  await delay(0);
  assert.equal(cancelled, true);
});

test('buffering preserves the original response, headers, cloning and body ownership', async (t) => {
  const original = new Response('{"ready":true}', { status: 202, headers: { Location: '/api/jobs/abc' } });
  t.mock.method(globalThis, 'fetch', async () => original);
  const response = await fetchBuffered('/test', {}, 1000);
  assert.equal(response, original);
  assert.equal(response.bodyUsed, false);
  assert.equal(response.headers.get('Location'), '/api/jobs/abc');
  assert.deepEqual(await response.clone().json(), { ready: true });
  assert.deepEqual(await response.json(), { ready: true });
});

test('response bounds count the actual streamed bytes, even without Content-Length', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => new Response(new ReadableStream({
    start(controller) {
      controller.enqueue(new Uint8Array(4));
      controller.enqueue(new Uint8Array(5));
      controller.close();
    },
  })));
  await assert.rejects(fetchBuffered('/test', {}, 1000, 8), ResponseLimitError);
  assert.equal((await fetchBuffered('/test', {}, 1000, 9)).status, 200);
});

test('304 and empty 204 responses retain their existing request behavior', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => new Response(null, { status: 304, headers: { ETag: 'v1' } }));
  const conditional = await requestRaw('/test', { acceptNotModified: true });
  assert.equal(conditional.status, 304);
  assert.equal(conditional.headers.get('ETag'), 'v1');
  t.mock.method(globalThis, 'fetch', async () => new Response(null, { status: 204 }));
  assert.equal(await request('/test'), undefined);
});

test('request headers accept records, Headers and tuples without modifying caller input', async (t) => {
  for (const input of [{ 'If-None-Match': 'v1' }, new Headers({ 'If-None-Match': 'v1' }), [['If-None-Match', 'v1']]]) {
    const before = [...new Headers(input)];
    const headers = apiHeaders(input, false);
    assert.equal(headers.get('If-None-Match'), 'v1');
    assert.equal(headers.get('Content-Type'), 'application/json');
    assert.deepEqual([...new Headers(input)], before);
  }
  let received;
  t.mock.method(globalThis, 'fetch', async (_url, init) => {
    received = init;
    return new Response('{}');
  });
  await request('/test', { method: 'POST', headers: new Headers({ 'X-Optix-Action': '0' }) });
  assert.equal(received.headers.get('X-Optix-Action'), '1');
  assert.equal(received.credentials, 'include');
  assert.equal(received.redirect, 'error');
});

test('Retry-After supports seconds and HTTP dates while rejecting unknown values', () => {
  const now = Date.parse('2026-09-05T00:00:00Z');
  assert.equal(parseRetryAfter('30', now), 30);
  assert.equal(parseRetryAfter('Sat, 05 Sep 2026 00:00:20 GMT', now), 20);
  assert.equal(parseRetryAfter('Sat, 05 Sep 2026 00:00:00 GMT', now + 1), 0);
  for (const value of ['', ' ', '-1', '1e3', -1, NaN, Infinity, null]) {
    assert.equal(parseRetryAfter(value, now), undefined);
  }
});

test('non-JSON proxy errors retain the server retry delay', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => new Response('<html>Busy</html>', {
    status: 429, headers: { 'Retry-After': '30', 'Content-Type': 'text/html' },
  }));
  await assert.rejects(request('/test'), (error) => error instanceof ApiError && error.retryAfter === 30);
});

test('valid nested retry values survive malformed body fields and override headers', async (t) => {
  for (const [body, expected] of [
    [{ retry_after_seconds: '', retry_after: 12 }, 12],
    [{ retry_after: -1, detail: { retry_after_seconds: 12, retryable: true } }, 12],
    [{ error: { retry_after: -1 }, retry_after: 12 }, 12],
    [{ detail: { retry_after: -1 } }, 30],
  ]) {
    t.mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify(body), { status: 503, headers: { 'Retry-After': '30' } }));
    await assert.rejects(request('/test'), (error) => error instanceof ApiError && error.retryAfter === expected);
  }
});

test('task creation reads date-based retry delays and malformed Location stays recoverable', async (t) => {
  t.mock.method(Date, 'now', () => Date.parse('2026-09-05T00:00:00Z'));
  t.mock.method(globalThis, 'fetch', async () => new Response('{"id":"fallback"}', {
    status: 202, headers: { Location: '/api/jobs/%E0%A4%A', 'Retry-After': 'Sat, 05 Sep 2026 00:00:20 GMT' },
  }));
  const created = await postCreate('/test', {});
  assert.equal(created.retryAfter, 20);
  assert.equal(created.data.id, 'fallback');
  assert.equal(idFromLocation(created.location), null);
  assert.equal(idFromLocation('/api/jobs/abc#state'), 'abc');
  assert.equal(idFromLocation('/api/jobs/abc?wait=1'), 'abc');
});

test('missing and nonfinite financial values remain distinct from real zero', () => {
  for (const formatter of [fmtPrice, fmtSigned, fmtPct, fmtCompact]) {
    for (const value of [null, undefined, NaN, Infinity, -Infinity]) assert.equal(formatter(value), '—');
    assert.notEqual(formatter(0), '—');
  }
  assert.equal(fmtCompact(-2_500_000_000), '-2.50B');
  assert.equal(fmtSigned(-12), '−12.00');
  assert.equal(fmtPct(1.5), '+1.50%');
  assert.doesNotThrow(() => fmtPrice(1.23, -1));
  assert.doesNotThrow(() => fmtPct(1.23, Infinity));
});

test('invalid countdowns are empty and New York clocks use the same midnight convention', () => {
  assert.equal(fmtCountdown('not-a-date', Date.now()), '—');
  assert.equal(fmtCountdown('2026-09-05T00:00:00Z', NaN), '—');
  assert.equal(fmtCountdown('2026-09-05T00:00:00Z', Date.parse('2026-09-05T00:00:01Z')), '00:00:00');
  const midnight = new Date('2026-09-05T04:00:00Z');
  assert.equal(fmtNyTime(midnight), '00:00:00');
  assert.equal(fmtNyTime(midnight), fmtTimeHHMMSS(midnight));
  assert.equal(fmtNyTime(new Date(NaN)), '—');
});
