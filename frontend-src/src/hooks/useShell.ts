import { createContext, useContext } from 'react';
import { t as __t } from '@/i18n/core';

export interface ShellContextValue {
  openPalette: () => void;
  openTicker: (ticker: string) => void;
}

export const ShellContext = createContext<ShellContextValue | null>(null);

export function useShell(): ShellContextValue {
  const ctx = useContext(ShellContext);
  if (!ctx) throw new Error(__t('useShell 必须在 <Layout> 内使用'));
  return ctx;
}
