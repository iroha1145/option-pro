/**
 * transitions.dev orchestration helpers.
 * Durations stay in CSS custom properties; these helpers only read them and
 * toggle the documented class / attribute hooks (is-open, is-closing, is-shaking).
 */
import { useLayoutEffect, useState } from 'react';

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
 * Write the sliding-tabs pill to a tab's offset. First paint and resize pass
 * `animate=false` so the pill does not tween from translateX(0)/width:0.
 */
export function placeTabsPill(
  pill: HTMLElement,
  tab: { offsetLeft: number; offsetWidth: number },
  animate: boolean,
): void {
  if (!animate) {
    const prev = pill.style.transition;
    pill.style.transition = 'none';
    pill.style.transform = `translateX(${tab.offsetLeft}px)`;
    pill.style.width = `${tab.offsetWidth}px`;
    void pill.offsetWidth;
    pill.style.transition = prev;
    return;
  }
  pill.style.transform = `translateX(${tab.offsetLeft}px)`;
  pill.style.width = `${tab.offsetWidth}px`;
}

/** Remove → reflow → add `.is-shaking` so the keyframe always restarts. */
export function replayShake(el: HTMLElement): void {
  el.classList.remove('is-shaking');
  void el.offsetWidth;
  el.classList.add('is-shaking');
}

export function shakeDurationMs(cssText: string): number {
  const a = parseDurationMs(readCssVar(cssText, '--shake-dur-a') ?? '', 80);
  const b = parseDurationMs(readCssVar(cssText, '--shake-dur-b') ?? '', 60);
  return a * 2 + b * 2;
}
