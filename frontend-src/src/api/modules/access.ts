/** 访问域：GET /api/access/status · POST /api/access/login · POST /api/access/logout */
import { get, post, mockOr } from '../client';
import { asRec, pickB, pickS } from '../live';
import * as session from '@/mocks/session';
import type { AccessStatus } from '../types';

/**
 * 真实后端形状。`logged_in` 只表示管理员（admin）身份；
 * `account` 是与之独立的客户会话，二者不能互相推导。
 */
interface LiveAccessStatus {
  access_mode: 'password' | 'private_network';
  logged_in: boolean;
  account?: { logged_in?: boolean; username?: string | null } | null;
}

function identityFromAccess(s: LiveAccessStatus): AccessStatus {
  const accountUsername =
    s.account?.logged_in === true && s.account.username ? String(s.account.username) : null;
  const owner = s.access_mode === 'private_network' || s.logged_in;
  if (!owner) {
    return {
      role: 'visitor',
      aiEnabled: false,
      aiAvailable: false,
      aiReason: 'owner_login_required',
      accountUsername,
    };
  }
  // AI 点是页头装饰，不能挡住研究页挂载：身份只由 /access/status 决定。
  return {
    role: 'owner',
    aiEnabled: false,
    aiAvailable: false,
    aiReason: 'analysis_status_pending',
    accountUsername,
  };
}

async function enrichOwnerCapabilities(base: AccessStatus): Promise<AccessStatus> {
  if (base.role !== 'owner') return base;
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
      ...base,
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
      ...base,
      aiEnabled: false,
      aiAvailable: false,
      aiReason: 'analysis_status_unavailable',
    };
  }
}

async function liveIdentity(): Promise<AccessStatus> {
  return identityFromAccess(await get<LiveAccessStatus>('/access/status'));
}

async function liveStatus(): Promise<AccessStatus> {
  return enrichOwnerCapabilities(await liveIdentity());
}

/**
 * 契约 §0.1：登录/登出只负责 Set-Cookie / 清 Cookie，
 * 前端只认 GET /api/access/status —— 写操作成功后重新拉取权威状态。
 * 错误透传：client 统一抛 ApiError（bizCode: login_cooldown/https_required/
 * owner_login_required，retryAfter 取自 Retry-After 头或 body.retry_after），
 * 形状与 Login.tsx 的 mapError 消费一致。
 *
 * 写操作与状态校验必须分开（GPT-5.6-Pro 审计 P2-2）：旧写法是
 * `post(...).then(liveStatus)`，随后那个 GET 一失败，整个登录 Promise 就 reject，
 * 界面报「登录失败」——而 Cookie 其实已经写好了。写成功就是写成功，校验是可重试的
 * 独立一步。
 */
export const accessApi = {
  /** 只读 /access/status，足够确认主体并挂载公开研究页。 */
  identity: (): Promise<AccessStatus> =>
    mockOr(() => session.getAccess(), liveIdentity),
  /** 管理员 AI 点：在身份确认之后再拉，失败不得回退成访客。 */
  enrichOwnerCapabilities: (status: AccessStatus): Promise<AccessStatus> =>
    mockOr(async () => status, () => enrichOwnerCapabilities(status)),
  status: (): Promise<AccessStatus> =>
    mockOr(() => session.getAccess(), liveStatus),
  /** 用户名为 admin 时走管理员通道，其余走客户账号表。 */
  login: (username: string, password: string): Promise<void> =>
    mockOr(
      async () => {
        await session.login(password);
      },
      async () => {
        await post('/access/login', { username, password });
      },
    ),
  register: (username: string, password: string): Promise<void> =>
    mockOr(
      async () => {
        await session.login(password);
      },
      async () => {
        await post('/account/register', { username, password });
      },
    ),
  logout: (): Promise<void> =>
    mockOr(
      async () => {
        await session.logout();
      },
      async () => {
        await post('/access/logout');
      },
    ),
};
