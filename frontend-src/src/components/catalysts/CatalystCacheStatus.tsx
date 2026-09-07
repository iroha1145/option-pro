import { localeTag } from '@/i18n/core';
import { fmtLocaleDate, fmtLocaleTime } from '@/lib/format';
import { calendarCopy } from './calendarPresentation';

export default function CatalystCacheStatus({ data, error, refreshing, restored, validatedAt, refresh }: {
  data: unknown; error: unknown; refreshing: boolean; restored: boolean; validatedAt: number; refresh: () => void;
}) {
  const copy = calendarCopy(localeTag());
  if (data === null) return null;
  return <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line bg-paper-2/40 px-4 py-2 text-micro text-ink-400" data-testid="catalyst-cache-status">
    <span role="status">{error ? copy.failed : refreshing ? copy.checking : restored ? copy.cached : copy.checked}
      {validatedAt > 0 && <time className="ml-2 font-mono tnum" dateTime={new Date(validatedAt).toISOString()}
        title={new Date(validatedAt).toLocaleString(localeTag())}>{fmtLocaleDate(new Date(validatedAt).toISOString(), { month: '2-digit', day: '2-digit' })} {fmtLocaleTime(new Date(validatedAt).toISOString())}</time>}
    </span>
    {Boolean(error) && <button type="button" className="font-medium text-brand-600 underline underline-offset-2" onClick={refresh}>{copy.retry}</button>}
  </div>;
}
