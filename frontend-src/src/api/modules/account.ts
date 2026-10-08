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
    if (error.bizCode === 'invalid_watchlist_undo') {
      return t('无法撤销，请重新读取关注列表');
    }
    if (error.bizCode === 'watchlist_identity_changed') {
      return t('登录账号已变化，请重新操作');
    }
    if (error.bizCode === 'watchlist_full') {
      return t('最多保存 {count} 只股票，请先移除部分股票', { count: maxTickers });
    }
    if (error.bizCode === 'invalid_ticker') {
      return t('股票代码格式不正确');
    }
    if (error.bizCode === 'invalid_payload') {
      return t('请求无法完成，请重试');
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

export interface WatchlistUndo {
  ticker: string;
  original_order: string[];
  principal_id: string;
}

export interface WatchlistRemoval extends AccountWatchlist {
  undo: WatchlistUndo | null;
}

function validTicker(value: unknown): value is string {
  return typeof value === 'string' && parseWatchlistInput(value).tickers.length === 1
    && parseWatchlistInput(value).tickers[0] === value;
}

function normalizeRemoval(body: unknown): WatchlistRemoval {
  const data = normalizeWatchlist(body);
  const raw = asRec(body).undo;
  if (raw === null) return { ...data, undo: null };
  const undo = asRec(raw);
  if (!validTicker(undo.ticker) || !Array.isArray(undo.original_order)
    || !undo.original_order.every(validTicker) || !undo.original_order.includes(undo.ticker)
    || new Set(undo.original_order).size !== undo.original_order.length
    || undo.original_order.length > data.maxTickers
    || typeof undo.principal_id !== 'string' || !undo.principal_id.trim()
    || data.tickers.includes(undo.ticker)) {
    throw new ApiError(502, t('关注列表返回异常，请重试'));
  }
  return { ...data, undo: { ticker: undo.ticker, original_order: undo.original_order, principal_id: undo.principal_id } };
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
    throw new ApiError(502, t('关注列表返回异常，请重试'));
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

  remove: (ticker: string, expectedUsername: string): Promise<WatchlistRemoval> =>
    mockOr(
      () => session.removeAccountWatchlist(ticker, expectedUsername),
      () => post('/account/watchlist/removals', { ticker, expected_username: expectedUsername }).then(normalizeRemoval),
    ),

  restore: (undo: WatchlistUndo): Promise<AccountWatchlist> =>
    mockOr(
      () => session.restoreAccountWatchlist(undo),
      () => post('/account/watchlist/restore', undo).then(normalizeWatchlist),
    ),

  edit: (add: string[], remove: string[]): Promise<AccountWatchlist> =>
    mockOr(
      () => session.editAccountWatchlist(add, remove),
      () => request('/account/watchlist', {
        method: 'PATCH', body: JSON.stringify({ add, remove }),
      }).then(normalizeWatchlist),
    ),
};
