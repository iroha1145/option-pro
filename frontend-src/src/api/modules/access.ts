/** 访问域：GET /api/access/status · POST /api/access/login · POST /api/access/logout */
import { get, post, mockOr } from '../client';
import { asRec, pickB, pickS } from '../live';
import * as session from '@/mocks/session';
import type { AccessStatus } from '../types';

/** 真实后端形状：{ access_mode: "password" | "private_network", logged_in: boolean } */
interface LiveAccessStatus {
  access_mode: 'password' | 'private_network';
  logged_in: boolean;
}

async function liveStatus(): Promise<AccessStatus> {
  const s = await get<LiveAccessStatus>('/access/status');
  const owner = s.access_mode === 'private_network' || s.logged_in;
  if (!owner) {
    return {
      role: 'visitor',
      aiEnabled: false,
      aiAvailable: false,
      aiReason: 'owner_login_required',
    };
  }

  try {
    const [capabilityBody, runtimeBody] = await Promise.all([
      get('/ai/status'),
      get('/runtime-settings'),
    ]);
    const capability = asRec(capabilityBody);
    const runtime = asRec(runtimeBody);
    const runtimeAi = asRec(asRec(runtime.settings).ai);
    const aiEnabled = pickB(runtimeAi, 'manual_analysis_enabled') === true;
    const capabilityEnabled = pickB(capability, 'enabled') === true;
    const aiAvailable = aiEnabled && capabilityEnabled;
    return {
      role: 'owner',
      aiEnabled,
      aiAvailable,
      aiReason: aiAvailable
        ? null
        : aiEnabled
          ? pickS(capability, 'status') ?? 'analysis_unavailable'
          : 'manual_analysis_disabled',
    };
  } catch {
    // 登录身份仍以 access/status 为准；模型能力或运行设置探针失败时绝不显示假绿灯。
    return {
      role: 'owner',
      aiEnabled: false,
      aiAvailable: false,
      aiReason: 'analysis_status_unavailable',
    };
  }
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
    mockOr(() => session.getAccess(), liveStatus),
  login: (password: string): Promise<AccessStatus> =>
    mockOr(
      () => session.login(password),
      () => post('/access/login', { password }).then(liveStatus),
    ),
  logout: (): Promise<AccessStatus> =>
    mockOr(
      () => session.logout(),
      () => post('/access/logout').then(liveStatus),
    ),
};
