import { flushSync } from 'react-dom';
import { getAppearance, resolveAppearance, setThemePreference, type ThemePreference } from './themePreference.ts';

// One owner across the header and login controls. A skipped transition can still
// run its update callback; the identity check keeps a newer selection in charge.
let active: { transition?: ViewTransition } | undefined;

export function revealThemePreference(preference: ThemePreference, trigger: HTMLElement | null): void {
  const previous = active;
  active = undefined;
  previous?.transition?.skipTransition();
  const root = document.documentElement;
  delete root.dataset.themeSwitching;
  for (const name of ['--theme-x', '--theme-y', '--theme-r']) root.style.removeProperty(name);

  const apply = () => flushSync(() => setThemePreference(preference));
  if (!trigger || typeof document.startViewTransition !== 'function'
    || window.matchMedia('(prefers-reduced-motion: reduce)').matches
    || resolveAppearance(preference) === getAppearance()) {
    apply();
    return;
  }

  const { left, top, width, height } = trigger.getBoundingClientRect();
  const x = left + width / 2;
  const y = top + height / 2;
  const radius = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
  root.style.setProperty('--theme-x', `${x}px`);
  root.style.setProperty('--theme-y', `${y}px`);
  root.style.setProperty('--theme-r', `${radius}px`);
  root.dataset.themeSwitching = '';
  const owner: { transition?: ViewTransition } = {};
  active = owner;
  const cleanup = () => {
    if (active !== owner) return;
    active = undefined;
    delete root.dataset.themeSwitching;
    for (const name of ['--theme-x', '--theme-y', '--theme-r']) root.style.removeProperty(name);
  };
  try {
    owner.transition = document.startViewTransition(() => {
      if (active === owner) apply();
    });
    // ready rejects when the browser skips a snapshot (e.g. a hidden tab).
    void owner.transition.ready.catch(() => {});
    void owner.transition.finished.catch(() => {}).finally(cleanup);
  } catch {
    apply();
    cleanup();
  }
}
