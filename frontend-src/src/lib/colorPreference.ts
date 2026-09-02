export type ColorMode = 'western' | 'asian';

const COLOR_MODE_KEY = 'optix_color_mode';

export function getColorMode(): ColorMode {
  if (typeof window === 'undefined') return 'western';
  try {
    const saved = localStorage.getItem(COLOR_MODE_KEY);
    return saved === 'asian' ? 'asian' : 'western';
  } catch {
    return 'western';
  }
}

export function setColorMode(mode: ColorMode): void {
  if (typeof window === 'undefined') return;
  try {
    localStorage.setItem(COLOR_MODE_KEY, mode);
  } catch {
    /* ignore storage errors */
  }
  applyColorMode(mode);
}

export function applyColorMode(mode: ColorMode = getColorMode()): void {
  if (typeof document === 'undefined') return;
  if (mode === 'asian') {
    document.documentElement.setAttribute('data-color-mode', 'asian');
  } else {
    document.documentElement.removeAttribute('data-color-mode');
  }
}
