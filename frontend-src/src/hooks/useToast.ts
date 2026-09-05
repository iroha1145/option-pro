import { createContext, useContext } from 'react';
import { t as __t } from '@/i18n/core';
export type ToastKind = 'success' | 'error' | 'info';
export interface ToastContextValue {
  toast: (kind: ToastKind, title: string, description?: string) => void;
  success: (title: string, description?: string) => void;
  error: (title: string, description?: string) => void;
  info: (title: string, description?: string) => void;
}

export const ToastContext = createContext<ToastContextValue | null>(null);

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error(__t('useToast 必须在 <ToastProvider> 内使用'));
  return ctx;
}
