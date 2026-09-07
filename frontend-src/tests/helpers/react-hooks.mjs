export function createReactStub() {
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
    rerender: render,
    unmount() { effects.forEach((effect) => effect.cleanup?.()); },
    mount(fn, ...args) {
      renderFn = fn;
      renderArgs = args;
      render();
      return () => lastResult;
    },
  };
}
