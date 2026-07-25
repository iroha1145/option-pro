import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { accessApi } from '@/api/modules/access';
import { OWNER_SESSION_INVALID_EVENT } from '@/api/client';
import type { AccessRole, AccessStatus } from '@/api/types';

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
  loading: boolean;
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

  const refresh = useCallback(async () => {
    try {
      setStatus(await accessApi.status());
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // backend 重启、新设备登录或会话过期后，旧 SPA 不能继续显示“已登录”。
  // 多数公开 GET 会以 visitor 身份返回 200，因此还需在重新聚焦/可见和定时点主动核验。
  useEffect(() => {
    if (status.role !== 'owner') return;
    const verify = () => void refresh().catch(() => undefined);
    const onInvalidated = () => {
      setStatus((current) => ({
        role: 'visitor',
        aiEnabled: false,
        aiAvailable: false,
        aiReason: 'owner_login_required',
        // 管理员会话失效不代表客户会话也失效；由随后的 refresh 校准。
        accountUsername: current.accountUsername,
      }));
      verify();
    };
    const onVisibility = () => {
      if (document.visibilityState === 'visible') verify();
    };
    window.addEventListener(OWNER_SESSION_INVALID_EVENT, onInvalidated);
    window.addEventListener('focus', verify);
    document.addEventListener('visibilitychange', onVisibility);
    const interval = window.setInterval(verify, 60_000);
    return () => {
      window.removeEventListener(OWNER_SESSION_INVALID_EVENT, onInvalidated);
      window.removeEventListener('focus', verify);
      document.removeEventListener('visibilitychange', onVisibility);
      window.clearInterval(interval);
    };
  }, [refresh, status.role]);

  const login = useCallback(async (username: string, password: string) => {
    setStatus(await accessApi.login(username, password));
  }, []);

  const register = useCallback(async (username: string, password: string) => {
    setStatus(await accessApi.register(username, password));
  }, []);

  const logout = useCallback(async () => {
    setStatus(await accessApi.logout());
  }, []);

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
      loading,
      login,
      register,
      logout,
      refresh,
    }),
    [status, loading, login, register, logout, refresh],
  );

  return <AccessContext.Provider value={value}>{children}</AccessContext.Provider>;
}

// Provider 与消费 Hook 同文件便于保持访问状态的单一事实源。
// eslint-disable-next-line react-refresh/only-export-components
export function useAccess(): AccessContextValue {
  const ctx = useContext(AccessContext);
  if (!ctx) throw new Error('useAccess 必须在 <AccessProvider> 内使用');
  return ctx;
}
