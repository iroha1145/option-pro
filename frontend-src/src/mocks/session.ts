/** Mock 会话 / 运行时状态（写操作在此落盘于内存） */
import type { AccessStatus, RuntimeSettings } from '@/api/types';

const session = {
  role: 'visitor' as AccessStatus['role'],
  aiEnabled: true,
};

export function getAccess(): AccessStatus {
  return { role: session.role, aiEnabled: session.aiEnabled };
}

export function login(_password: string): AccessStatus {
  // mock：任意密码均可登录为 owner
  session.role = 'owner';
  return getAccess();
}

export function logout(): AccessStatus {
  session.role = 'visitor';
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
  { id: 'h1', at: new Date(Date.now() - 3 * 86_400_000).toISOString(), actor: 'owner', change: '开启 AI 分析开关' },
  { id: 'h2', at: new Date(Date.now() - 6 * 86_400_000).toISOString(), actor: 'owner', change: '扫描间隔 10 → 15 分钟' },
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
