import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { accessApi } from '@/api/modules/access';
import { PRINCIPAL_INVALID_EVENT } from '@/api/client';
import { dropSharedReads } from '@/api/sharedRead';
import { setQueryPrincipal } from '@/api/queryRegistry';
import { resetMarketReadState } from '@/api/marketRead';
import { clearCatalystReadCache } from '@/components/catalysts/api';
import type { AccessRole, AccessStatus } from '@/api/types';
import { t } from '../i18n/core.ts';

interface AccessContextValue {
  role: AccessRole;
  aiEnabled: boolean;
  aiAvailable: boolean;
  aiReason: string | null;
  isOwner: boolean;
  isVisitor: boolean;
  /** 已登录客户的用户名；管理员或未登录时为 null。 */
  username: string | null;
  /** 是否为已登录的普通客户（与 isOwner 互不蕴含）。 */
  isCustomer: boolean;
  /**
   * 能否读写「我的自选」。
   *
   * 客户会话与 Owner 会话各自持有独立的一份自选，后端 /api/account/watchlist
   * 两种主体都受理。不能只看 isCustomer：Owner 走口令登录、不持账号 cookie，
   * accountUsername 恒为 null，于是整块自选管理 UI 会对唯一的真实用户隐身。
   */
  canManageWatchlist: boolean;
  loading: boolean;
  /**
   * 身份服务本身读不到。
   *
   * visitor 不能兼任错误回退（审计 P2-1）：首次 /access/status 失败时旧实现直接
   * 停在初始的 visitor 上，于是「身份服务不可用」被显示成「未登录」。
   */
  identityUnavailable: boolean;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const AccessContext = createContext<AccessContextValue | null>(null);

/**
 * 访问身份（visitor / owner）
 * mock 默认 visitor；「模拟登录」任意密码即可切换为 owner。
 */
export function AccessProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AccessStatus>({
    role: 'visitor',
    aiEnabled: false,
    aiAvailable: false,
    aiReason: 'owner_login_required',
    accountUsername: null,
  });
  const [loading, setLoading] = useState(true);
  const [identityUnavailable, setIdentityUnavailable] = useState(false);

  /**
   * 身份世代（审计 P1-07）。
   *
   * 初始读取、窗口聚焦、页面重新可见、60 秒定时、登录、注册、登出都会并发地
   * 请求身份状态，而任何「先发出、后返回」的响应都能直接覆盖较新的结果：聚焦时
   * 那次慢响应可以在登录成功之后把界面打回访客态。这里给每次探测编号，只有当前
   * 世代的结果允许写入；登录/注册/登出先递增世代，从而作废所有在途探测。
   */
  const generationRef = useRef(0);

  /**
   * 上一次确认到的身份，用来判断它是不是**真的**变了。
   *
   * 身份不只在登录/登出时变：会话过期是由 60 秒定时或重新聚焦那次核验发现的，
   * 没有任何本地写操作。那条路径也必须作废共享读，否则刚被降级成访客的页面还能
   * 在 2 秒内复用 owner 那份 /strength/market。
   *
   * 但只在**变了**的时候作废。每次探测都清一遍会把 2 秒共享窗口废掉：一个轮询
   * 周期里三个组件同时要 /market/status，那正是这个窗口要压掉的重复。
   */
  const identityRef = useRef<string | null>(null);

  const readInto = useCallback(async (generation: number) => {
    try {
      const next = await accessApi.status();
      if (generation !== generationRef.current) return;
      const identity = `${next.role}\u0000${next.accountUsername ?? ''}`;
      if (identityRef.current !== null && identityRef.current !== identity) {
        // 主体变了:注册表(含持久层)、marketRead、catalysts 读缓存一起作废,
        // 上一个主体的响应不得复用给下一个。
        dropSharedReads();
        resetMarketReadState();
        clearCatalystReadCache();
      }
      identityRef.current = identity;
      setQueryPrincipal(identity);
      setStatus(next);
      setIdentityUnavailable(false);
    } catch (error) {
      if (generation !== generationRef.current) return;
      // 身份读不到时保留当前已知身份并明确置错，而不是悄悄退回访客。
      setIdentityUnavailable(true);
      throw error;
    } finally {
      if (generation === generationRef.current) setLoading(false);
    }
  }, []);

  const refresh = useCallback(async () => {
    await readInto(generationRef.current);
  }, [readInto]);

  /** 作废所有在途探测并开始新一轮读取。写操作之后必须走这条路径。 */
  const invalidateAndRead = useCallback(() => {
    generationRef.current += 1;
    // 共享读缓存也一起作废：/strength/market 对访客和 owner 返回的不是同一份
    // 数据（访客读公开快照，owner 实时算），登录成功后的第一次读取不得复用
    // 上一个身份那份。三层读缓存一起清。
    dropSharedReads();
    resetMarketReadState();
    clearCatalystReadCache();
    return readInto(generationRef.current);
  }, [readInto]);

  useEffect(() => {
    void refresh().catch(() => undefined);
  }, [refresh]);

  // backend 重启、新设备登录或会话过期后，旧 SPA 不能继续显示“已登录”。
  // 多数公开 GET 会以 visitor 身份返回 200，因此还需在重新聚焦/可见和定时点主动核验。
  //
  // 核验对「任一已登录主体」生效（审计 P1-08）：旧实现只在 role === 'owner' 时挂
  // 定时器，普通客户的会话过期后界面会一直显示原用户名和自选管理入口，请求持续
  // 401，只能靠用户手动刷新整页。
  const hasPrincipal = status.role === 'owner' || status.accountUsername !== null;
  useEffect(() => {
    if (!hasPrincipal) return;
    const verify = () => void refresh().catch(() => undefined);
    const onInvalidated = () => {
      // 只做降级提示，权威结论仍以随后的 /access/status 为准。
      setStatus((current) => ({
        ...current,
        role: 'visitor',
        aiEnabled: false,
        aiAvailable: false,
        aiReason: 'owner_login_required',
      }));
      verify();
    };
    const onVisibility = () => {
      if (document.visibilityState === 'visible') verify();
    };
    window.addEventListener(PRINCIPAL_INVALID_EVENT, onInvalidated);
    window.addEventListener('focus', verify);
    document.addEventListener('visibilitychange', onVisibility);
    const interval = window.setInterval(verify, 60_000);
    return () => {
      window.removeEventListener(PRINCIPAL_INVALID_EVENT, onInvalidated);
      window.removeEventListener('focus', verify);
      document.removeEventListener('visibilitychange', onVisibility);
      window.clearInterval(interval);
    };
  }, [refresh, hasPrincipal]);

  /**
   * 写操作成功即视为成功（审计 P2-2）。随后的状态校验独立进行：它失败只意味着
   * 「暂时读不到身份」，不能把一次已经写好 Cookie 的登录报成登录失败。
   */
  const applyWrite = useCallback(
    async (write: () => Promise<void>) => {
      await write();
      await invalidateAndRead().catch(() => undefined);
    },
    [invalidateAndRead],
  );

  const login = useCallback(
    (username: string, password: string) =>
      applyWrite(() => accessApi.login(username, password)),
    [applyWrite],
  );

  const register = useCallback(
    (username: string, password: string) =>
      applyWrite(() => accessApi.register(username, password)),
    [applyWrite],
  );

  const logout = useCallback(
    () => applyWrite(() => accessApi.logout()),
    [applyWrite],
  );

  const value = useMemo<AccessContextValue>(
    () => ({
      role: status.role,
      aiEnabled: status.aiEnabled,
      aiAvailable: status.aiAvailable,
      aiReason: status.aiReason,
      isOwner: status.role === 'owner',
      isVisitor: status.role !== 'owner',
      username: status.accountUsername,
      isCustomer: status.accountUsername !== null,
      canManageWatchlist: status.accountUsername !== null || status.role === 'owner',
      loading,
      identityUnavailable,
      login,
      register,
      logout,
      refresh,
    }),
    [status, loading, identityUnavailable, login, register, logout, refresh],
  );

  return <AccessContext.Provider value={value}>{children}</AccessContext.Provider>;
}

// Provider 与消费 Hook 同文件便于保持访问状态的单一事实源。
// eslint-disable-next-line react-refresh/only-export-components
export function useAccess(): AccessContextValue {
  const ctx = useContext(AccessContext);
  if (!ctx) throw new Error(t('useAccess 必须在 <AccessProvider> 内使用'));
  return ctx;
}
