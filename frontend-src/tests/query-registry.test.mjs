import test from 'node:test';
import assert from 'node:assert/strict';

import {
  dropQueryRegistry,
  invalidateQueryPaths,
  registryGet,
  resetQueryRegistry,
} from '../src/api/queryRegistry.ts';

function jsonResponse(body, headers = {}) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json', ...headers },
  });
}

test('concurrent reads of one whitelisted path share a single request', async () => {
  resetQueryRegistry();
  const originalFetch = globalThis.fetch;
  let fetchCount = 0;
  let release;
  globalThis.fetch = () => {
    fetchCount += 1;
    return new Promise((resolve) => {
      release = () => resolve(jsonResponse({ market: 'open' }));
    });
  };
  try {
    const first = registryGet('/market/status');
    const second = registryGet('/market/status');
    const third = registryGet('/market/status');
    assert.equal(fetchCount, 1);
    release();
    const values = await Promise.all([first, second, third]);
    assert.equal(values[0].market, 'open');
    assert.equal(values[1], values[0]);
    assert.equal(values[2], values[0]);
  } finally {
    globalThis.fetch = originalFetch;
    resetQueryRegistry();
  }
});

test('fresh window serves from memory without a second request', async () => {
  resetQueryRegistry();
  const originalFetch = globalThis.fetch;
  let fetchCount = 0;
  globalThis.fetch = async () => {
    fetchCount += 1;
    return jsonResponse({ indices: [1, 2] });
  };
  try {
    await registryGet('/market/indices');
    await registryGet('/market/indices');
    await registryGet('/market/indices');
    assert.equal(fetchCount, 1);
  } finally {
    globalThis.fetch = originalFetch;
    resetQueryRegistry();
  }
});

test('after the fresh window a 304 keeps the value with no body download', async () => {
  resetQueryRegistry();
  const originalFetch = globalThis.fetch;
  const originalNow = Date.now;
  const seen = [];
  let phase = 0;
  globalThis.fetch = async (_url, init) => {
    const headers = new Headers(init?.headers ?? {});
    seen.push(headers.get('If-None-Match'));
    if (phase === 0) {
      return jsonResponse({ items: ['real'] }, { ETag: '"v1"' });
    }
    return new Response(null, { status: 304, headers: { ETag: '"v1"' } });
  };
  try {
    let clock = originalNow();
    Date.now = () => clock;
    const first = await registryGet('/earnings/upcoming');
    assert.deepEqual(first.items, ['real']);
    assert.equal(seen[0], null);

    phase = 1;
    clock += 61_000; // beyond the 60s fresh window
    const second = await registryGet('/earnings/upcoming');
    assert.equal(seen[1], '"v1"');
    assert.deepEqual(second.items, ['real']);
    // The revalidated value is fresh again: no third request inside the window.
    const third = await registryGet('/earnings/upcoming');
    assert.equal(seen.length, 2);
    assert.deepEqual(third.items, ['real']);
  } finally {
    Date.now = originalNow;
    globalThis.fetch = originalFetch;
    resetQueryRegistry();
  }
});

test('manual invalidation discards in-flight responses instead of writing them back', async () => {
  resetQueryRegistry();
  const originalFetch = globalThis.fetch;
  let release;
  const bodies = [{ generationTag: 'stale' }, { generationTag: 'fresh' }];
  let served = 0;
  globalThis.fetch = () => {
    const body = bodies[served];
    served += 1;
    if (body.generationTag === 'stale') {
      return new Promise((resolve) => {
        release = () => resolve(jsonResponse(body));
      });
    }
    return Promise.resolve(jsonResponse(body));
  };
  try {
    const slow = registryGet('/signals/market');
    // The user hits refresh while the old request is still in flight.
    invalidateQueryPaths(['/signals/market']);
    release();
    const slowValue = await slow;
    // The original caller still gets its answer…
    assert.equal(slowValue.generationTag, 'stale');
    // …but the registry must not have cached it: the next read refetches.
    const next = await registryGet('/signals/market');
    assert.equal(next.generationTag, 'fresh');
    assert.equal(served, 2);
  } finally {
    globalThis.fetch = originalFetch;
    resetQueryRegistry();
  }
});

test('principal switch drops every cached value', async () => {
  resetQueryRegistry();
  const originalFetch = globalThis.fetch;
  let fetchCount = 0;
  globalThis.fetch = async () => {
    fetchCount += 1;
    return jsonResponse({ n: fetchCount });
  };
  try {
    const first = await registryGet('/strength/market');
    assert.equal(first.n, 1);
    dropQueryRegistry();
    const second = await registryGet('/strength/market');
    assert.equal(second.n, 2);
    assert.equal(fetchCount, 2);
  } finally {
    globalThis.fetch = originalFetch;
    resetQueryRegistry();
  }
});

test('non-whitelisted paths pass through without sharing', async () => {
  resetQueryRegistry();
  const originalFetch = globalThis.fetch;
  let fetchCount = 0;
  globalThis.fetch = async () => {
    fetchCount += 1;
    return jsonResponse({ ok: true });
  };
  try {
    await registryGet('/account/watchlist');
    await registryGet('/account/watchlist');
    assert.equal(fetchCount, 2);
  } finally {
    globalThis.fetch = originalFetch;
    resetQueryRegistry();
  }
});

test('hard invalidation bypasses the browser HTTP cache exactly once', async () => {
  resetQueryRegistry();
  const originalFetch = globalThis.fetch;
  const inits = [];
  let version = 'old';
  globalThis.fetch = async (_url, init) => {
    inits.push(init ?? {});
    return jsonResponse({ version }, { ETag: `"${version}"` });
  };
  try {
    const first = await registryGet('/earnings/upcoming');
    assert.equal(first.version, 'old');
    version = 'new';
    // 硬失效：POST 成功后的写路径语义——下一发必须打穿浏览器缓存，
    // 且不得带旧 ETag 的条件头（否则 60s max-age 窗口内可能整个
    // 不出浏览器就拿回刷新前的旧正文）。
    invalidateQueryPaths(['/earnings/upcoming'], { reload: true });
    const second = await registryGet('/earnings/upcoming');
    assert.equal(second.version, 'new');
    assert.equal(inits[1].cache, 'reload');
    assert.equal('If-None-Match' in (inits[1].headers ?? {}), false);
    // 一次性：硬失效只作用于紧随其后的一发，之后恢复条件请求语义。
    invalidateQueryPaths(['/earnings/upcoming']);
    const third = await registryGet('/earnings/upcoming');
    assert.equal(third.version, 'new');
    assert.equal(inits[2].cache, undefined);
  } finally {
    globalThis.fetch = originalFetch;
    resetQueryRegistry();
  }
});

test('restore age gate anchors on the last validation and deletes over-age records', async () => {
  const { persistedRecordWithinAge } = await import('../src/api/queryRegistry.ts');
  const DAY = 24 * 60 * 60 * 1000;
  const now = 1_800_000_000_000;
  // 无上限路径不设闸
  assert.equal(persistedRecordWithinAge({}, { storedAt: now - 400 * DAY }, now), true);
  // 上限内可恢复
  assert.equal(
    persistedRecordWithinAge({ maxRestoreAgeMs: 3 * DAY }, { storedAt: now - 2 * DAY }, now),
    true,
  );
  // 超龄拒绝
  assert.equal(
    persistedRecordWithinAge({ maxRestoreAgeMs: 3 * DAY }, { storedAt: now - 4 * DAY }, now),
    false,
  );
  // validatedAt 优先：老 storedAt + 新近 304 确认 = 仍可恢复
  assert.equal(
    persistedRecordWithinAge(
      { maxRestoreAgeMs: 3 * DAY },
      { storedAt: now - 40 * DAY, validatedAt: now - DAY },
      now,
    ),
    true,
  );
  // 损坏时间戳按不可恢复处理
  assert.equal(
    persistedRecordWithinAge({ maxRestoreAgeMs: 3 * DAY }, { storedAt: Number.NaN }, now),
    false,
  );
});
