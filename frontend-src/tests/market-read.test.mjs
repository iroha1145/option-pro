import test from 'node:test';
import assert from 'node:assert/strict';

import {
  marketGet,
  resetMarketReadState,
} from '../src/api/marketRead.ts';

test('same stock-detail URL shares one in-flight request', async () => {
  resetMarketReadState();
  const originalFetch = globalThis.fetch;
  let fetchCount = 0;
  let release;
  globalThis.fetch = () => {
    fetchCount += 1;
    return new Promise((resolve) => {
      release = () =>
        resolve(
          new Response(JSON.stringify({ ticker: 'AAOI', price: 25.125 }), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          }),
        );
    });
  };
  try {
    const first = marketGet('/stocks/AAOI', { ttlMs: 1_000 });
    const second = marketGet('/stocks/AAOI', { ttlMs: 1_000 });
    assert.equal(fetchCount, 1);
    release();
    const [a, b] = await Promise.all([first, second]);
    assert.deepEqual(a, b);
    assert.equal(a.price, 25.125);
  } finally {
    globalThis.fetch = originalFetch;
    resetMarketReadState();
  }
});

test('initial stock drawer fan-out issues five unique market reads', async () => {
  resetMarketReadState();
  const originalFetch = globalThis.fetch;
  const requested = [];
  globalThis.fetch = async (url) => {
    requested.push(String(url));
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  };
  try {
    await Promise.all([
      marketGet('/stocks/AAOI'),
      marketGet('/strength/stocks/AAOI'),
      marketGet('/stocks/AAOI/chart?range=1d&adjustment=raw'),
      marketGet('/signals/stock/AAOI'),
      marketGet('/signals/stock/AAOI'),
      marketGet('/breakouts/tickers/AAOI'),
      marketGet('/breakouts/tickers/AAOI'),
    ]);
    assert.equal(requested.length, 5);
    assert.equal(new Set(requested).size, 5);
  } finally {
    globalThis.fetch = originalFetch;
    resetMarketReadState();
  }
});

test('429 applies shared backoff without issuing another market request', async () => {
  resetMarketReadState();
  const originalFetch = globalThis.fetch;
  let fetchCount = 0;
  globalThis.fetch = async () => {
    fetchCount += 1;
    return new Response(
      JSON.stringify({ error: 'rate_limited', message: 'Too many requests' }),
      {
        status: 429,
        headers: {
          'Content-Type': 'application/json',
          'Retry-After': '60',
        },
      },
    );
  };
  try {
    await assert.rejects(
      marketGet('/stocks/AAOI'),
      (error) => error.code === 429 && error.retryAfter === 60,
    );
    await assert.rejects(
      marketGet('/stocks/NBIS'),
      (error) => error.code === 429 && error.retryAfter > 0,
    );
    assert.equal(fetchCount, 1);
  } finally {
    globalThis.fetch = originalFetch;
    resetMarketReadState();
  }
});

test('bounded stale fallback reuses only a prior real response', async () => {
  resetMarketReadState();
  const originalFetch = globalThis.fetch;
  let fetchCount = 0;
  globalThis.fetch = async () => {
    fetchCount += 1;
    if (fetchCount === 1) {
      return new Response(JSON.stringify({ ticker: 'NBIS', price: 52.5 }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }
    return new Response(
      JSON.stringify({ message: 'provider unavailable' }),
      {
        status: 503,
        headers: { 'Content-Type': 'application/json' },
      },
    );
  };
  try {
    const live = await marketGet('/stocks/NBIS', {
      ttlMs: 0,
      staleMs: 60_000,
    });
    const recovered = await marketGet('/stocks/NBIS', {
      ttlMs: 0,
      staleMs: 60_000,
    });
    assert.deepEqual(recovered, live);
    assert.equal(fetchCount, 2);
    await assert.rejects(
      marketGet('/stocks/NBIS', {
        ttlMs: 0,
        staleMs: 60_000,
        force: true,
      }),
      (error) => error.code === 503,
    );
  } finally {
    globalThis.fetch = originalFetch;
    resetMarketReadState();
  }
});
