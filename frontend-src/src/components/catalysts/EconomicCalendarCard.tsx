import { useMemo, useState } from 'react';
import { Link } from 'react-router';
import { localeTag } from '@/i18n/core';
import { fmtLocaleDate, fmtLocaleTime } from '@/lib/format';
import { cn } from '@/lib/utils';
import Segmented from '@/components/shared/Segmented';
import { SkeletonRows } from '@/components/shared/Skeleton';
import { calendarCopy, flatCountry, selectCalendarEvents } from './calendarPresentation';
import { useCalendarResource } from './useCalendarResource';
import CatalystCacheStatus from './CatalystCacheStatus';
import { cacheStatusProps } from './cacheStatusProps';

const BARS = { high: 'bg-down-600', medium: 'bg-warn-600', low: 'bg-brand-400', holiday: 'bg-ink-300' };
export default function EconomicCalendarCard() {
  const q = useCalendarResource();
  const copy = calendarCopy(localeTag());
  const [view, setView] = useState<'today' | 'next'>('today');
  const events = useMemo(() => selectCalendarEvents(q.data ?? [], q.now, view === 'next'), [q.data, q.now, view]);
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  return <section className="card-surface mt-8 overflow-hidden" aria-label={copy.title} data-testid="home-economic-calendar">
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-4 sm:px-5">
      <div className="min-w-0">
        <h2 className="text-body-s font-semibold text-ink-800">{copy.title}</h2>
        <p className="mt-0.5 text-micro text-ink-400">{copy.local} · {zone}</p>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Segmented value={view} onChange={setView} ariaLabel={copy.title}
          options={[{ value: 'today', label: copy.today }, { value: 'next', label: copy.next }]} />
        <Link to="/catalysts?tab=calendar" className="text-caption font-medium text-brand-600 hover:underline">{copy.all} →</Link>
      </div>
    </div>
    <CatalystCacheStatus {...cacheStatusProps(q)} />
    {q.loading && !q.data ? <SkeletonRows rows={3} /> : q.error && !q.data ?
      <div className="px-5 py-7 text-center text-caption text-ink-500">{q.error.message}
        <button type="button" onClick={q.refresh} className="ml-3 text-brand-600 underline">{copy.retry}</button></div>
      : events.length === 0 ? <p className="px-5 py-7 text-center text-caption text-ink-400">{copy.empty}</p>
      : <div className="grid grid-cols-1 lg:grid-cols-2">
        {events.slice(0, 4).map((event) => <article key={event.eventId} className="flex min-w-0 gap-3 border-b border-line px-4 py-4 sm:px-5">
          <span aria-hidden="true" className={cn('w-[3px] shrink-0 rounded-full', BARS[event.impact])} />
          <div className="w-20 shrink-0">
            <time dateTime={event.scheduledAt} className="block font-mono text-caption font-medium text-ink-700 tnum">{fmtLocaleTime(event.scheduledAt)}</time>
            {view === 'next' && <span className="block font-mono text-micro text-ink-400">{fmtLocaleDate(event.scheduledAt, { month: '2-digit', day: '2-digit' })}</span>}
            <span className="mt-1 inline-block max-w-full break-words rounded border border-line bg-paper-2 px-1.5 py-0.5 text-[10px] leading-4 text-ink-500">{flatCountry(event.country)}</span>
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-body-s font-medium leading-relaxed text-ink-800">{event.title}</p>
            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-micro text-ink-400">
              <span>{copy[event.impact]}</span>
              <span>{copy.forecast} <span className="font-mono text-ink-600 tnum">{event.forecast}</span></span>
              <span>{copy.previous} <span className="font-mono text-ink-600 tnum">{event.previous}</span></span>
              <span>{copy.actual} <span className={cn('font-mono tnum', event.actual !== null ? 'font-medium text-brand-700' : 'text-ink-400')}>
                {event.actual !== null ? event.actual : Date.parse(event.scheduledAt) <= q.now.getTime() ? copy.delayed : copy.pending}
              </span></span>
            </div>
          </div>
        </article>)}
      </div>}
    {events.length > 4 && <Link to="/catalysts?tab=calendar" className="block px-5 py-2.5 text-right text-micro text-ink-500 hover:text-brand-600">{copy.more} · {events.length - 4} →</Link>}
  </section>;
}
