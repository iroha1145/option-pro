/** Mock 会话 / 运行时状态（写操作在此落盘于内存） */
import type { AccessStatus, RuntimeSettings } from '@/api/types';
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

/* ---------------- 运行时设置 ---------------- */
const settings: RuntimeSettings = {
  aiEnabled: true,
  scanIntervalMin: 15,
  watchlistRefreshSec: 60,
  optionsUnusualEnabled: true,
};

const history: { id: string; at: string; actor: string; change: string }[] = [
  { id: 'h1', at: new Date(Date.now() - 3 * 86_400_000).toISOString(), actor: 'owner', change: t('开启 AI 分析开关') },
  { id: 'h2', at: new Date(Date.now() - 6 * 86_400_000).toISOString(), actor: 'owner', change: t('扫描间隔 10 → 15 分钟') },
];

export function getSettings(): RuntimeSettings {
  return { ...settings, aiEnabled: session.aiEnabled };
}

export function updateSettings(patch: Partial<RuntimeSettings>): RuntimeSettings {
  Object.assign(settings, patch);
  if (patch.aiEnabled !== undefined) session.aiEnabled = patch.aiEnabled;
  history.unshift({
    id: `h${Date.now()}`,
    at: new Date().toISOString(),
    actor: 'owner',
    change: `更新设置：${Object.keys(patch).join('、')}`,
  });
  return getSettings();
}

export function getSettingsHistory() {
  return [...history];
}

export function rollbackSettings(id: string): RuntimeSettings {
  const h = history.find((x) => x.id === id);
  if (h) {
    history.unshift({ id: `h${Date.now()}`, at: new Date().toISOString(), actor: 'owner', change: `回滚至 ${h.change}` });
  }
  return getSettings();
}
