import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

// Execute the shipped hook with deterministic frames, timers and React effects.
// The race cases cannot be proven by matching source text or still screenshots.
function harness() {
  const slots = [];
  let cursor = 0;
  let renderHook;
  let result;
  let rendering = false;
  let queued = false;
  let now = 0;
  let nextId = 0;
  let reduced = false;
  let onRender = () => {};
  const frames = new Map();
  const timers = new Map();
  const mediaListeners = new Set();

  function render() {
    if (rendering) { queued = true; return; }
    rendering = true;
    let passes = 0;
    do {
      assert.ok(++passes < 30, 'effects must settle');
      queued = false;
      cursor = 0;
      result = renderHook();
      onRender(result);
      for (const slot of slots) {
        if (!slot?.dirty) continue;
        slot.dirty = false;
        slot.cleanup?.();
        slot.cleanup = slot.create();
      }
    } while (queued);
    rendering = false;
  }
  const React = {
    useState(initial) {
      const index = cursor++;
      const slot = slots[index] ??= { value: initial };
      slot.set ??= (next) => {
        const value = typeof next === 'function' ? next(slot.value) : next;
        if (Object.is(value, slot.value)) return;
        slot.value = value;
        render();
      };
      return [slot.value, slot.set];
    },
    useRef(initial) {
      const index = cursor++;
      return slots[index] ??= { current: initial };
    },
    useEffect(create, deps) {
      const index = cursor++;
      const slot = slots[index] ??= {};
      if (!slot.deps || deps.some((dep, i) => !Object.is(dep, slot.deps[i]))) {
        Object.assign(slot, { create, deps, dirty: true });
      }
    },
    useCallback(fn, deps) {
      const index = cursor++;
      const slot = slots[index] ??= {};
      if (!slot.deps || deps.some((dep, i) => !Object.is(dep, slot.deps[i]))) {
        Object.assign(slot, { value: fn, deps });
      }
      return slot.value;
    },
  };
  const runtime = {
    window: {
      setTimeout(fn, delay) {
        const id = ++nextId;
        timers.set(id, { at: now + delay, fn });
        return id;
      },
      clearTimeout(id) { timers.delete(id); },
      matchMedia: () => ({
        matches: reduced,
        addEventListener: (_event, callback) => mediaListeners.add(callback),
        removeEventListener: (_event, callback) => mediaListeners.delete(callback),
      }),
    },
    performance: { now: () => now },
    requestAnimationFrame(fn) {
      const id = ++nextId;
      frames.set(id, fn);
      return id;
    },
    cancelAnimationFrame(id) { frames.delete(id); },
  };
  runtime.window.requestAnimationFrame = runtime.requestAnimationFrame;
  runtime.window.cancelAnimationFrame = runtime.cancelAnimationFrame;
  React.useLayoutEffect = React.useEffect;
  React.useId = () => 'test-tooltip';
  React.useSyncExternalStore = (subscribe, getSnapshot) => {
    const [snapshot, setSnapshot] = React.useState(getSnapshot());
    React.useEffect(() => subscribe(() => setSnapshot(getSnapshot())), [subscribe, getSnapshot]);
    return snapshot;
  };
  return {
    React, runtime, frames, timers, mediaListeners,
    mount(fn, afterRender) {
      renderHook = fn;
      onRender = afterRender ?? onRender;
      render();
    },
    render,
    value: () => result,
    reduce(value) { reduced = value; mediaListeners.forEach((listener) => listener()); render(); },
    frame(at) {
      now = at;
      const scheduled = [...frames.values()];
      frames.clear();
      scheduled.forEach((fn) => fn(now));
    },
    timeout(at) {
      now = at;
      for (const [id, timer] of timers) {
        if (timer.at <= now) { timers.delete(id); timer.fn(); }
      }
    },
    unmount() { slots.forEach((slot) => slot?.cleanup?.()); },
  };
}

const jsx = (type, props) => ({ type, props });
function loadSource(relative, imports = {}, globals = {}) {
  const source = fs.readFileSync(new URL(`../src/${relative}`, import.meta.url), 'utf8');
  const compiled = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const module = { exports: {} };
  vm.runInNewContext(compiled, {
    module, exports: module.exports,
    require(id) {
      if (id === 'react/jsx-runtime') return { jsx, jsxs: jsx };
      if (id === '@/lib/utils') return { cn: (...parts) => parts.filter(Boolean).join(' ') };
      if (id === '@/components/icons') return { default: () => null };
      if (id.endsWith('i18n/core.ts')) return { t: (text) => text };
      if (id in imports) return imports[id];
      throw new Error(`Unexpected import ${id}`);
    },
    ...globals,
  });
  return module.exports;
}
function countHarness() {
  const h = harness();
  let hidden = false;
  const visibilityListeners = new Set();
  const document = {
    get hidden() { return hidden; },
    addEventListener: (_name, callback) => visibilityListeners.add(callback),
    removeEventListener: (_name, callback) => visibilityListeners.delete(callback),
  };
  const motionPreference = loadSource('hooks/usePrefersReducedMotion.ts', { react: h.React }, h.runtime);
  const { useCountUp } = loadSource('hooks/useCountUp.ts', {
    react: h.React, './usePrefersReducedMotion': motionPreference,
  }, { ...h.runtime, document });
  return { ...h, useCountUp, visibilityListeners,
    hide(value) { hidden = value; visibilityListeners.forEach((listener) => listener()); },
  };
}
function loadTransitions(h) {
  const motionPreference = loadSource('hooks/usePrefersReducedMotion.ts', { react: h.React }, h.runtime);
  return loadSource('lib/transitions.ts', {
    react: h.React, '../hooks/usePrefersReducedMotion.ts': motionPreference,
  }, h.runtime);
}
function allNodes(tree) {
  if (tree === null || tree === undefined || typeof tree !== 'object') return [];
  if (Array.isArray(tree)) return tree.flatMap(allNodes);
  return [tree, ...allNodes(tree.props?.children)];
}
function textOf(tree) {
  if (tree === null || tree === undefined || typeof tree === 'boolean') return '';
  if (typeof tree !== 'object') return String(tree);
  if (Array.isArray(tree)) return tree.map(textOf).join('');
  return textOf(tree.props?.children);
}

test('first render shows the real number and schedules no count from zero', () => {
  const h = countHarness();
  h.mount(() => h.useCountUp(143.72));
  assert.equal(h.value(), 143.72);
  assert.equal(h.frames.size, 0);
  assert.equal(h.timers.size, 0);
});

test('an interrupted update resumes at the currently displayed value', () => {
  const h = countHarness();
  let target = 100;
  h.mount(() => h.useCountUp(target, 1000));
  target = 200;
  h.render();
  h.frame(100);
  const visible = h.value();
  assert.ok(visible > 100 && visible < 200);
  const cancelled = [...h.frames.values()][0];
  target = 300;
  h.render();
  cancelled(900);
  assert.equal(h.value(), visible, 'a cancelled callback cannot overwrite a new generation');
  h.frame(100);
  assert.equal(h.value(), visible, 'the next animation starts from its on-screen position');
  h.frame(1100);
  assert.equal(h.value(), 300);
  assert.equal(h.frames.size, 0);
  assert.equal(h.timers.size, 0);
});

test('background timeout cancels its queued frame and settles exactly once', () => {
  const h = countHarness();
  let target = 10;
  h.mount(() => h.useCountUp(target, 1000));
  target = 20;
  h.render();
  const lateFrame = [...h.frames.values()][0];
  h.timeout(1080);
  assert.equal(h.value(), 20);
  assert.equal(h.frames.size, 0);
  lateFrame(100);
  assert.equal(h.value(), 20, 'a delayed old frame must not restore an intermediate number');
});

test('hiding the page immediately finishes a count and removes its pending work', () => {
  const h = countHarness();
  let target = 10;
  h.mount(() => h.useCountUp(target, 1000));
  target = 20;
  h.render();
  h.frame(100);
  const lateFrame = [...h.frames.values()][0];
  h.hide(true);
  assert.equal(h.value(), 20);
  assert.equal(h.frames.size, 0);
  assert.equal(h.timers.size, 0);
  lateFrame(200);
  assert.equal(h.value(), 20);
  target = 30;
  h.render();
  assert.equal(h.value(), 30, 'an update received while hidden does not start a new animation');
  h.unmount();
  assert.equal(h.visibilityListeners.size, 0);
});

test('opposite-sign finite extremes do not overflow during interpolation', () => {
  const h = countHarness();
  let target = Number.MAX_VALUE;
  h.mount(() => h.useCountUp(target, 1000));
  target = -Number.MAX_VALUE;
  h.render();
  h.frame(100);
  assert.ok(Number.isFinite(h.value()));
  assert.notEqual(h.value(), target, 'the update remains a transition rather than an overflow fallback');
  h.frame(1000);
  assert.equal(h.value(), target);
  h.unmount();
});

test('enabling reduced motion during an update immediately settles and removes scheduled work', () => {
  const h = countHarness();
  let target = 5;
  h.mount(() => h.useCountUp(target));
  target = 25;
  h.render();
  h.frame(50);
  h.reduce(true);
  assert.equal(h.value(), 25);
  assert.equal(h.frames.size, 0);
  assert.equal(h.timers.size, 0);
  assert.equal(h.mediaListeners.size, 1, 'the shipped hook subscribes to preference changes');
  h.unmount();
  assert.equal(h.mediaListeners.size, 0, 'preference subscription is removed on unmount');
});

test('missing numbers do not interpolate to zero and recovery presents a real value', () => {
  const h = countHarness();
  let target = Number.NaN;
  const commits = [];
  h.mount(() => h.useCountUp(target), (value) => commits.push({ target, value }));
  assert.ok(Number.isNaN(h.value()));
  target = 42;
  h.render();
  assert.equal(h.value(), 42);
  assert.equal(commits.find((commit) => commit.target === 42).value, 42,
    'the first render after recovery must not expose the previous NaN state');
  assert.equal(h.frames.size, 0);
  target = Number.POSITIVE_INFINITY;
  h.render();
  assert.equal(h.value(), Number.POSITIVE_INFINITY);
  assert.equal(h.frames.size, 0);
});

test('zero duration and unmount leave no pending animation', () => {
  const h = countHarness();
  let target = 10;
  let duration = 0;
  h.mount(() => h.useCountUp(target, duration));
  target = 20;
  h.render();
  assert.equal(h.value(), 20);
  duration = 900;
  target = 30;
  h.render();
  assert.equal(h.frames.size, 1);
  h.unmount();
  assert.equal(h.frames.size, 0);
  assert.equal(h.timers.size, 0);
});

test('StatCard exposes the final value to readers and keeps animated interpolation decorative', () => {
  const { default: StatCard } = loadSource('components/shared/StatCard.tsx', {
    '@/hooks/useCountUp': { useCountUp: () => 124.5 },
  });
  const tree = StatCard({ label: '成交额', value: 150.25, digits: 2, suffix: '美元' });
  const nodes = allNodes(tree);
  assert.equal(textOf(nodes.find((node) => node.props?.className === 'sr-only')), '150.25美元');
  assert.equal(textOf(nodes.find((node) => node.props?.['aria-hidden'] === 'true')), '124.50美元');
  for (const missing of [Number.NaN, Number.POSITIVE_INFINITY, null]) {
    const empty = StatCard({ label: '成交额', value: missing, digits: 2 });
    assert.equal(textOf(allNodes(empty).find((node) => node.props?.className === 'sr-only')), '—');
    assert.equal(textOf(allNodes(empty).find((node) => node.props?.['aria-hidden'] === 'true')), '—');
  }
});

test('HorizontalScroller reads the current motion preference on every button press', () => {
  const h = harness();
  const calls = [];
  const scroller = {
    clientWidth: 400, scrollWidth: 1200, scrollLeft: 0,
    firstElementChild: null,
    addEventListener() {}, removeEventListener() {},
    scrollBy(options) { calls.push(options); },
  };
  const { default: HorizontalScroller } = loadSource('components/shared/HorizontalScroller.tsx', {
    react: h.React,
    'framer-motion': { motion: { div: 'div' } },
    '@/lib/scrollEdges': { computeScrollEdges: (left, width, viewport) => ({ left: left > 1, right: left < width - viewport - 1 }) },
  }, h.runtime);
  h.mount(() => HorizontalScroller({ children: 'content', label: '主题' }), (tree) => {
    allNodes(tree).forEach((node) => { if (node.props?.ref) node.props.ref.current = scroller; });
  });
  const pressRight = () => allNodes(h.value()).find((node) => node.type === 'button').props.onClick();
  pressRight();
  assert.equal(calls.at(-1).behavior, 'smooth');
  assert.equal(calls.at(-1).left, 340);
  h.reduce(true);
  pressRight();
  assert.equal(calls.at(-1).behavior, 'instant');
  assert.equal(calls.at(-1).left, 340);
  h.unmount();
});

test('overlay phases preserve their closing clock and cancel it when reopened', () => {
  const h = harness();
  const { useOverlayPhase } = loadTransitions(h);
  let open = false;
  h.mount(() => useOverlayPhase(open, 150));
  assert.equal(h.value(), 'closed');
  assert.equal(h.timers.size, 0);
  open = true;
  h.render();
  assert.equal(h.value(), 'preopen');
  h.frame(16);
  assert.equal(h.value(), 'open');
  open = false;
  h.render();
  assert.equal(h.value(), 'closing');
  open = true;
  h.render();
  assert.equal(h.timers.size, 0);
  h.frame(32);
  h.timeout(300);
  assert.equal(h.value(), 'open');
  open = false;
  h.render();
  h.timeout(450);
  assert.equal(h.value(), 'closed');
});

test('reduced motion closes overlays immediately and cancels a pending close', () => {
  const h = harness();
  const { useOverlayPhase } = loadTransitions(h);
  let open = true;
  h.mount(() => useOverlayPhase(open, 350));
  h.frame(16);
  open = false;
  h.render();
  assert.equal(h.value(), 'closing');
  h.reduce(true);
  assert.equal(h.value(), 'closed');
  assert.equal(h.timers.size, 0);
  open = true;
  h.render();
  assert.equal(h.value(), 'open');
  assert.equal(h.frames.size, 0);
  open = false;
  h.render();
  assert.equal(h.value(), 'closed');
});

test('clearing field feedback before its animation frame prevents a late shake', () => {
  const h = harness();
  const { useCatalogShake } = loadTransitions(h);
  h.mount(() => useCatalogShake(1200));
  h.value().play({ message: true });
  assert.equal(h.value().error, true);
  assert.equal(h.value().shaking, false);
  h.frame(16);
  assert.equal(h.value().shaking, true);
  h.value().play();
  assert.equal(h.value().shaking, false);
  h.value().clear();
  h.frame(32);
  assert.equal(h.value().error, false);
  assert.equal(h.value().shaking, false);
  assert.equal(h.frames.size, 0);
  assert.equal(h.timers.size, 0);
  h.value().play();
  h.unmount();
  assert.equal(h.frames.size, 0);
  assert.equal(h.timers.size, 0);
});

test('skeleton loading reversals cancel the previous reveal clock', () => {
  const h = harness();
  const { SkeletonReveal } = loadSource('components/shared/Skeleton.tsx', {
    react: h.React,
    '@/lib/transitions': { readRootDurationMs: () => 400 },
  }, h.runtime);
  let loading = true;
  const children = jsx('div', { children: 'real content' });
  h.mount(() => SkeletonReveal({ loading, skeleton: 'loading', children }));
  assert.equal(h.value().props['data-state'], 'loading');
  loading = false;
  h.render();
  assert.equal(h.value().props['data-state'], 'reveal');
  assert.equal(allNodes(h.value()).find((node) => node.props?.className === 't-skel-content').props.children, children);
  loading = true;
  h.render();
  assert.equal(h.value().props['data-state'], 'loading');
  assert.equal(h.timers.size, 0);
  h.timeout(450);
  assert.equal(h.value().props['data-state'], 'loading');
  loading = false;
  h.render();
  h.timeout(850);
  assert.equal(h.value().props['data-state'], 'done');
  assert.equal(allNodes(h.value()).find((node) => node.props?.className === 't-skel-content').props.children, children);
});

test('Escape dismisses a focused tooltip without closing its surrounding dialog', () => {
  const h = harness();
  const tip = { style: {}, offsetHeight: 80, classList: { add() {} } };
  const trigger = { getBoundingClientRect: () => ({ left: 100, right: 116, width: 16, height: 16, top: 100, bottom: 116 }) };
  const document = { body: {}, documentElement: { clientWidth: 390, clientHeight: 700 }, addEventListener() {}, removeEventListener() {} };
  Object.assign(h.runtime.window, { addEventListener() {}, removeEventListener() {} });
  const { default: InfoHint } = loadSource('components/shared/InfoHint.tsx', {
    react: h.React, 'react-dom': { createPortal: (child) => child },
  }, { ...h.runtime, document });
  h.mount(() => InfoHint({ hint: { title: 'Score', body: 'Explanation' } }), (tree) => {
    allNodes(tree).forEach((node) => {
      if (!node.props?.ref) return;
      node.props.ref.current = node.props.role === 'tooltip' ? tip : trigger;
    });
  });
  allNodes(h.value()).find((node) => node.props?.role === 'button').props.onFocus();
  assert.ok(allNodes(h.value()).some((node) => node.props?.role === 'tooltip'));
  assert.ok(Number.parseFloat(tip.style.left) >= 8);
  let stopped = false;
  h.value().props.onKeyDown({ key: 'Escape', stopPropagation() { stopped = true; } });
  assert.equal(stopped, true);
  assert.equal(allNodes(h.value()).some((node) => node.props?.role === 'tooltip'), false);
  assert.equal(allNodes(h.value()).find((node) => node.props?.role === 'button').props['aria-expanded'], false);
  h.unmount();
});
