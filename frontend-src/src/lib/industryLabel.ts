import { t } from '../i18n/core';

// Provider industry names are data labels, not user-created watchlist groups.
const INDUSTRY_LABELS: Record<string, () => string> = {
  'electrical equipment & parts': () => t('电气设备及零部件'),
  'internet content & information': () => t('互联网内容与信息'),
};

export function industryLabel(value: string | null | undefined): string {
  const label = value?.trim() ?? '';
  if (!label || label === '自定义' || label.toLowerCase() === 'custom') return '';
  return INDUSTRY_LABELS[label.toLowerCase()]?.() ?? t(label);
}
