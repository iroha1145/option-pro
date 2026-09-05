import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

// Run real hooks against deterministic effects and timer callbacks. Render-phase
// updates discard their intermediate render, as React does before committing.
function mountHook(file, renderHook) {
  const slots = [];
  const timers = new Map();
  let cursor = 0;
  let queued = false;
  let rendering = false;
  let render;
  let result;
  let now = 0;
  let nextTimer = 0;
  const React = {
    useState(initial) {
      const index = cursor++;
      const slot = slots[index] ??= { value: typeof initial === 'function' ? initial() : initial };
      slot.set ??= (next) => {
        const value = typeof next === 'function' ? next(slot.value) : next;
        if (Object.is(value, slot.value)) return;
        slot.value = value;
        render();
      };
      return [slot.value, slot.set];
    },
    useRef(initial) { return slots[cursor++] ??= { current: initial }; },
    useEffect(create, deps) {
      const slot = slots[cursor++] ??= {};
      if (!slot.deps || deps.some((dep, i) => !Object.is(dep, slot.deps[i]))) {
        Object.assign(slot, { create, deps, dirty: true });
      }
    },
    useMemo(create, deps) {
      const slot = slots[cursor++] ??= {};
      if (!slot.deps || deps.some((dep, i) => !Object.is(dep, slot.deps[i]))) {
        Object.assign(slot, { value: create(), deps });
      }
      return slot.value;
    },
    useCallback(fn, deps) { return React.useMemo(() => fn, deps); },
  };
  const compiled = ts.transpileModule(fs.readFileSync(new URL(`../src/hooks/${file}`, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const module = { exports: {} };
  vm.runInNewContext(compiled, {
    module, exports: module.exports,
    require(id) {
      if (id === 'react') return React;
      if (id === 'react-dom') return { flushSync: (fn) => fn() };
      throw new Error(`Unexpected import ${id}`);
    },
    setTimeout(fn, delay) {
      const id = ++nextTimer;
      timers.set(id, { fn, at: now + delay });
      return id;
    },
    clearTimeout(id) { timers.delete(id); },
  });
  render = () => {
    if (rendering) { queued = true; return; }
    rendering = true;
    let guard = 0;
    do {
      assert.ok(++guard < 30, 'hook must settle without repeated value-only rerenders');
      queued = false;
      cursor = 0;
      result = renderHook(module.exports);
      if (queued) continue;
      for (const slot of slots) {
        if (!slot.dirty) continue;
        slot.dirty = false;
        slot.cleanup?.();
        slot.cleanup = slot.create();
      }
    } while (queued);
    rendering = false;
  };
  render();
  return {
    read: () => result, render, timers,
    advance(at) {
      now = at;
      for (const [id, timer] of [...timers]) {
        if (timer.at <= at) { timers.delete(id); timer.fn(); }
      }
    },
    unmount() { slots.forEach((slot) => slot.cleanup?.()); },
  };
}

const rowsOf = (count) => Array.from({ length: count }, (_, id) => ({ id }));

test('progressive list retains loaded coverage through polling and clamps only to available data', () => {
  let rows = rowsOf(214);
  const h = mountHook('useProgressiveList.ts', ({ useProgressiveList }) => useProgressiveList(rows));
  assert.equal(h.read().visible.length, 24);
  assert.equal(h.read().remaining, 190);
  for (let i = 0; i < 3; i++) h.read().loadMore();
  assert.equal(h.read().visible.length, 96);
  rows = rowsOf(215);
  h.render();
  assert.equal(h.read().visible.length, 96);
  rows = rowsOf(60);
  h.render();
  assert.equal(h.read().visible, rows);
  assert.equal(h.read().hasMore, false);
  rows = rowsOf(200);
  h.render();
  assert.equal(h.read().visible.length, 60);
  rows = [];
  h.render();
  rows = rowsOf(200);
  h.render();
  assert.equal(h.read().visible.length, 60, 'temporary empty data must not reset the loaded limit');
});

test('printing twice renders the full list synchronously and restores the original batch limit', () => {
  const rows = rowsOf(214);
  const h = mountHook('useProgressiveList.ts', ({ useProgressiveList }) => useProgressiveList(rows));
  h.read().loadMore();
  assert.equal(h.read().visible.length, 48);
  h.read().prepareForPrint();
  assert.equal(h.read().visible, rows);
  h.read().prepareForPrint();
  assert.equal(h.read().visible, rows);
  h.read().restoreAfterPrint();
  assert.equal(h.read().visible.length, 48);
  h.read().restoreAfterPrint();
  assert.equal(h.read().visible.length, 48);
  h.read().loadAll();
  assert.equal(h.read().visible, rows);
});

test('tick feedback expires even if identical quotes and callback references keep changing', () => {
  let price = 100;
  const h = mountHook('useTickFlash.ts', ({ useTickFlash }) => useTickFlash(
    [{ id: 'A', price }], (row) => row.id, (row) => row.price,
  ));
  assert.equal(Object.keys(h.read()).length, 0);
  price = 101;
  h.render();
  assert.equal(h.read().A, 'up');
  const timer = [...h.timers.keys()][0];
  h.render();
  assert.equal(h.read().A, 'up');
  assert.deepEqual([...h.timers.keys()], [timer], 'reference-only changes must not restart the clock');
  h.advance(700);
  assert.equal(Object.keys(h.read()).length, 0);
  assert.equal(h.timers.size, 0);
});

test('a later quote replaces the direction and its expiry cannot clear a newer flash', () => {
  let rows = [{ id: 'A', price: 100 }];
  const h = mountHook('useTickFlash.ts', ({ useTickFlash }) => useTickFlash(rows, (row) => row.id, (row) => row.price));
  rows = [{ id: 'A', price: 101 }];
  h.render();
  const oldCallback = [...h.timers.values()][0].fn;
  h.advance(100);
  rows = [{ id: 'A', price: 99 }];
  h.render();
  assert.equal(h.read().A, 'down');
  oldCallback();
  assert.equal(h.read().A, 'down');
  h.unmount();
  assert.equal(h.timers.size, 0, 'a stale callback must not discard ownership of the new timer');
});

test('removed or missing quotes return without a comparison against stale history', () => {
  let rows = [{ id: 'A', price: 100 }];
  const h = mountHook('useTickFlash.ts', ({ useTickFlash }) => useTickFlash(rows, (row) => row.id, (row) => row.price));
  rows = [];
  h.render();
  rows = [{ id: 'A', price: 200 }];
  h.render();
  assert.equal(Object.keys(h.read()).length, 0);
  rows = [{ id: 'A', price: null }];
  h.render();
  rows = [{ id: 'A', price: 300 }];
  h.render();
  assert.equal(Object.keys(h.read()).length, 0);
  rows = [{ id: 'A', price: 301 }];
  h.render();
  assert.equal(h.read().A, 'up');
  h.unmount();
  assert.equal(h.timers.size, 0);
});
