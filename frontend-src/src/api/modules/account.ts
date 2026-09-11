/**
 * 客户账号域：个人自选的读写与退出。
 *
 * 与管理员（admin）通道完全分开：这里的会话只能读写自己那份自选，
 * 拿不到任何管理员能力。登录复用 /access/login（用户名不是 admin 即走客户表）。
 */
import { ApiError, get, mockOr, post, request } from '../client';
import { asRec } from '../live';
import { parseWatchlistInput } from '@/lib/personalWatchlist';
import * as session from '@/mocks/session';
import { getLocale, t } from '@/i18n/core';

const CJK = /[\u4e00-\u9fff]/;

/** Map watchlist API failures to the current locale. Backend messages are Chinese. */
export function watchlistErrorMessage(error: unknown, maxTickers = 50): string {
  if (error instanceof ApiError) {
    if (error.bizCode === 'watchlist_full') {
      return t('最多保存 {count} 只股票，请先移除一些代码', { count: maxTickers });
    }
    if (error.bizCode === 'invalid_ticker') {
      return t('股票代码格式不正确');
    }
    if (error.bizCode === 'invalid_payload') {
      return t('请求无法完成');
    }
  }
  if (error instanceof Error && error.message) {
    if (getLocale() !== 'zh' && CJK.test(error.message)) return t('请稍后再试');
    return error.message;
  }
  return t('请稍后再试');
}

export interface AccountWatchlist {
  tickers: string[];
  maxTickers: number;
}

function normalizeWatchlist(body: unknown): AccountWatchlist {
  const row = asRec(body);
  const raw = row.tickers;
  const maxRaw = Number(row.max_tickers ?? row.maxTickers);
  if (!Array.isArray(raw) || !raw.every((value) => typeof value === 'string'
    && parseWatchlistInput(value).tickers.length === 1
    && parseWatchlistInput(value).tickers[0] === value)
    || !Number.isInteger(maxRaw) || maxRaw < 1 || raw.length > maxRaw
    || new Set(raw).size !== raw.length) {
    throw new ApiError(502, t('自选列表返回异常，请重试'));
  }
  return {
    tickers: raw,
    maxTickers: maxRaw,
  };
}

export const accountApi = {
  logout: (): Promise<void> => post('/account/logout').then(() => undefined),

  watchlist: (): Promise<AccountWatchlist> =>
    mockOr(() => session.getAccountWatchlist(), () => get('/account/watchlist').then(normalizeWatchlist)),

  edit: (add: string[], remove: string[]): Promise<AccountWatchlist> =>
    mockOr(
      () => session.editAccountWatchlist(add, remove),
      () => request('/account/watchlist', {
        method: 'PATCH', body: JSON.stringify({ add, remove }),
      }).then(normalizeWatchlist),
    ),
};
