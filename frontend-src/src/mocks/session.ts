/** Mock 会话 / 运行时状态（写操作在此落盘于内存） */
import type { AccessStatus } from '@/api/types';
import { ApiError } from '@/api/client';
import { parseWatchlistInput } from '@/lib/personalWatchlist';
import { t } from '../i18n/core.ts';

const session = {
  role: 'visitor' as AccessStatus['role'],
  aiEnabled: true,
  username: null as string | null,
};

const MOCK_WATCHLIST_MAX = 50;
let watchlistTickers: string[] = [];

function requireWatchlistPrincipal() {
  if (session.role !== 'owner' && !session.username) {
    throw new ApiError(401, t('请先登录'), { bizCode: 'account_login_required' });
  }
}

function normalizeMockTicker(value: string): string {
  const parsed = parseWatchlistInput(value);
  if (parsed.invalid.length || parsed.tickers.length !== 1) {
    throw new ApiError(400, t('股票代码格式不正确'), { bizCode: 'invalid_ticker' });
  }
  return parsed.tickers[0];
}

function watchlistPayload() {
  return { tickers: [...watchlistTickers], maxTickers: MOCK_WATCHLIST_MAX };
}

export function getAccountWatchlist() {
  requireWatchlistPrincipal();
  return watchlistPayload();
}

export function editAccountWatchlist(add: string[], remove: string[]) {
  requireWatchlistPrincipal();
  if (!Array.isArray(add) || !Array.isArray(remove)) {
    throw new ApiError(400, t('请求无法完成'), { bizCode: 'invalid_payload' });
  }
  const addTickers = add.map(normalizeMockTicker);
  const removeTickers = new Set(remove.map(normalizeMockTicker));
  const next = [...new Set([
    ...watchlistTickers.filter((symbol) => !removeTickers.has(symbol)),
    ...addTickers,
  ])];
  if (next.length > MOCK_WATCHLIST_MAX) {
    throw new ApiError(409, t('最多保存 {count} 只股票，请先移除一些代码', { count: MOCK_WATCHLIST_MAX }), { bizCode: 'watchlist_full' });
  }
  watchlistTickers = next;
  return watchlistPayload();
}

export function replaceAccountWatchlist(tickers: string[]) {
  requireWatchlistPrincipal();
  if (!Array.isArray(tickers)) {
    throw new ApiError(400, t('请求无法完成'), { bizCode: 'invalid_payload' });
  }
  const next = tickers.map(normalizeMockTicker);
  if (new Set(next).size !== next.length) {
    throw new ApiError(400, t('请求无法完成'), { bizCode: 'invalid_payload' });
  }
  if (next.length > MOCK_WATCHLIST_MAX) {
    throw new ApiError(409, t('最多保存 {count} 只股票，请先移除一些代码', { count: MOCK_WATCHLIST_MAX }), { bizCode: 'watchlist_full' });
  }
  watchlistTickers = next;
  return watchlistPayload();
}

export function getAccess(): AccessStatus {
  const owner = session.role === 'owner';
  return {
    role: session.role,
    aiEnabled: owner && session.aiEnabled,
    aiAvailable: owner && session.aiEnabled,
    aiReason: owner && session.aiEnabled ? null : owner ? 'analysis_trigger_disabled' : 'owner_login_required',
    accountUsername: session.username,
  };
}

export function login(password: string): AccessStatus {
  // mock：任意密码均可登录为 owner
  void password;
  session.role = 'owner';
  return getAccess();
}

export function logout(): AccessStatus {
  session.role = 'visitor';
  session.username = null;
  return getAccess();
}
