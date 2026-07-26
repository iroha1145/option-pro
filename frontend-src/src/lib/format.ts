/** 数字 / 时间格式化（等宽数字场景统一走这里） */

import { t } from '../i18n/core.ts';
export function fmtPrice(n: number, digits = 2): string {
  return n.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function fmtSigned(n: number, digits = 2): string {
  const s = fmtPrice(Math.abs(n), digits);
  return `${n >= 0 ? '+' : '−'}${s}`;
}

export function fmtPct(n: number, digits = 2): string {
  return `${n >= 0 ? '+' : '−'}${Math.abs(n).toFixed(digits)}%`;
}

export function fmtCompact(n: number): string {
  if (n >= 1e12) return `${(n / 1e12).toFixed(2)}T`;
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return String(Math.round(n));
}

export function fmtTimeHHMMSS(ts: number | Date): string {
  const d = typeof ts === 'number' ? new Date(ts) : ts;
  if (Number.isNaN(d.getTime())) return '—'; // 无效时间（live 缺失字段）如实显「—」
  return d.toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

/** 纽约时间 HH:MM:SS（秒级走字） */
export function fmtNyTime(d: Date): string {
  return d.toLocaleTimeString('en-US', {
    timeZone: 'America/New_York',
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

export function fmtRelative(iso: string | null | undefined): string {
  if (!iso) return '—'; // live 可空时间字段（如未触发事件的 triggered_at）显「—」
  const diff = Date.now() - new Date(iso).getTime();
  if (!Number.isFinite(diff)) return '—';
  const min = Math.floor(diff / 60_000);
  if (min < 1) return t('刚刚');
  if (min < 60) return `${min} 分钟前`;
  const h = Math.floor(min / 60);
  if (h < 24) return `${h} 小时前`;
  const d = Math.floor(h / 24);
  return `${d} 天前`;
}

export function fmtCountdown(targetIso: string, now: number): string {
  const ms = Math.max(0, new Date(targetIso).getTime() - now);
  const h = Math.floor(ms / 3_600_000);
  const m = Math.floor((ms % 3_600_000) / 60_000);
  const s = Math.floor((ms % 60_000) / 1000);
  const pad = (x: number) => String(x).padStart(2, '0');
  return `${pad(h)}:${pad(m)}:${pad(s)}`;
}
