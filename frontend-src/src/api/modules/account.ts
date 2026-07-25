/**
 * 客户账号域：注册 · 个人自选的读写。
 *
 * 与管理员（admin）通道完全分开：这里的会话只能读写自己那份自选，
 * 拿不到任何管理员能力。登录复用 /access/login（用户名不是 admin 即走客户表）。
 */
import { del, get, post, put } from '../client';
import { asRec, pickS } from '../live';

export interface AccountWatchlist {
  tickers: string[];
  maxTickers: number;
}

function normalizeWatchlist(body: unknown): AccountWatchlist {
  const row = asRec(body);
  const raw = Array.isArray(row.tickers) ? row.tickers : [];
  const maxRaw = Number(row.max_tickers ?? row.maxTickers);
  return {
    tickers: raw.map((value) => String(value).toUpperCase()).filter(Boolean),
    maxTickers: Number.isFinite(maxRaw) && maxRaw > 0 ? Math.floor(maxRaw) : 50,
  };
}

export const accountApi = {
  /** 注册成功即登录（后端直接下发会话 cookie）。 */
  register: (username: string, password: string): Promise<string> =>
    post('/account/register', { username, password }).then(
      (body) => pickS(asRec(body), 'username') ?? username,
    ),

  me: (): Promise<string | null> =>
    get('/account/me').then((body) => {
      const row = asRec(body);
      return row.logged_in === true ? pickS(row, 'username') ?? null : null;
    }),

  logout: (): Promise<void> => post('/account/logout').then(() => undefined),

  watchlist: (): Promise<AccountWatchlist> =>
    get('/account/watchlist').then(normalizeWatchlist),

  add: (ticker: string): Promise<AccountWatchlist> =>
    post('/account/watchlist', { ticker }).then(normalizeWatchlist),

  remove: (ticker: string): Promise<AccountWatchlist> =>
    del(`/account/watchlist/${encodeURIComponent(ticker)}`).then(normalizeWatchlist),

  replace: (tickers: string[]): Promise<AccountWatchlist> =>
    put('/account/watchlist', { tickers }).then(normalizeWatchlist),
};
