import type { MarketSession } from '@/api/types';
import { t } from '../i18n/core.ts';

type MarketState = 'open' | 'premarket' | 'postmarket' | 'closed';

/** 已归一的市场状态；旧接口别名仍由各 API 网关处理。 */
export const MARKET_TO_SESSION: Record<MarketState, MarketSession> = {
  open: 'regular',
  premarket: 'premarket',
  postmarket: 'afterhours',
  closed: 'closed',
};

export const MARKET_LABEL: Record<MarketState, string> = {
  open: t('盘中'),
  premarket: t('盘前'),
  postmarket: t('盘后'),
  closed: t('休市'),
};
