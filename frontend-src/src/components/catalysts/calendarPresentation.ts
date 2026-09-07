import { getLocale } from '../../i18n/core.ts';

/** Country display never depends on OS emoji rendering. */
function isRegionalIndicator(cp: number): boolean {
  return cp >= 0x1f1e6 && cp <= 0x1f1ff;
}
function isFlagMarkup(cp: number): boolean {
  return isRegionalIndicator(cp)
    || cp === 0x1f3f3 || cp === 0x1f3f4 || cp === 0xfe0f || cp === 0x200d
    || (cp >= 0xe0020 && cp <= 0xe007f);
}
export function flatCountry(value: string): string {
  const chars = [...value];
  const flags = chars.filter((c) => isRegionalIndicator(c.codePointAt(0) ?? 0));
  const text = chars.filter((c) => !isFlagMarkup(c.codePointAt(0) ?? 0)).join('').replace(/\s+/g, ' ').trim();
  if (text) return text;
  return flags.length === 2
    ? flags.map((c) => String.fromCharCode((c.codePointAt(0) ?? 0) - 0x1f1e6 + 65)).join('') : '—';
}
export function localDay(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}
export interface CalendarRecord { scheduledAt: string; impact: string; actual: string | null }
export function selectCalendarEvents<T extends CalendarRecord>(items: readonly T[], now: Date, upcoming: boolean): T[] {
  const today = localDay(now);
  return items.filter((item) => Number.isFinite(Date.parse(item.scheduledAt)))
    .filter((item) => upcoming ? Date.parse(item.scheduledAt) >= now.getTime()
      : localDay(new Date(item.scheduledAt)) === today)
    .sort((a, b) => Date.parse(a.scheduledAt) - Date.parse(b.scheduledAt));
}
export function calendarInterval(items: readonly CalendarRecord[] | null, now = Date.now()): number {
  const nearRelease = items?.some((item) => item.actual === null && item.impact !== 'holiday'
    && Date.parse(item.scheduledAt) - now <= 15 * 60_000 && Date.parse(item.scheduledAt) - now >= -60 * 60_000);
  return nearRelease ? 60_000 : 300_000;
}
export function calendarCopy(locale: string = getLocale()) {
  const en = {
    title: 'Economic calendar', today: 'Today', next: 'Upcoming', all: 'Full calendar',
    local: 'Local time', empty: 'No economic events in this window', forecast: 'Forecast',
    previous: 'Previous', actual: 'Actual', pending: 'Awaiting release', delayed: 'Awaiting source',
    checked: 'Last checked', checking: 'Checking for updates', cached: 'Showing saved data',
    failed: 'Update failed; showing saved data', retry: 'Retry', more: 'More events',
    high: 'High', medium: 'Medium', low: 'Low', holiday: 'Holiday', limited: 'Showing the most recent 240 loaded articles',
  };
  if (locale.startsWith('ja')) return { ...en, title: '経済カレンダー', today: '今日', next: '今後',
    all: 'すべて表示', local: '現地時間', empty: 'この期間の経済イベントはありません', forecast: '予想',
    previous: '前回', actual: '結果', pending: '発表待ち', delayed: 'データ更新待ち', checked: '最終確認',
    checking: '更新を確認中', cached: '保存済みデータを表示', failed: '更新に失敗しました。保存済みデータを表示', retry: '再試行',
    more: 'ほかのイベント', high: '高', medium: '中', low: '低', holiday: '休場', limited: '読み込み済みの直近240件を表示' };
  if (locale.startsWith('zh')) return { ...en, title: '经济日历', today: '今日', next: '即将公布',
    all: '完整日历', local: '本地时间', empty: '本窗口暂无经济事件', forecast: '预期', previous: '前值', actual: '实际',
    pending: '待公布', delayed: '数据源未回填', checked: '最后核验', checking: '正在检查更新', cached: '显示已保存的数据',
    failed: '更新失败，保留上次数据', retry: '重试', more: '更多事件', high: '高', medium: '中', low: '低', holiday: '假期',
    limited: '保留最近加载的 240 条新闻' };
  return en;
}
