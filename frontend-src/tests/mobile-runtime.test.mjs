import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

function loadHook(name, react, globals) {
  const source = fs.readFileSync(new URL(`../src/hooks/${name}.ts`, import.meta.url), 'utf8');
  const exports = {};
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
  } }).outputText, { exports, require: () => react, ...globals });
  return exports;
}

test('mobile consumers share one native listener and react to orientation changes', () => {
  let matches = true;
  let nativeListener;
  let added = 0;
  let removed = 0;
  const stores = [];
  const { useIsMobile } = loadHook('use-mobile', {
    useSyncExternalStore(subscribe, snapshot, serverSnapshot) {
      stores.push({ subscribe, snapshot, serverSnapshot });
      return snapshot();
    },
  }, { window: { matchMedia: () => ({
    get matches() { return matches; },
    addEventListener(_type, listener) { nativeListener = listener; added++; },
    removeEventListener(_type, listener) { assert.equal(listener, nativeListener); removed++; },
  }) } });
  for (let i = 0; i < 100; i++) assert.equal(useIsMobile(), true);
  let updates = 0;
  const stop = stores.map(store => store.subscribe(() => { updates++; assert.equal(store.snapshot(), false); }));
  assert.equal(added, 1);
  matches = false;
  nativeListener();
  assert.equal(updates, 100);
  stop.slice(1).forEach(fn => fn());
  assert.equal(removed, 0);
  stop[0]();
  assert.equal(removed, 1);
  assert.equal(stores[0].serverSnapshot(), false);
});

test('clocks stop while hidden, resume at current time, and clean up on unmount', () => {
  let now = 1000;
  let value;
  let cleanup;
  let timer;
  let onVisibility;
  let hidden = false;
  const { useNow } = loadHook('useNow', {
    useState: initial => { value = initial(); return [value, next => { value = next; }]; },
    useEffect: effect => { cleanup = effect(); },
  }, {
    Date: { now: () => now },
    setInterval: fn => { timer = fn; return 1; },
    clearInterval: () => { timer = undefined; },
    document: {
      get hidden() { return hidden; },
      addEventListener: (_type, fn) => { onVisibility = fn; },
      removeEventListener: (_type, fn) => { assert.equal(fn, onVisibility); onVisibility = undefined; },
    },
  });
  useNow();
  now = 2000; timer(); assert.equal(value, 2000);
  hidden = true; onVisibility(); assert.equal(timer, undefined);
  now = 50_000; hidden = false; onVisibility(); assert.equal(value, 50_000);
  assert.equal(typeof timer, 'function');
  cleanup(); assert.equal(timer, undefined); assert.equal(onVisibility, undefined);
  useNow(0); assert.equal(timer, undefined); assert.equal(onVisibility, undefined);
});
