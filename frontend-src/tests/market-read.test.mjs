import test from 'node:test';
import assert from 'node:assert/strict';

import {
  marketGet,
  resetMarketReadPaths,
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

test('manual path invalidation keeps the shared provider backoff for unrelated reads', async () => {
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
      (error) => error.code === 429,
    );
    resetMarketReadPaths(['/stocks/AAOI']);
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

test('an invalidated exact path gets one force-read backoff exemption shared by concurrent readers', async () => {
  resetMarketReadState();
  const originalFetch = globalThis.fetch;
  let fetchCount = 0;
  let releaseRefresh;
  globalThis.fetch = async () => {
    fetchCount += 1;
    if (fetchCount === 1) {
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
    }
    return new Promise((resolve) => {
      releaseRefresh = () =>
        resolve(
          new Response(JSON.stringify({ ticker: 'AAOI', price: 112.02 }), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          }),
        );
    });
  };
  try {
    await assert.rejects(
      marketGet('/stocks/AAOI'),
      (error) => error.code === 429,
    );
    resetMarketReadPaths(['/stocks/AAOI']);

    const refreshed = marketGet('/stocks/AAOI', { force: true });
    const shared = marketGet('/stocks/AAOI', { force: true });
    assert.equal(fetchCount, 2);
    releaseRefresh();
    const [first, second] = await Promise.all([refreshed, shared]);
    assert.deepEqual(first, second);
    assert.equal(first.price, 112.02);

    await assert.rejects(
      marketGet('/stocks/AAOI', { force: true }),
      (error) => error.code === 429 && error.retryAfter > 0,
    );
    await assert.rejects(
      marketGet('/stocks/AAOI/chart?range=1d&adjustment=raw', { force: true }),
      (error) => error.code === 429 && error.retryAfter > 0,
    );
    assert.equal(fetchCount, 2);
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

test('manual invalidation detaches an older in-flight response and starts a fresh shared read', async () => {
  resetMarketReadState();
  const originalFetch = globalThis.fetch;
  const releases = [];
  let fetchCount = 0;
  globalThis.fetch = () => {
    const requestIndex = fetchCount;
    fetchCount += 1;
    return new Promise((resolve) => {
      releases[requestIndex] = (price) =>
        resolve(
          new Response(JSON.stringify({ ticker: 'AAOI', price }), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          }),
        );
    });
  };
  try {
    const older = marketGet('/stocks/AAOI', {
      ttlMs: 60_000,
      staleMs: 60_000,
    });
    resetMarketReadPaths(['/stocks/AAOI']);
    const refreshed = marketGet('/stocks/AAOI', {
      ttlMs: 60_000,
      staleMs: 60_000,
      force: true,
    });
    const sharedRefreshed = marketGet('/stocks/AAOI', {
      ttlMs: 60_000,
      staleMs: 60_000,
      force: true,
    });

    assert.equal(fetchCount, 2);
    releases[1](112.02);
    const [freshA, freshB] = await Promise.all([refreshed, sharedRefreshed]);
    assert.equal(freshA.price, 112.02);
    assert.equal(freshB.price, 112.02);
    releases[0](1);
    assert.equal((await older).price, 1);

    const cached = await marketGet('/stocks/AAOI', {
      ttlMs: 60_000,
      staleMs: 60_000,
    });
    assert.equal(cached.price, 112.02);
    assert.equal(fetchCount, 2);
  } finally {
    globalThis.fetch = originalFetch;
    resetMarketReadState();
  }
});

test('identity reset rejects old cache writes after the new identity has already loaded the same URL', async () => {
  resetMarketReadState(); const originalFetch = globalThis.fetch; const releases = [];
  globalThis.fetch = () => new Promise(resolve => releases.push(resolve));
  const response = value => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
  try {
    const old = marketGet('/stocks/AAOI', { ttlMs: 60_000 });
    resetMarketReadState();
    const current = marketGet('/stocks/AAOI', { ttlMs: 60_000 });
    releases[1](response({ principal: 'current', price: 90 })); await current;
    releases[0](response({ principal: 'old', price: 100 })); await old;
    assert.deepEqual(await marketGet('/stocks/AAOI'), { principal: 'current', price: 90 });
    assert.equal(releases.length, 2);
  } finally { globalThis.fetch = originalFetch; resetMarketReadState(); }
});

test('force read does not join a pre-refresh GET and uses cache reload', async () => {
  resetMarketReadState();
  const originalFetch = globalThis.fetch;
  const inits = [];
  const releases = [];
  globalThis.fetch = (_url, init) => {
    inits.push(init);
    return new Promise((resolve) => {
      releases.push((price) =>
        resolve(
          new Response(JSON.stringify({ ticker: 'NVDA', price }), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          }),
        ),
      );
    });
  };
  try {
    const stale = marketGet('/strength/scan?sector_id=semiconductors', { ttlMs: 60_000 });
    const forced = marketGet('/strength/scan?sector_id=semiconductors', {
      ttlMs: 60_000,
      force: true,
    });
    assert.equal(releases.length, 2);
    assert.equal(inits[1].cache, 'reload');
    releases[1](180);
    assert.equal((await forced).price, 180);
    releases[0](12);
    assert.equal((await stale).price, 12);
  } finally {
    globalThis.fetch = originalFetch;
    resetMarketReadState();
  }
});

test('a previous identity late 429 cannot impose a shared backoff on the new identity', async () => {
  resetMarketReadState(); const originalFetch = globalThis.fetch; let release;
  globalThis.fetch = () => new Promise(resolve => { release = resolve; });
  try {
    const old = marketGet('/stocks/AAOI'); const rejected = assert.rejects(old, error => error.code === 429);
    resetMarketReadState();
    release(new Response(JSON.stringify({ message: 'old limit' }), { status: 429, headers: { 'Content-Type': 'application/json', 'Retry-After': '300' } }));
    await rejected;
    let requests = 0;
    globalThis.fetch = async () => { requests++; return new Response(JSON.stringify({ price: 42 })); };
    assert.deepEqual(await marketGet('/stocks/NEW'), { price: 42 }); assert.equal(requests, 1);
  } finally { globalThis.fetch = originalFetch; resetMarketReadState(); }
});

test('late ordinary read cannot overwrite a newer forced response', async () => {
  resetMarketReadState();
  const originalFetch = globalThis.fetch;
  const releases = [];
  globalThis.fetch = () => new Promise((resolve) => releases.push((price) => resolve(new Response(JSON.stringify({ price }), { headers: { 'Content-Type': 'application/json' } }))));
  try {
    const old = marketGet('/strength/scan?top=20');
    const fresh = marketGet('/strength/scan?top=20', { force: true });
    releases[1](180);
    await fresh;
    releases[0](120);
    await old;
    assert.equal((await marketGet('/strength/scan?top=20')).price, 180);
  } finally {
    globalThis.fetch = originalFetch;
    resetMarketReadState();
  }
});

test('a detached forced request cannot remove the newer forced request sharing marker', async () => {
  resetMarketReadState();
  const originalFetch = globalThis.fetch;
  const releases = [];
  globalThis.fetch = () => new Promise((resolve) => releases.push(() => resolve(new Response('{}', { headers: { 'Content-Type': 'application/json' } }))));
  try {
    const old = marketGet('/strength/scan', { force: true });
    resetMarketReadPaths(['/strength/scan']);
    const fresh = marketGet('/strength/scan', { force: true });
    releases[0]();
    await old;
    const shared = marketGet('/strength/scan', { force: true });
    const count = releases.length;
    for (const release of releases.slice(1)) release();
    await Promise.all([fresh, shared]);
    assert.equal(count, 2);
    assert.equal(fresh, shared);
  } finally {
    globalThis.fetch = originalFetch;
    resetMarketReadState();
  }
});
