import { useSyncExternalStore } from 'react';
import {
  getAppearance,
  getThemePreference,
  subscribeAppearance,
  type Appearance,
  type ThemePreference,
} from '@/lib/themePreference.ts';

/** 已解析的浅/深外观；跟随系统时随设备 color-scheme 更新。 */
export function useAppearance(): Appearance {
  return useSyncExternalStore(subscribeAppearance, getAppearance, getAppearance);
}

/** 用户选择的偏好（system / light / dark），不是解析后的外观。 */
export function useThemePreference(): ThemePreference {
  return useSyncExternalStore(subscribeAppearance, getThemePreference, getThemePreference);
}
