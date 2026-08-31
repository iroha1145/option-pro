/** 数字 / 时间格式化（等宽数字场景统一走这里） */

import { localeTag, t } from '../i18n/core.ts';

/** 界面日期：跟当前语言走，不再写死 zh-CN。无效输入显「—」。 */
export function fmtLocaleDate(iso: string, opts?: Intl.DateTimeFormatOptions): string {
  const d = new Date(iso);
  if (!iso || Number.isNaN(d.getTime())) return '—';
  return d.toLocaleDateString(localeTag(), opts ?? { year: 'numeric', month: '2-digit', day: '2-digit' });
}

/** 界面时刻：跟当前语言走。无效输入显「—」。 */
export function fmtLocaleTime(iso: string, opts?: Intl.DateTimeFormatOptions): string {
  const d = new Date(iso);
  if (!iso || Number.isNaN(d.getTime())) return '—';
  return d.toLocaleTimeString(localeTag(), opts ?? { hour12: false, hour: '2-digit', minute: '2-digit' });
}

/** 界面日期+时刻：跟当前语言走。无效输入显「—」。 */
export function fmtLocaleDateTime(iso: string, opts?: Intl.DateTimeFormatOptions): string {
  const d = new Date(iso);
  if (!iso || Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString(localeTag(), opts ?? { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}
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
  /* 统一 ET：12 处「更新 / 最近扫描 / 报价更新于」用它，而同屏页面时钟走
     fmtNyTime（ET）。原实现走浏览器本地时区且无任何时区标注——UTC+8 用户
     同屏看到「01:12:04 ET」和「更新 14:10:42」两套数字。 */
  return d.toLocaleTimeString('en-GB', {
    timeZone: 'America/New_York',
    hourCycle: 'h23',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
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

/* 事件时刻的全站统一 ET 口径。行情事件（突破触发、K 线 bar、快照观测）的
   语义时区是美东：用浏览器本地 getHours() 渲染再标「美东」，UTC+8 用户会看到
   差一整个时区的数字。hourCycle h23：en-US 在 hour12:false 下会把午夜渲染成
   24:00。 */
const NY_EVENT_FMT = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/New_York',
  month: 'numeric',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
});
const NY_HHMM_FMT = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'America/New_York',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
});

/** ISO → 美东 `M/DD HH:mm`；无效输入显「—」 */
export function fmtNyEventTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  const parts = Object.fromEntries(
    NY_EVENT_FMT.formatToParts(d).map((part) => [part.type, part.value]),
  );
  return `${parts.month}/${parts.day} ${parts.hour}:${parts.minute}`;
}

/** ISO → 美东 `HH:mm`；空值/无效输入显「—」 */
export function fmtNyHHmm(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return NY_HHMM_FMT.format(d);
}

export function fmtRelative(iso: string | null | undefined): string {
  if (!iso) return '—'; // live 可空时间字段（如未触发事件的 triggered_at）显「—」
  const diff = Date.now() - new Date(iso).getTime();
  if (!Number.isFinite(diff)) return '—';
  const min = Math.floor(diff / 60_000);
  if (min < 1) return t('刚刚');
  if (min < 60) return t('{n} 分钟前', { n: min });
  const h = Math.floor(min / 60);
  if (h < 24) return t('{n} 小时前', { n: h });
  const d = Math.floor(h / 24);
  return t('{n} 天前', { n: d });
}

/**
 * 紧凑相对时间（如 5m / 3h / 2d）。字母单位与语言无关，供来源卡等窄栏使用。
 * 早先 SourcesPanel 对 fmtRelative 的输出做 .replace(' 分钟前','m')，那在英/日
 * 界面下拿不到中文单位而失效——改为独立函数从源头产出，不再依赖措辞。
 */
export function fmtRelativeShort(iso: string | null | undefined): string {
  if (!iso) return '—';
  const diff = Date.now() - new Date(iso).getTime();
  if (!Number.isFinite(diff)) return '—';
  const min = Math.floor(diff / 60_000);
  if (min < 1) return t('刚刚');
  if (min < 60) return `${min}m`;
  const h = Math.floor(min / 60);
  if (h < 24) return `${h}h`;
  return `${Math.floor(h / 24)}d`;
}

export function fmtCountdown(targetIso: string, now: number): string {
  const ms = Math.max(0, new Date(targetIso).getTime() - now);
  const h = Math.floor(ms / 3_600_000);
  const m = Math.floor((ms % 3_600_000) / 60_000);
  const s = Math.floor((ms % 60_000) / 1000);
  const pad = (x: number) => String(x).padStart(2, '0');
  return `${pad(h)}:${pad(m)}:${pad(s)}`;
}
