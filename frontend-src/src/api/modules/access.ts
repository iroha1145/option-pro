/** 访问域：GET /api/access/status · POST /api/access/login · POST /api/access/logout */
import { get, post, mockOr } from '../client';
import * as session from '@/mocks/session';
import type { AccessStatus } from '../types';

/** 真实后端形状：{ access_mode: "password" | "private_network", logged_in: boolean } */
interface LiveAccessStatus {
  access_mode: 'password' | 'private_network';
  logged_in: boolean;
}

function mapLive(s: LiveAccessStatus): AccessStatus {
  return {
    role: s.access_mode === 'private_network' || s.logged_in ? 'owner' : 'visitor',
    aiEnabled: s.access_mode === 'private_network' || s.logged_in,
  };
}

/**
 * 契约 §0.1：登录/登出只负责 Set-Cookie / 清 Cookie，
 * 前端只认 GET /api/access/status —— 写操作成功后重新拉取权威状态。
 * 错误透传：client 统一抛 ApiError（bizCode: login_cooldown/https_required/
 * owner_login_required，retryAfter 取自 Retry-After 头或 body.retry_after），
 * 形状与 Login.tsx 的 mapError 消费一致。
 */
export const accessApi = {
  status: (): Promise<AccessStatus> =>
    mockOr(() => session.getAccess(), () => get<LiveAccessStatus>('/access/status').then(mapLive)),
  login: (password: string): Promise<AccessStatus> =>
    mockOr(
      () => session.login(password),
      () => post('/access/login', { password }).then(() => get<LiveAccessStatus>('/access/status').then(mapLive)),
    ),
  logout: (): Promise<AccessStatus> =>
    mockOr(
      () => session.logout(),
      () => post('/access/logout').then(() => get<LiveAccessStatus>('/access/status').then(mapLive)),
    ),
};
