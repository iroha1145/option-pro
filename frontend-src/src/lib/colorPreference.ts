/**
 * 涨跌色彩习惯（美股绿涨红跌 / 亚洲红涨绿跌）。
 * CSS 变量、Tailwind up/down 工具类、ECharts CH.up600/down600 共用这一份状态。
 * 夜间涨跌色取自 Cloud Monitor 的 ok / crit。
 */
import { getAppearance, subscribeAppearance } from './themePreference.ts';

export type ColorMode = 'western' | 'asian';

const COLOR_MODE_KEY = 'optix_color_mode';

export const PRICE_COLORS = {
  western: {
    up600: '#0E9F6E',
    up700: '#0B7A55',
    up50: '#E5F6EF',
    down600: '#E5484D',
    down700: '#C4302B',
    down50: '#FCECEC',
  },
  asian: {
    up600: '#E5484D',
    up700: '#C4302B',
    up50: '#FCECEC',
    down600: '#0E9F6E',
    down700: '#0B7A55',
    down50: '#E5F6EF',
  },
} as const;

export const PRICE_COLORS_DARK = {
  western: {
    up600: '#62D0A5',
    up700: '#7EE0B8',
    up50: '#163D34',
    down600: '#FF8A80',
    down700: '#FFB4AE',
    down50: '#3A1618',
  },
  asian: {
    up600: '#FF8A80',
    up700: '#FFB4AE',
    up50: '#3A1618',
    down600: '#62D0A5',
    down700: '#7EE0B8',
    down50: '#163D34',
  },
} as const;

const listeners = new Set<() => void>();
let currentMode: ColorMode = readStoredMode();
let storageBound = false;

function readStoredMode(): ColorMode {
  if (typeof window === 'undefined') return 'western';
  try {
    return window.localStorage.getItem(COLOR_MODE_KEY) === 'asian' ? 'asian' : 'western';
  } catch {
    return 'western';
  }
}

function persist(mode: ColorMode): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(COLOR_MODE_KEY, mode);
  } catch {
    /* ignore quota / private-mode failures */
  }
}

function emit(): void {
  listeners.forEach((listener) => listener());
}

function onStorage(event: StorageEvent): void {
  if (event.key !== COLOR_MODE_KEY) return;
  const next: ColorMode = event.newValue === 'asian' ? 'asian' : 'western';
  if (next === currentMode) return;
  currentMode = next;
  applyColorMode(next);
  emit();
}

function ensureStorageListener(): void {
  if (storageBound || typeof window === 'undefined') return;
  storageBound = true;
  window.addEventListener('storage', onStorage);
}

export function getColorMode(): ColorMode {
  return currentMode;
}

export function directionColors(mode: ColorMode = getColorMode()) {
  return getAppearance() === 'dark' ? PRICE_COLORS_DARK[mode] : PRICE_COLORS[mode];
}

export function applyColorMode(mode: ColorMode = getColorMode()): void {
  currentMode = mode;
  if (typeof document === 'undefined') return;
  if (mode === 'asian') {
    document.documentElement.setAttribute('data-color-mode', 'asian');
  } else {
    document.documentElement.removeAttribute('data-color-mode');
  }
}

export function setColorMode(mode: ColorMode): void {
  currentMode = mode;
  persist(mode);
  applyColorMode(mode);
  emit();
}

export function subscribeColorMode(listener: () => void): () => void {
  ensureStorageListener();
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/* 夜间涨跌色与浅色不同：外观一变，CH.up600 / 热力两端也要跟着通知订阅者。 */
subscribeAppearance(() => {
  emit();
});
