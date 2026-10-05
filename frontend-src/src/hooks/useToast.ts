import { createContext, useContext } from 'react';
import { t as __t } from '@/i18n/core';
export type ToastKind = 'success' | 'error' | 'info';
/** 提示条上的一个操作（如「撤销」）：点了先执行再收起这条提示 */
export interface ToastAction {
  label: string;
  onClick: () => void;
}
export interface ToastOptions {
  action?: ToastAction;
}
export interface ToastContextValue {
  toast: (kind: ToastKind, title: string, description?: string, options?: ToastOptions) => void;
  success: (title: string, description?: string, options?: ToastOptions) => void;
  error: (title: string, description?: string, options?: ToastOptions) => void;
  info: (title: string, description?: string, options?: ToastOptions) => void;
}

export const ToastContext = createContext<ToastContextValue | null>(null);

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error(__t('useToast 必须在 <ToastProvider> 内使用'));
  return ctx;
}
