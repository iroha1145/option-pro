import { useEffect, useState } from 'react';
import { browserCalendarQuery, catalystsContract, type EconomicEvent } from './api';
import { calendarInterval } from './calendarPresentation';
import type { ResourcePolicy } from './resourceCache';
import { useCatalystResource } from './useCatalystResource';

const CALENDAR_POLICY: ResourcePolicy<EconomicEvent[]> = {
  validate: (value): value is EconomicEvent[] => Array.isArray(value) && value.every((item) =>
    item && typeof item.eventId === 'string' && typeof item.country === 'string'
    && typeof item.title === 'string' && Number.isFinite(Date.parse(item.scheduledAt))
    && ['high', 'medium', 'low', 'holiday'].includes(item.impact)
    && (item.actual === null || typeof item.actual === 'string')),
  freshMs: (data) => calendarInterval(data), retainMs: 7 * 24 * 60 * 60_000,
};
export function useCalendarResource() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const tick = () => { if (document.visibilityState === 'visible') setNow(new Date()); };
    const timer = setInterval(tick, 60_000);
    document.addEventListener('visibilitychange', tick);
    return () => { clearInterval(timer); document.removeEventListener('visibilitychange', tick); };
  }, []);
  const query = browserCalendarQuery(now);
  const q = useCatalystResource(`calendar:${JSON.stringify(query)}`, CALENDAR_POLICY,
    () => catalystsContract.calendar(query));
  return { ...q, now };
}
