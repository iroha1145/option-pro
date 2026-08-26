/**
 * transitions.dev orchestration helpers.
 * Durations stay in CSS custom properties; these helpers only read them and
 * toggle the documented class / attribute hooks (is-open, is-closing, is-shaking).
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

export type OverlayPhase = 'closed' | 'preopen' | 'open' | 'closing';

/** Parse a CSS duration ("250ms", "0.25s") to milliseconds. */
export function parseDurationMs(value: string, fallback: number): number {
  const trimmed = value.trim();
  if (!trimmed) return fallback;
  const n = parseFloat(trimmed);
  if (!Number.isFinite(n)) return fallback;
  if (trimmed.endsWith('ms')) return n;
  if (trimmed.endsWith('s')) return n * 1000;
  return n;
}

/** Read a custom property assignment from a stylesheet source string. */
export function readCssVar(cssText: string, name: string): string | null {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const re = new RegExp(`${escaped}\\s*:\\s*([^;\\n]+);`);
  const match = cssText.match(re);
  return match ? match[1].trim() : null;
}

export function overlayTiming(
  cssText: string,
  openVar: string,
  closeVar: string,
  openFallback: number,
  closeFallback: number,
): { open: number; close: number } {
  return {
    open: parseDurationMs(readCssVar(cssText, openVar) ?? '', openFallback),
    close: parseDurationMs(readCssVar(cssText, closeVar) ?? '', closeFallback),
  };
}

export function overlayCloseTimeoutMs(cssText: string, closeVar: string, fallback: number): number {
  return parseDurationMs(readCssVar(cssText, closeVar) ?? '', fallback);
}

export function overlayClassName(phase: OverlayPhase): string {
  if (phase === 'open') return 'is-open';
  if (phase === 'closing') return 'is-closing';
  return '';
}

export function overlayDataOpen(phase: OverlayPhase): 'true' | 'false' {
  return phase === 'open' ? 'true' : 'false';
}

export function overlayMounted(phase: OverlayPhase): boolean {
  return phase !== 'closed';
}

/** Keep the node mounted while `open` or the close clock is still playing. */
export function overlayVisible(open: boolean, phase: OverlayPhase): boolean {
  return open || phase !== 'closed';
}

/** Read a duration custom property from :root at runtime. */
export function readRootDurationMs(name: string, fallback: number): number {
  if (typeof document === 'undefined') return fallback;
  return parseDurationMs(getComputedStyle(document.documentElement).getPropertyValue(name), fallback);
}

/**
 * Keep the overlay in the DOM through the close clock so `.is-closing` can play.
 * First paint after `open` stays at rest (`closed`/`preopen`); the next frame
 * flips to `open` so the catalog CSS actually tweens.
 */
export function useOverlayPhase(open: boolean, closeMs: number): OverlayPhase {
  const [phase, setPhase] = useState<OverlayPhase>(open ? 'open' : 'closed');

  useLayoutEffect(() => {
    if (open) {
      setPhase('preopen');
      const id = window.requestAnimationFrame(() => setPhase('open'));
      return () => window.cancelAnimationFrame(id);
    }
    setPhase((prev) => (prev === 'closed' ? 'closed' : 'closing'));
    const timer = window.setTimeout(() => setPhase('closed'), Math.max(0, closeMs));
    return () => window.clearTimeout(timer);
  }, [open, closeMs]);

  return phase;
}

/**
 * 把滑行指示器写到目标格的位置/尺寸上。`animate=false` 用于首绘与换批：
 * 先掐断过渡再写、强制回流后还原，避免从 translate(0)/size:0 补间过来。
 * 轴向由调用点决定——横向标签条是 x（translateX + width），纵向结果列表
 * 是 y（translateY + height）。
 *
 * 注意：掐断过渡期间写入的**任何**属性都不会补间，所以调用方若还想让
 * opacity 淡入，必须在本函数返回之后再写 opacity。
 */
export function placeGlide(
  el: HTMLElement,
  rect: { offset: number; size: number },
  opts: { axis: 'x' | 'y'; animate: boolean },
): void {
  const transform = opts.axis === 'x' ? `translateX(${rect.offset}px)` : `translateY(${rect.offset}px)`;
  const sizeProp = opts.axis === 'x' ? 'width' : 'height';
  if (!opts.animate) {
    const prev = el.style.transition;
    el.style.transition = 'none';
    el.style.transform = transform;
    el.style.setProperty(sizeProp, `${rect.size}px`);
    void el.offsetWidth;
    el.style.transition = prev;
    return;
  }
  el.style.transform = transform;
  el.style.setProperty(sizeProp, `${rect.size}px`);
}

/** Remove → reflow → add `.is-shaking` so the keyframe always restarts. */
export function replayShake(el: HTMLElement): void {
  el.classList.remove('is-shaking');
  void el.offsetWidth;
  el.classList.add('is-shaking');
}

/**
 * Catalog error-shake classes for the wrap + the bordered input.
 * These strings must land in React `className` — a later commit of
 * `className={cn(...)}` would wipe classList-only toggles.
 * `withMessage` keeps wrap/input orthogonal (catalog 12): the input owns
 * border + shake, the wrap owns the inline message reveal — a server-side
 * failure shakes the field without duplicating the live-region text below.
 */
export function catalogShakeStateClasses(
  error: boolean,
  shaking: boolean,
  withMessage = true,
): { wrap: string; input: string } {
  return {
    wrap: error && withMessage ? 'is-error' : '',
    input: [error ? 'is-error' : '', shaking ? 'is-shaking' : ''].filter(Boolean).join(' '),
  };
}

/** React owner for the catalog shake: state → className, reflow, then replay. */
export function useCatalogShake(holdMs = 1200) {
  const [error, setError] = useState(false);
  const [shaking, setShaking] = useState(false);
  const [withMessage, setWithMessage] = useState(false);
  const inputRef = useRef<HTMLDivElement>(null);
  const holdTimer = useRef(0);

  const play = useCallback(
    (opts?: { message?: boolean }) => {
      setError(true);
      setShaking(false);
      setWithMessage(opts?.message ?? false);
      if (holdTimer.current) window.clearTimeout(holdTimer.current);
      holdTimer.current = window.setTimeout(() => {
        setError(false);
        setShaking(false);
        holdTimer.current = 0;
      }, holdMs);
    },
    [holdMs],
  );

  const clear = useCallback(() => {
    if (holdTimer.current) window.clearTimeout(holdTimer.current);
    holdTimer.current = 0;
    setError(false);
    setShaking(false);
  }, []);

  useLayoutEffect(() => {
    if (!error || shaking) return;
    const el = inputRef.current;
    if (el) void el.offsetWidth;
    setShaking(true);
  }, [error, shaking]);

  useEffect(
    () => () => {
      if (holdTimer.current) window.clearTimeout(holdTimer.current);
    },
    [],
  );

  return {
    error,
    shaking,
    inputRef,
    classes: catalogShakeStateClasses(error, shaking, withMessage),
    play,
    clear,
  };
}

export function shakeDurationMs(cssText: string): number {
  const a = parseDurationMs(readCssVar(cssText, '--shake-dur-a') ?? '', 80);
  const b = parseDurationMs(readCssVar(cssText, '--shake-dur-b') ?? '', 60);
  return a * 2 + b * 2;
}
