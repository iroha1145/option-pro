import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { accessApi } from '@/api/modules/access';
import type { AccessRole, AccessStatus } from '@/api/types';

interface AccessContextValue {
  role: AccessRole;
  aiEnabled: boolean;
  isOwner: boolean;
  isVisitor: boolean;
  loading: boolean;
  login: (password: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const AccessContext = createContext<AccessContextValue | null>(null);

/**
 * 访问身份（visitor / owner）
 * mock 默认 visitor；「模拟登录」任意密码即可切换为 owner。
 */
export function AccessProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AccessStatus>({ role: 'visitor', aiEnabled: true });
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

  const login = useCallback(async (password: string) => {
    setStatus(await accessApi.login(password));
  }, []);

  const logout = useCallback(async () => {
    setStatus(await accessApi.logout());
  }, []);

  const value = useMemo<AccessContextValue>(
    () => ({
      role: status.role,
      aiEnabled: status.aiEnabled,
      isOwner: status.role === 'owner',
      isVisitor: status.role !== 'owner',
      loading,
      login,
      logout,
      refresh,
    }),
    [status, loading, login, logout, refresh],
  );

  return <AccessContext.Provider value={value}>{children}</AccessContext.Provider>;
}

export function useAccess(): AccessContextValue {
  const ctx = useContext(AccessContext);
  if (!ctx) throw new Error('useAccess 必须在 <AccessProvider> 内使用');
  return ctx;
}
