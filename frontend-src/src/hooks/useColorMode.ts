import { useSyncExternalStore } from 'react';
import { getAppearance } from '@/lib/themePreference.ts';
import { getColorMode, subscribeColorMode, type ColorMode } from '@/lib/colorPreference.ts';

function colorModeSnapshot(): string {
  return `${getColorMode()}|${getAppearance()}`;
}

/** 顶栏与 Dock 共用同一外部快照，避免两套独立 useState 互相看不见。 */
export function useColorMode(): ColorMode {
  useSyncExternalStore(subscribeColorMode, colorModeSnapshot, colorModeSnapshot);
  return getColorMode();
}
