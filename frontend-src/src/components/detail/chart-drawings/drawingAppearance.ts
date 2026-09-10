import { getAppearance } from '../../../lib/themePreference.ts';

// Resolve built-in ink for display only; stored drawing colors stay unchanged.
const DARK_INK: Record<string, string> = {
  '#0E647F': '#69C7DD', '#8D299B': '#E398ED', '#4F46E5': '#A5A0FF',
  '#52617A': '#A0A8B5', '#2E46E0': '#8B9CFF', '#3B59F2': '#8B9CFF',
  '#0E9F6E': '#62D0A5', '#0B7A55': '#7EE0B8',
  '#E5484D': '#FF8A80', '#C4302B': '#FFB4AE',
  '#E8930C': '#FACC15', '#0B7285': '#4EC4D4', '#085E6E': '#4EC4D4',
  '#3D4A68': '#B0B6C0', '#5A6788': '#A0A8B5', '#8A94B0': '#A0A8B5',
  '#2338C8': '#8B9CFF', '#C27706': '#F2B96B', '#B87821': '#F2B96B',
  '#866026': '#F2B96B', '#BA7517': '#F2B96B', '#0B6E99': '#69C7DD',
};

export function drawingPaint(color: string): string {
  return getAppearance() === 'dark' ? DARK_INK[color.toUpperCase()] ?? color : color;
}

export function drawingSurface(opacity: number): string {
  return `rgba(${getAppearance() === 'dark' ? '36,38,45' : '255,255,255'},${opacity})`;
}
