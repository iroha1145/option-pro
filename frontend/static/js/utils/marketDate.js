const MARKET_TIME_ZONE = 'America/New_York';

const MARKET_DATE_FORMATTER = new Intl.DateTimeFormat('en-US', {
  timeZone: MARKET_TIME_ZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
});

function validDate(value) {
  const date = value instanceof Date ? new Date(value.getTime()) : new Date(value);
  if (Number.isNaN(date.getTime())) throw new TypeError('marketTodayISO requires a valid date');
  return date;
}

function isoParts(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || ''));
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const utc = new Date(Date.UTC(year, month - 1, day));
  if (
    utc.getUTCFullYear() !== year
    || utc.getUTCMonth() !== month - 1
    || utc.getUTCDate() !== day
  ) return null;
  return { year, month, day };
}

/** Return the New York market calendar date for a supplied instant. */
export function marketTodayISO(now = new Date()) {
  const parts = Object.fromEntries(
    MARKET_DATE_FORMATTER.formatToParts(validDate(now))
      .filter((part) => part.type !== 'literal')
      .map((part) => [part.type, part.value]),
  );
  return `${parts.year}-${parts.month}-${parts.day}`;
}

/** Calendar-day distance that is not distorted by daylight-saving changes. */
export function marketCalendarDayDelta(fromISO, toISO) {
  const from = isoParts(fromISO);
  const to = isoParts(toISO);
  if (!from || !to) return null;
  const fromTime = Date.UTC(from.year, from.month - 1, from.day);
  const toTime = Date.UTC(to.year, to.month - 1, to.day);
  return Math.round((toTime - fromTime) / 86_400_000);
}

export { MARKET_TIME_ZONE };
