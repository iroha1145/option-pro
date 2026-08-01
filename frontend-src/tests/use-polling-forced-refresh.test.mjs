import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

/**
 * P1-01 行为守卫：手动刷新（force）后，旧在途响应最后返回也不得把
 * 刷新前的数据写回页面；且旧请求在途时 force 必须真的发起新请求
 * （普通 refresh 会被同世代合流闸吞掉——那正是修复前的根因）。
 *
 * 用最小 React 桩直接执行真实的 usePolling.ts：只实现
 * useState/useRef/useCallback/useEffect 四个原语 + 手动渲染循环。
 */

const here = path.dirname(fileURLToPath(import.meta.url));
const hookPath = path.resolve(here, '..', 'src', 'hooks', 'usePolling.ts');

function createReactStub() {
  const slots = [];
  let cursor = 0;
  let renderFn = null;
  let renderArgs = [];
  let lastResult = null;
  let rendering = false;
  let renderQueued = false;
  const effects = [];

  function flushEffects() {
    for (const effect of effects) {
      if (!effect.dirty) continue;
      effect.dirty = false;
      if (effect.cleanup) effect.cleanup();
      const cleanup = effect.create();
      effect.cleanup = typeof cleanup === 'function' ? cleanup : null;
    }
  }

  function render() {
    if (rendering) {
      renderQueued = true;
      return;
    }
    rendering = true;
    let guard = 0;
    do {
      renderQueued = false;
      guard += 1;
      if (guard > 50) throw new Error('render loop runaway');
      cursor = 0;
      lastResult = renderFn(...renderArgs);
      flushEffects();
    } while (renderQueued);
    rendering = false;
  }

  const React = {
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) {
        slots[index] = {
          value: typeof initial === 'function' ? initial() : initial,
        };
      }
      const slot = slots[index];
      if (!slot.set) {
        slot.set = (next) => {
          const value = typeof next === 'function' ? next(slot.value) : next;
          if (Object.is(value, slot.value)) return;
          slot.value = value;
          render();
        };
      }
      return [slot.value, slot.set];
    },
    useRef(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = { value: { current: initial } };
      return slots[index].value;
    },
    useCallback(fn, deps) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = { value: fn, deps: undefined };
      const slot = slots[index];
      const changed =
        slot.deps === undefined ||
        deps === undefined ||
        deps.length !== slot.deps.length ||
        deps.some((dep, i) => !Object.is(dep, slot.deps[i]));
      if (changed) {
        slot.value = fn;
        slot.deps = deps;
      }
      return slot.value;
    },
    useEffect(create, deps) {
      const index = cursor++;
      if (!(index in slots)) {
        const record = { deps: undefined, create, cleanup: null, dirty: true };
        slots[index] = { value: record };
        effects.push(record);
      }
      const record = slots[index].value;
      const changed =
        record.deps === undefined ||
        deps === undefined ||
        deps.length !== record.deps.length ||
        deps.some((dep, i) => !Object.is(dep, record.deps[i]));
      record.deps = deps;
      record.create = create;
      if (changed && !record.dirty) record.dirty = true;
      if (record.deps !== undefined && !changed) record.dirty = record.dirty && true;
    },
  };

  return {
    React,
    mount(fn, ...args) {
      renderFn = fn;
      renderArgs = args;
      render();
      return () => lastResult;
    },
  };
}

function loadUsePolling(React) {
  const source = fs.readFileSync(hookPath, 'utf8');
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      esModuleInterop: true,
    },
  }).outputText;
  const module = { exports: {} };
  class ApiError extends Error {
    constructor(status, message) {
      super(message);
      this.status = status;
    }
  }
  vm.runInNewContext(compiled, {
    module,
    exports: module.exports,
    require: (id) => {
      if (id === 'react') return React;
      if (id === '@/api/client') return { ApiError };
      if (id === '../i18n/core.ts') return { t: (msg) => msg };
      throw new Error(`unexpected import: ${id}`);
    },
    document: {
      visibilityState: 'visible',
      addEventListener() {},
      removeEventListener() {},
    },
    setInterval,
    clearInterval,
    Date,
    Promise,
    Set,
    Array,
  });
  return module.exports.usePolling;
}

async function settle() {
  for (let i = 0; i < 8; i += 1) await Promise.resolve();
}

test('forced refresh issues a real request and old in-flight data cannot overwrite it', async () => {
  const { React, mount } = createReactStub();
  const usePolling = loadUsePolling(React);

  let call = 0;
  let resolveOld = null;
  let resolveNew = null;
  const fetcher = () => {
    call += 1;
    if (call === 1) {
      return new Promise((resolve) => {
        resolveOld = () => resolve({ version: 'old' });
      });
    }
    return new Promise((resolve) => {
      resolveNew = () => resolve({ version: 'new' });
    });
  };

  const read = mount(() => usePolling(fetcher, null, []));
  await settle();
  assert.equal(call, 1);
  assert.equal(read().loading, true);

  // 复现前提：旧请求在途时，普通 refresh 被合流闸吞掉，不发新请求。
  read().refresh();
  await settle();
  assert.equal(call, 1);

  // 强制刷新：开新世代，必须真的发起第二个请求。
  read().refresh({ force: true });
  await settle();
  assert.equal(call, 2);

  resolveNew();
  await settle();
  assert.equal(read().data.version, 'new');
  assert.equal(read().loading, false);

  // 核心断言：旧在途响应最后返回，也不得覆盖新数据。
  resolveOld();
  await settle();
  assert.equal(read().data.version, 'new');
  assert.equal(read().error, null);
});

test('forced refresh replaces stale error state with the fresh result', async () => {
  const { React, mount } = createReactStub();
  const usePolling = loadUsePolling(React);

  let call = 0;
  let rejectOld = null;
  let resolveNew = null;
  const fetcher = () => {
    call += 1;
    if (call === 1) {
      return new Promise((_resolve, reject) => {
        rejectOld = () => reject(new Error('stale failure'));
      });
    }
    return new Promise((resolve) => {
      resolveNew = () => resolve({ version: 'new' });
    });
  };

  const read = mount(() => usePolling(fetcher, null, []));
  await settle();
  read().refresh({ force: true });
  await settle();
  assert.equal(call, 2);

  resolveNew();
  await settle();
  // 旧请求随后失败：不得把错误态压到新数据上。
  rejectOld();
  await settle();
  assert.equal(read().data.version, 'new');
  assert.equal(read().error, null);
});
