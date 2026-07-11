const PAGE_SELECTOR = '[data-motion-page]';
const ITEM_SELECTOR = '[data-motion-card], [data-motion-reveal]';
const LENS_SELECTOR = '[data-motion-lens]';
const SPOTLIGHT_SELECTOR = '[data-motion-spotlight]';
const CHART_SELECTOR = 'canvas, .instrument-chart-panel, .instrument-chart-stage, [data-chart-static], [data-motion-static]';

const PAGE_DURATION = 360;
const COMPACT_DURATION = 180;
const LENS_DURATION = 620;
const ITEM_STAGGER = 24;
const MAX_STAGGERED_ITEMS = 8;

let activeDispose = null;

function noOp() {}

function safeMatchMedia(query) {
  try {
    if (typeof window.matchMedia === 'function') return window.matchMedia(query);
  } catch (_) { /* unsupported media query */ }
  return { matches: false };
}

function saveDataEnabled() {
  try {
    const connection = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
    return connection?.saveData === true;
  } catch (_) {
    return false;
  }
}

function isElement(value) {
  return typeof Element !== 'undefined' && value instanceof Element;
}

function collectElements(node, selector) {
  if (!node || typeof node.querySelectorAll !== 'function') return [];
  const elements = [];
  if (isElement(node) && node.matches(selector)) elements.push(node);
  elements.push(...node.querySelectorAll(selector));
  return elements;
}

function isChartSurface(element) {
  if (!isElement(element)) return true;
  return element.matches(CHART_SELECTOR) || Boolean(element.closest(CHART_SELECTOR));
}

function sortByDocumentOrder(elements) {
  return [...elements].sort((left, right) => {
    if (left === right || typeof left.compareDocumentPosition !== 'function') return 0;
    const position = left.compareDocumentPosition(right);
    if (position & Node.DOCUMENT_POSITION_FOLLOWING) return -1;
    if (position & Node.DOCUMENT_POSITION_PRECEDING) return 1;
    return 0;
  });
}

function pageNamespace(element) {
  const page = element.closest?.(PAGE_SELECTOR);
  const explicit = page?.getAttribute('data-motion-page')?.trim();
  return explicit || document.body?.dataset?.route || 'page';
}

function stableMotionKey(element, kind) {
  const explicit = element.getAttribute('data-motion-key')?.trim();
  if (explicit) return `${pageNamespace(element)}:${kind}:${explicit}`;

  const identity = element.id
    || element.getAttribute('aria-labelledby')
    || [...element.classList].slice(0, 3).join('.')
    || element.tagName.toLowerCase();
  const parent = element.parentElement;
  const parentIdentity = parent?.id
    || (parent ? [...parent.classList].slice(0, 2).join('.') : '')
    || parent?.tagName?.toLowerCase()
    || 'root';
  const peers = parent
    ? [...parent.children].filter((child) => child.matches?.(kind === 'card' ? '[data-motion-card]' : `[data-motion-${kind}]`))
    : [];
  const index = Math.max(0, peers.indexOf(element));
  return `${pageNamespace(element)}:${kind}:${parentIdentity}:${identity}:${index}`;
}

function mediaListener(query, listener) {
  if (!query) return noOp;
  if (typeof query.addEventListener === 'function') {
    query.addEventListener('change', listener);
    return () => query.removeEventListener('change', listener);
  }
  if (typeof query.addListener === 'function') {
    query.addListener(listener);
    return () => query.removeListener(listener);
  }
  return noOp;
}

function baseVisualState(element) {
  try {
    const style = window.getComputedStyle(element);
    return {
      opacity: Number.isFinite(Number(style.opacity)) ? Number(style.opacity) : 1,
      transform: style.transform && style.transform !== 'none' ? style.transform : 'none',
    };
  } catch (_) {
    return { opacity: 1, transform: 'none' };
  }
}

function offsetTransform(base, distance, scale = 1) {
  const offset = `translate3d(0, ${distance}px, 0)${scale === 1 ? '' : ` scale(${scale})`}`;
  return base === 'none' ? offset : `${offset} ${base}`;
}

export function disposeMotion() {
  const dispose = activeDispose;
  activeDispose = null;
  try { dispose?.(); } catch (_) { /* motion is optional */ }
}

export function mountMotion(root = document.getElementById('app')) {
  disposeMotion();
  if (!root || typeof root.querySelectorAll !== 'function') return noOp;

  const reducedMotion = safeMatchMedia('(prefers-reduced-motion: reduce)');
  const finePointer = safeMatchMedia('(hover: hover) and (pointer: fine)');
  const seenItems = new Set();
  const seenLenses = new Set();
  const observedElements = new WeakSet();
  const animations = new Set();
  const timers = new Set();
  const mediaCleanups = [];
  let mutationObserver = null;
  let intersectionObserver = null;
  let pageEntered = false;
  let disposed = false;
  let activeSpotlight = null;
  let spotlightFrame = 0;
  let pointerX = 0;
  let pointerY = 0;

  const decorativeMotionAllowed = () => {
    try {
      return !disposed && !reducedMotion.matches && !document.hidden && !saveDataEnabled();
    } catch (_) {
      return false;
    }
  };

  const compactMotion = () => !finePointer.matches;

  const stopAnimations = () => {
    animations.forEach((animation) => {
      try { animation.cancel(); } catch (_) { /* already finished */ }
    });
    animations.clear();
  };

  const play = (element, keyframes, options) => {
    if (!decorativeMotionAllowed() || isChartSurface(element) || typeof element.animate !== 'function') return;
    try {
      const animation = element.animate(keyframes, { fill: 'both', ...options });
      animations.add(animation);
      const release = () => {
        if (!animations.delete(animation)) return;
        try { animation.cancel(); } catch (_) { /* effect already released */ }
      };
      animation.finished.then(release, release);
    } catch (_) { /* WAAPI unsupported or target detached */ }
  };

  const animatePage = (page) => {
    if (pageEntered) return;
    pageEntered = true;
    if (!decorativeMotionAllowed()) return;

    const containsChart = Boolean(page.querySelector?.(CHART_SELECTOR));
    const compact = compactMotion() || containsChart;
    const base = baseVisualState(page);
    const from = compact
      ? { opacity: 0 }
      : { opacity: 0, transform: offsetTransform(base.transform, 10) };
    const to = compact
      ? { opacity: base.opacity }
      : { opacity: base.opacity, transform: base.transform };
    play(page, [from, to], {
      duration: compact ? COMPACT_DURATION : PAGE_DURATION,
      easing: 'cubic-bezier(0.16, 1, 0.3, 1)',
    });
  };

  const itemMeta = (element) => {
    const kind = element.hasAttribute('data-motion-card') ? 'card' : 'reveal';
    return { element, kind, key: stableMotionKey(element, kind) };
  };

  const animateItemBatch = (elements) => {
    const unique = [];
    const batchKeys = new Set();
    sortByDocumentOrder(elements).forEach((element) => {
      if (!element?.isConnected || isChartSurface(element)) return;
      const meta = itemMeta(element);
      if (seenItems.has(meta.key) || batchKeys.has(meta.key)) return;
      batchKeys.add(meta.key);
      unique.push(meta);
    });

    unique.forEach(({ element, key }, index) => {
      seenItems.add(key);
      intersectionObserver?.unobserve(element);
      observedElements.delete(element);
      if (!decorativeMotionAllowed() || index >= MAX_STAGGERED_ITEMS) return;

      const compact = compactMotion();
      const base = baseVisualState(element);
      const from = compact
        ? { opacity: 0 }
        : { opacity: 0, transform: offsetTransform(base.transform, 8, 0.99) };
      const to = compact
        ? { opacity: base.opacity }
        : { opacity: base.opacity, transform: base.transform };
      play(element, [from, to], {
        duration: compact ? COMPACT_DURATION : 300,
        delay: Math.min(index * ITEM_STAGGER, 180),
        easing: 'cubic-bezier(0.25, 1, 0.5, 1)',
      });
    });
  };

  const observeItem = (element) => {
    if (!isElement(element) || isChartSurface(element)) return;
    const { key } = itemMeta(element);
    if (seenItems.has(key) || observedElements.has(element)) return;
    if (!decorativeMotionAllowed()) {
      seenItems.add(key);
      return;
    }
    if (intersectionObserver) {
      observedElements.add(element);
      intersectionObserver.observe(element);
    } else {
      animateItemBatch([element]);
    }
  };

  const startLens = (element) => {
    if (!isElement(element) || isChartSurface(element)) return;
    if (element.hidden || element.closest('[hidden]')) return;
    const key = stableMotionKey(element, 'lens');
    if (seenLenses.has(key)) return;
    seenLenses.add(key);
    if (!decorativeMotionAllowed() || compactMotion()) return;
    try {
      element.classList.add('is-lens-sweeping');
      const timer = window.setTimeout(() => {
        timers.delete(timer);
        element.classList.remove('is-lens-sweeping');
      }, LENS_DURATION);
      timers.add(timer);
    } catch (_) { /* detached node */ }
  };

  const scanTree = (node) => {
    if (disposed || !node) return;
    try {
      const page = isElement(node) && node.matches(PAGE_SELECTOR)
        ? node
        : node.querySelector?.(PAGE_SELECTOR);
      if (page) animatePage(page);
      collectElements(node, LENS_SELECTOR).forEach(startLens);
      collectElements(node, ITEM_SELECTOR).forEach(observeItem);
    } catch (_) { /* malformed or detached subtree */ }
  };

  const unobserveTree = (node) => {
    if (!intersectionObserver || !node) return;
    collectElements(node, ITEM_SELECTOR).forEach((element) => {
      intersectionObserver.unobserve(element);
      observedElements.delete(element);
    });
  };

  const resetSpotlight = (surface = activeSpotlight) => {
    if (!surface) return;
    try {
      surface.classList.remove('is-spotlight-active');
      surface.style.removeProperty('--spot-x');
      surface.style.removeProperty('--spot-y');
    } catch (_) { /* detached node */ }
    if (activeSpotlight === surface) activeSpotlight = null;
  };

  const clearLensEffects = () => {
    collectElements(root, `${LENS_SELECTOR}.is-lens-sweeping`).forEach((element) => {
      element.classList.remove('is-lens-sweeping');
    });
  };

  const flushSpotlight = () => {
    spotlightFrame = 0;
    const surface = activeSpotlight;
    if (!surface?.isConnected || !decorativeMotionAllowed() || !finePointer.matches) {
      resetSpotlight(surface);
      return;
    }
    try {
      const rect = surface.getBoundingClientRect();
      const x = Math.max(0, Math.min(rect.width, pointerX - rect.left));
      const y = Math.max(0, Math.min(rect.height, pointerY - rect.top));
      surface.style.setProperty('--spot-x', `${x}px`);
      surface.style.setProperty('--spot-y', `${y}px`);
      surface.classList.add('is-spotlight-active');
    } catch (_) {
      resetSpotlight(surface);
    }
  };

  const onPointerMove = (event) => {
    if (event.pointerType === 'touch' || !decorativeMotionAllowed() || !finePointer.matches) return;
    const target = isElement(event.target) ? event.target : event.target?.parentElement;
    if (!target || target.closest(CHART_SELECTOR)) return;
    const surface = target.closest(SPOTLIGHT_SELECTOR);
    if (!surface || !root.contains(surface)) {
      resetSpotlight();
      return;
    }
    if (activeSpotlight && activeSpotlight !== surface) resetSpotlight(activeSpotlight);
    activeSpotlight = surface;
    pointerX = event.clientX;
    pointerY = event.clientY;
    if (!spotlightFrame) spotlightFrame = window.requestAnimationFrame(flushSpotlight);
  };

  const onPointerOut = (event) => {
    const target = isElement(event.target) ? event.target : event.target?.parentElement;
    const surface = target?.closest?.(SPOTLIGHT_SELECTOR);
    if (!surface || surface !== activeSpotlight) return;
    const related = event.relatedTarget;
    if (related && typeof Node !== 'undefined' && related instanceof Node && surface.contains(related)) return;
    resetSpotlight(surface);
  };

  const onPointerLeave = () => resetSpotlight();

  const onMotionPreferenceChanged = () => {
    if (reducedMotion.matches || !finePointer.matches) resetSpotlight();
    if (reducedMotion.matches) {
      stopAnimations();
      clearLensEffects();
    }
  };

  const onVisibilityChanged = () => {
    if (!document.hidden) return;
    stopAnimations();
    resetSpotlight();
    clearLensEffects();
  };

  const dispose = () => {
    if (disposed) return;
    disposed = true;
    mutationObserver?.disconnect();
    intersectionObserver?.disconnect();
    mediaCleanups.forEach((cleanup) => cleanup());
    mediaCleanups.length = 0;
    document.removeEventListener('visibilitychange', onVisibilityChanged);
    root.removeEventListener('pointermove', onPointerMove);
    root.removeEventListener('pointerout', onPointerOut);
    root.removeEventListener('pointerleave', onPointerLeave);
    if (spotlightFrame) window.cancelAnimationFrame(spotlightFrame);
    spotlightFrame = 0;
    resetSpotlight();
    timers.forEach((timer) => window.clearTimeout(timer));
    timers.clear();
    clearLensEffects();
    stopAnimations();
    if (activeDispose === dispose) activeDispose = null;
  };

  activeDispose = dispose;

  try {
    if (typeof IntersectionObserver === 'function') {
      intersectionObserver = new IntersectionObserver((entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting || entry.intersectionRatio > 0)
          .map((entry) => entry.target);
        if (visible.length) animateItemBatch(visible);
      }, { threshold: 0.08, rootMargin: '0px 0px -8% 0px' });
    }

    if (typeof MutationObserver === 'function') {
      mutationObserver = new MutationObserver((mutations) => {
        mutations.forEach((mutation) => {
          if (mutation.type === 'attributes') {
            scanTree(mutation.target);
            return;
          }
          mutation.removedNodes.forEach(unobserveTree);
          mutation.addedNodes.forEach(scanTree);
        });
      });
      mutationObserver.observe(root, {
        childList: true,
        subtree: true,
        attributes: true,
        attributeFilter: [
          'data-motion-page',
          'data-motion-reveal',
          'data-motion-card',
          'data-motion-key',
          'data-motion-lens',
          'data-motion-spotlight',
          'hidden',
        ],
      });
    }

    root.addEventListener('pointermove', onPointerMove, { passive: true });
    root.addEventListener('pointerout', onPointerOut, { passive: true });
    root.addEventListener('pointerleave', onPointerLeave, { passive: true });
    document.addEventListener('visibilitychange', onVisibilityChanged);
    mediaCleanups.push(mediaListener(reducedMotion, onMotionPreferenceChanged));
    mediaCleanups.push(mediaListener(finePointer, onMotionPreferenceChanged));
    scanTree(root);
  } catch (_) {
    dispose();
    return noOp;
  }

  return dispose;
}
