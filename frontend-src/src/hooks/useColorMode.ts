import { useSyncExternalStore } from 'react';
import { getColorMode, subscribeColorMode, type ColorMode } from '@/lib/colorPreference.ts';

/** 顶栏与 Dock 共用同一外部快照，避免两套独立 useState 互相看不见。 */
export function useColorMode(): ColorMode {
  return useSyncExternalStore(subscribeColorMode, getColorMode, getColorMode);
}
