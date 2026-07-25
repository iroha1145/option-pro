/**
 * 身份与网络可靠性回归（GPT-5.6-Pro 审计 P1-07 / P1-08 / P1-09 / P1-10 / P2-1 / P2-2 / P3-6）
 *
 * 这一批的共同形状是「时序」而不是「取值」：先发出后返回的响应能覆盖新结果、
 * 会话过期后界面不降级、请求永不超时、一个损坏的本地存储值让整站白屏。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

import {
  ApiError,
  PRINCIPAL_INVALID_EVENT,
  REQUEST_TIMEOUT_MS,
  get,
} from '../src/api/client.ts';
import {
  RECENT_KEY,
  RECENT_LIMIT,
  pushRecent,
  readRecent,
} from '../src/lib/recentTickers.ts';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = path.resolve(here, '..', 'src');

async function source(relativePath) {
  return readFile(path.join(src, relativePath), 'utf8');
}

function codeOf(text) {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    .filter((line) => {
      const trimmed = line.trimStart();
      return !trimmed.startsWith('//') && !trimmed.startsWith('*');
    })
    .join('\n');
}

/* ---------------- P1-10：请求超时与取消 ---------------- */

test('请求带默认超时，不会永久停在「请求中」', async () => {
  assert.equal(typeof REQUEST_TIMEOUT_MS, 'number');
  assert.ok(REQUEST_TIMEOUT_MS > 0 && REQUEST_TIMEOUT_MS <= 30_000);

  const originalFetch = globalThis.fetch;
  // 模拟半开连接：永不 resolve，只有 abort 能终止。
  globalThis.fetch = (_url, init) =>
    new Promise((_resolve, reject) => {
      init.signal.addEventListener('abort', () => {
        const error = new Error('aborted');
        error.name = 'AbortError';
        reject(error);
      });
    });
  try {
    const started = Date.now();
    await assert.rejects(
      get('/stocks/AAOI', { timeoutMs: 40 }),
      (error) => {
        assert.ok(error instanceof ApiError);
        assert.equal(error.code, 408);
        assert.equal(error.bizCode, 'request_timeout');
        assert.equal(error.retryable, true);
        return true;
      },
    );
    assert.ok(Date.now() - started < 2_000, '超时必须真的终止请求，而不是等到测试超时');
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('调用方 signal 仍然有效，且不会被误报成超时', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (_url, init) =>
    new Promise((_resolve, reject) => {
      init.signal.addEventListener('abort', () => {
        const error = new Error('aborted');
        error.name = 'AbortError';
        reject(error);
      });
    });
  try {
    const controller = new AbortController();
    const pending = get('/stocks/AAOI', { signal: controller.signal });
    controller.abort();
    await assert.rejects(pending, (error) => {
      // 主动取消不是超时：不能伪装成 408 让调用方去重试。
      assert.equal(error instanceof ApiError, false);
      assert.equal(error.name, 'AbortError');
      return true;
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

/* ---------------- P1-08：任一主体失效都要降级 ---------------- */

test('客户会话 401 与管理员 401 走同一条降级通路', async () => {
  const originalFetch = globalThis.fetch;
  const seen = [];
  const onInvalid = () => seen.push('event');
  // 浏览器里 window 就是全局 EventTarget；Node 的 globalThis 不是，需要补一个。
  const fakeWindow = new EventTarget();
  globalThis.window = fakeWindow;
  fakeWindow.addEventListener(PRINCIPAL_INVALID_EVENT, onInvalid);
  globalThis.fetch = async () =>
    new Response(JSON.stringify({ code: 'account_login_required', message: '请先登录' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    });
  try {
    await assert.rejects(get('/account/watchlist'));
    assert.deepEqual(seen, ['event'], 'account_login_required 必须广播主体失效');
  } finally {
    fakeWindow.removeEventListener(PRINCIPAL_INVALID_EVENT, onInvalid);
    delete globalThis.window;
    globalThis.fetch = originalFetch;
  }
});

test('无关的 401 不会误伤当前身份', async () => {
  const originalFetch = globalThis.fetch;
  const seen = [];
  const onInvalid = () => seen.push('event');
  // 浏览器里 window 就是全局 EventTarget；Node 的 globalThis 不是，需要补一个。
  const fakeWindow = new EventTarget();
  globalThis.window = fakeWindow;
  fakeWindow.addEventListener(PRINCIPAL_INVALID_EVENT, onInvalid);
  globalThis.fetch = async () =>
    new Response(JSON.stringify({ code: 'analysis_required', message: '需要先分析' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    });
  try {
    await assert.rejects(get('/ai/jobs/x'));
    assert.deepEqual(seen, [], '只有身份相关的业务码才触发降级');
  } finally {
    fakeWindow.removeEventListener(PRINCIPAL_INVALID_EVENT, onInvalid);
    delete globalThis.window;
    globalThis.fetch = originalFetch;
  }
});

test('主动核验对客户与管理员同时生效', async () => {
  const hook = codeOf(await source('hooks/useAccess.tsx'));
  assert.doesNotMatch(
    hook,
    /if \(status\.role !== 'owner'\) return;/,
    '定时核验不能只在管理员身份下运行',
  );
  assert.match(
    hook,
    /hasPrincipal =\s*status\.role === 'owner' \|\| status\.accountUsername !== null/,
  );
  assert.match(hook, /if \(!hasPrincipal\) return;/);
});

/* ---------------- P1-07：身份响应乱序 ---------------- */

test('身份读取带世代号，旧响应不能覆盖新结果', async () => {
  const hook = codeOf(await source('hooks/useAccess.tsx'));
  assert.match(hook, /generationRef = useRef\(0\)/);
  assert.match(hook, /if \(generation !== generationRef\.current\) return;/);
  // 登录 / 注册 / 登出必须先递增世代，作废在途探测
  assert.match(hook, /generationRef\.current \+= 1;/);
  assert.match(hook, /applyWrite\(\(\) => accessApi\.login\(username, password\)\)/);
  assert.match(hook, /applyWrite\(\(\) => accessApi\.logout\(\)\)/);
});

/* ---------------- P2-1 / P2-2：错误不再由 visitor 兼任 ---------------- */

test('身份服务不可用是独立状态，不显示成未登录', async () => {
  const hook = codeOf(await source('hooks/useAccess.tsx'));
  assert.match(hook, /identityUnavailable/);
  assert.match(hook, /setIdentityUnavailable\(true\)/);
});

test('写操作成功后状态校验失败不再报成登录失败', async () => {
  const api = codeOf(await source('api/modules/access.ts'));
  const hook = codeOf(await source('hooks/useAccess.tsx'));
  // 登录不再把状态 GET 串进同一个 Promise
  assert.doesNotMatch(api, /post\('\/access\/login'[^)]*\)\.then\(liveStatus\)/);
  assert.doesNotMatch(api, /post\('\/access\/logout'\)\.then\(liveStatus\)/);
  assert.match(hook, /await write\(\);\s*\n\s*await invalidateAndRead\(\)\.catch\(\(\) => undefined\);/);
});

/* ---------------- P1-09：损坏的本地存储不能让整站白屏 ---------------- */

function withStorage(seed) {
  const store = new Map(seed === undefined ? [] : [[RECENT_KEY, seed]]);
  const original = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: {
      getItem: (key) => (store.has(key) ? store.get(key) : null),
      setItem: (key, value) => store.set(key, String(value)),
      removeItem: (key) => store.delete(key),
      clear: () => store.clear(),
    },
  });
  return {
    store,
    restore: () => {
      if (original) Object.defineProperty(globalThis, 'localStorage', original);
      else delete globalThis.localStorage;
    },
  };
}

test('损坏的 recent-tickers 不抛异常，并被就地清理', () => {
  // 审计给出的最小复现，加上其余几种合法 JSON 但不是数组的形状。
  for (const corrupt of ['{}', 'null', '"NVDA"', '42', 'not json at all']) {
    const ctx = withStorage(corrupt);
    try {
      assert.deepEqual(readRecent(), [], `坏值 ${corrupt} 应读成空表而不是抛异常`);
      assert.equal(
        ctx.store.has(RECENT_KEY),
        false,
        `坏值 ${corrupt} 应被删除，否则每次启动都会再踩一次`,
      );
    } finally {
      ctx.restore();
    }
  }
});

test('recent-tickers 逐项校验形状并封顶条数', () => {
  const ctx = withStorage(
    JSON.stringify([
      'NVDA',
      123,
      null,
      { ticker: 'AAPL' },
      'A'.repeat(64),
      '<script>',
      'MSFT',
      'TSLA',
      'AMD',
      'AVGO',
      'SMCI',
    ]),
  );
  try {
    const recent = readRecent();
    assert.deepEqual(recent, ['NVDA', 'MSFT', 'TSLA', 'AMD', 'AVGO']);
    assert.equal(recent.length, RECENT_LIMIT);
    // 过滤结果应写回，下次读取不必重新清洗
    assert.deepEqual(JSON.parse(ctx.store.get(RECENT_KEY)), recent);
  } finally {
    ctx.restore();
  }
});

test('写入路径复用同一套校验，坏值不会被写回', () => {
  const ctx = withStorage('{}');
  try {
    pushRecent('NVDA');
    assert.deepEqual(JSON.parse(ctx.store.get(RECENT_KEY)), ['NVDA']);
    pushRecent('<script>');
    assert.deepEqual(
      JSON.parse(ctx.store.get(RECENT_KEY)),
      ['NVDA'],
      '非法代码不得进入本地存储',
    );
  } finally {
    ctx.restore();
  }
});

test('存储本身不可用时不抛异常', () => {
  const original = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    get() {
      throw new Error('SecurityError: storage disabled');
    },
  });
  try {
    assert.deepEqual(readRecent(), []);
    assert.doesNotThrow(() => pushRecent('NVDA'));
  } finally {
    if (original) Object.defineProperty(globalThis, 'localStorage', original);
    else delete globalThis.localStorage;
  }
});

test('命令面板不再内联本地存储解析', async () => {
  const palette = codeOf(await source('components/CommandPalette.tsx'));
  assert.doesNotMatch(palette, /JSON\.parse\(localStorage/);
  assert.match(palette, /from '@\/lib\/recentTickers'/);
});

test('顶级错误边界包住身份 Provider、命令面板与抽屉', async () => {
  const app = codeOf(await source('App.tsx'));
  // AppErrorBoundary 必须在 AccessProvider 之外
  assert.match(app, /<AppErrorBoundary>\s*\n\s*<AccessProvider>/);
  assert.match(app, /<\/AccessProvider>\s*\n\s*<\/AppErrorBoundary>/);
});

/* ---------------- P3-6：未知路由显示 404 ---------------- */

test('未知路由不再静默重定向到自选', async () => {
  const app = codeOf(await source('App.tsx'));
  assert.doesNotMatch(app, /path="\*" element=\{<Navigate to="\/watchlist" replace \/>\}/);
  assert.match(app, /path="\*" element=\{<NotFound \/>\}/);
});
