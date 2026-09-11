/**
 * 外观偏好（跟随系统 / 浅色 / 深色）。
 * CSS 变量、html.dark、ECharts 调色与顶栏开关共用这一份状态。
 * 夜间画布色取自 Cloud Monitor（#191B20 / #24262D），群青品牌仍走 Optix 令牌。
 */
export type ThemePreference = 'system' | 'light' | 'dark';
export type Appearance = 'light' | 'dark';

export const THEME_KEY = 'optix_theme';

const THEME_PREFERENCES: readonly ThemePreference[] = ['system', 'light', 'dark'];

const listeners = new Set<() => void>();
let currentPreference: ThemePreference = 'system';
let currentAppearance: Appearance = 'light';
let hydrated = false;
let storageBound = false;
let mediaBound = false;
let mediaQuery: MediaQueryList | null = null;

function isPreference(value: unknown): value is ThemePreference {
  return typeof value === 'string' && (THEME_PREFERENCES as readonly string[]).includes(value);
}

function systemAppearance(): Appearance {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return 'light';
  try {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  } catch {
    return 'light';
  }
}

function readStoredPreference(): ThemePreference {
  if (typeof window === 'undefined') return 'system';
  try {
    const raw = window.localStorage.getItem(THEME_KEY);
    return isPreference(raw) ? raw : 'system';
  } catch {
    return 'system';
  }
}

function persist(preference: ThemePreference): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(THEME_KEY, preference);
  } catch {
    /* ignore quota / private-mode failures */
  }
}

function emit(): void {
  listeners.forEach((listener) => listener());
}

export function resolveAppearance(preference: ThemePreference): Appearance {
  return preference === 'system' ? systemAppearance() : preference;
}

function paintDocument(appearance: Appearance): void {
  if (typeof document === 'undefined') return;
  const root = document.documentElement;
  root.classList.toggle('dark', appearance === 'dark');
  root.dataset.theme = appearance;
  root.style.colorScheme = appearance;
  const themeColor = appearance === 'dark' ? '#191B20' : '#F6F7F9';
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', themeColor);
}

export function applyAppearance(preference: ThemePreference = getThemePreference()): void {
  hydrate();
  currentPreference = preference;
  currentAppearance = resolveAppearance(preference);
  paintDocument(currentAppearance);
  ensureMediaListener();
  ensureStorageListener();
}

export function getThemePreference(): ThemePreference {
  hydrate();
  return currentPreference;
}

export function getAppearance(): Appearance {
  hydrate();
  return currentAppearance;
}

export function setThemePreference(preference: ThemePreference): void {
  currentPreference = preference;
  persist(preference);
  applyAppearance(preference);
  emit();
}

function onStorage(event: StorageEvent): void {
  if (event.key !== THEME_KEY) return;
  const next: ThemePreference = isPreference(event.newValue) ? event.newValue : 'system';
  if (next === currentPreference && resolveAppearance(next) === currentAppearance) return;
  currentPreference = next;
  applyAppearance(next);
  emit();
}

function onMediaChange(): void {
  if (currentPreference !== 'system') return;
  const next = systemAppearance();
  if (next === currentAppearance) return;
  applyAppearance('system');
  emit();
}

function ensureStorageListener(): void {
  if (storageBound || typeof window === 'undefined') return;
  storageBound = true;
  window.addEventListener('storage', onStorage);
}

function ensureMediaListener(): void {
  if (mediaBound || typeof window === 'undefined' || typeof window.matchMedia !== 'function') return;
  mediaBound = true;
  try {
    mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
    mediaQuery.addEventListener('change', onMediaChange);
  } catch {
    mediaBound = false;
    mediaQuery = null;
  }
}

function hydrate(): void {
  if (hydrated) return;
  hydrated = true;
  currentPreference = readStoredPreference();
  currentAppearance = resolveAppearance(currentPreference);
}

export function subscribeAppearance(listener: () => void): () => void {
  hydrate();
  ensureMediaListener();
  ensureStorageListener();
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

