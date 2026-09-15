import { t } from '../i18n/core.ts';

export type T1Status = 'met' | 'unmet' | 'pending' | 'unavailable' | string | null | undefined;

export function t1StatusPresentation(status: T1Status): { label: string; className: string } | null {
  if (status === 'met') {
    return { label: t('日线量价优先'), className: 'radar-chip-up bg-up-50 text-up-700' };
  }
  if (status === 'pending') {
    return { label: t('待收盘确认'), className: 'radar-chip-warn bg-warn-50 text-warn-700' };
  }
  if (status === 'unavailable') {
    return { label: t('日线数据不足'), className: 'radar-chip-neutral bg-card-warm text-ink-500' };
  }
  return null;
}
