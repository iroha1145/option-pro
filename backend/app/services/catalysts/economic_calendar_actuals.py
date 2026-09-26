"""Best-effort actual-value enrichment for recent economic-calendar events.

MacroLens remains the calendar-of-record.  TradingView is queried only for
already-released events whose upstream ``actual`` value is still empty.
Matches are conservative: currency and release time must agree, then the
translated title or published forecast/previous values must identify one
unambiguous source row.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
import re
import time as monotonic_time
from typing import Any, Mapping, Sequence

import httpx

from .local_intelligence import _public_calendar_title


_TRADINGVIEW_CALENDAR_URL = "https://economic-calendar.tradingview.com/events"
_PROVIDER = "TradingView Economic Calendar"
_CACHE_TTL_SECONDS = 300.0
_FAILURE_CACHE_TTL_SECONDS = 60.0
_FETCH_TIMEOUT_SECONDS = 5.0
_MAX_SOURCE_ROWS = 8_000
_MAX_ENRICH_DAYS = 4
_MIN_CANDIDATE_SCORE = 6
_cache: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = {}
_failure_cache: dict[tuple[str, str], float] = {}
_cache_locks: dict[tuple[str, str], asyncio.Lock] = {}
_cache_lock_users: dict[tuple[str, str], int] = {}
_VALUE_RE = re.compile(r"^([-+]?\d+(?:\.\d+)?)([KMBT%]?)$", re.IGNORECASE)


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _plain_decimal(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    text = format(number.normalize(), "f")
    return "0" if text in {"-0", ""} else text


def _has_value(value: Any) -> bool:
    return value is not None and bool(str(value).strip())


def _format_source_value(row: Mapping[str, Any], field: str) -> str | None:
    raw = str(row.get(field) if row.get(field) is not None else "").strip()
    raw = (
        raw.replace(",", "")
        .replace("−", "-")
        .replace("–", "-")
        .replace("£", "")
        .replace("€", "")
        .replace("$", "")
        .replace("¥", "")
        .upper()
    )
    match = _VALUE_RE.fullmatch(raw)
    if match is None:
        return None
    text = _plain_decimal(match.group(1))
    if text is None:
        return None
    suffix = match.group(2).upper()
    if suffix:
        text = f"{text}{suffix}"
    scale = str(row.get("scale") or "").strip().upper()
    if (
        scale in {"K", "M", "B", "T"}
        and suffix not in {"K", "M", "B", "T"}
    ):
        text = f"{text}{scale}"
    unit = str(row.get("unit") or "").strip()
    if unit == "%" and suffix != "%":
        text = f"{text}%"
    return text


def _value_signature(value: Any) -> tuple[Decimal, str] | None:
    text = str(value or "").strip().upper()
    if not text:
        return None
    text = (
        text.replace(",", "")
        .replace("−", "-")
        .replace("–", "-")
        .replace("£", "")
        .replace("€", "")
        .replace("$", "")
        .replace("¥", "")
    )
    match = _VALUE_RE.fullmatch(text)
    if match is None:
        return None
    try:
        number = Decimal(match.group(1)).normalize()
    except InvalidOperation:
        return None
    return number, match.group(2).upper()


def _title_signature(value: Any) -> str:
    public = _public_calendar_title(value)
    return re.sub(r"[\W_]+", "", public.casefold(), flags=re.UNICODE)


# ── AI-28b：标题相似度门槛 ────────────────────────────────────
#
# _candidate_score used to let a numeric coincidence (forecast+previous both
# matching) qualify a candidate on its own, with an exact _title_signature
# match only adding bonus points. Two distinct indicators released at the
# same instant in the same currency can share forecast/previous by pure
# chance, and an exact signature match is rare across providers on purpose:
# an event's title has already been through local_intelligence's
# _public_calendar_title once (see its own calendar serialization), and that
# function deliberately keeps the upstream English original in a trailing
# "（...）" whenever it recognized a qualifier (core / m-m / y-y / a digit),
# precisely so it never conflates e.g. core and headline CPI. That means the
# English words identifying the specific release are still present in the
# event's now-mostly-Chinese title, and a candidate's own English title/
# indicator carries the same words spelled its own way (an acronym vs. the
# spelled-out name, "m/m" vs. "MoM"). We compare only those words: a shared
# qualifier must match exactly (a different cadence or core-vs-headline is a
# different number and must not borrow another release's actual value), and
# the remaining core words must overlap substantially. This is deliberately a
# small, curated list rather than a general synonym dictionary — a release
# outside it is compared on its own words only, never guessed at.
_TITLE_RELEASE_PHRASES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), canonical)
    for pattern, canonical in (
        (r"\b(?:cpi|consumer price index)\b", "cpi"),
        (r"\b(?:ppi|producer price index)\b", "ppi"),
        (r"\bpce price index\b|\bpce\b|\bpersonal consumption expenditures?\b", "pce"),
        (r"\bgdp\b|\bgross domestic product\b", "gdp"),
        (r"\bnfp\b|\bnon-?farm payrolls?\b", "nfp"),
        (r"\bpmi\b|\bpurchasing managers.? index\b", "pmi"),
        (r"\bunemployment rate\b", "unemploymentrate"),
        (r"\bunemployment claims\b|\binitial jobless claims\b", "initialjoblessclaims"),
        (r"\bcontinuing jobless claims\b", "continuingjoblessclaims"),
        (r"\bretail sales\b", "retailsales"),
        (r"\bindustrial production\b", "industrialproduction"),
        (r"\bconsumer confidence\b", "consumerconfidence"),
        (r"\bexisting home sales\b", "existinghomesales"),
        (r"\bnew home sales\b", "newhomesales"),
        (r"\bhousing starts\b", "housingstarts"),
        (r"\bbuilding permits?\b", "buildingpermits"),
        (r"\bdurable goods orders?\b", "durablegoodsorders"),
        (r"\btrade balance\b", "tradebalance"),
        (r"\bjob openings\b", "jobopenings"),
        (r"\binterest rate decision\b|\brate decision\b", "ratedecision"),
    )
)

_TITLE_FREQUENCY_PHRASES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), canonical)
    for pattern, canonical in (
        (r"\b(?:m/m|mom|month[- ]on[- ]month)\b", "mom"),
        (r"\b(?:y/y|yoy|year[- ]on[- ]year)\b", "yoy"),
        (r"\b(?:q/q|qoq|quarter[- ]on[- ]quarter)\b", "qoq"),
        (r"\b(?:w/w|wow|week[- ]on[- ]week)\b", "wow"),
    )
)

# Qualifiers that change what number a release actually reports. These must
# match exactly between the two titles; they are never "overlap enough".
_TITLE_QUALIFIER_TOKENS = frozenset(
    {
        "mom", "yoy", "qoq", "wow",
        "core", "final", "preliminary", "flash", "advance", "revised", "headline",
    }
)

_TITLE_STOPWORDS = frozenset(
    {"the", "a", "an", "of", "and", "for", "in", "index", "report", "data", "level", "total", "net", "change"}
)

_TITLE_RELATED_MIN_OVERLAP = 0.6


def _title_tokens(value: Any) -> frozenset[str]:
    """English tokens used only by the fuzzy relatedness check below.

    Deliberately does not call _public_calendar_title: it works directly off
    whatever text is given (a raw upstream title, or an event title that has
    already been translated but still carries its English original in a
    "（...）" suffix — see the module note above), so it never depends on
    running that translation a second time.
    """

    folded = re.sub(r"\s+", " ", str(value or "").casefold()).strip()
    for pattern, canonical in _TITLE_RELEASE_PHRASES:
        folded = pattern.sub(canonical, folded)
    for pattern, canonical in _TITLE_FREQUENCY_PHRASES:
        folded = pattern.sub(canonical, folded)
    words = re.findall(r"[a-z0-9]+", folded)
    return frozenset(word for word in words if len(word) > 1 and word not in _TITLE_STOPWORDS)


def _titles_are_related(event_title: Any, candidate_title: Any) -> bool:
    """Looser than an exact _title_signature match, but still release-specific.

    Requires: (1) both sides yield at least one recognizable English token;
    (2) any qualifier tokens present (core/mom/yoy/...) match exactly, so a
    different cadence or core-vs-headline release is never treated as
    related; (3) the remaining core words overlap at least
    _TITLE_RELATED_MIN_OVERLAP of the shorter side's word count.
    """

    event_tokens = _title_tokens(event_title)
    candidate_tokens = _title_tokens(candidate_title)
    if not event_tokens or not candidate_tokens:
        return False
    if (event_tokens & _TITLE_QUALIFIER_TOKENS) != (candidate_tokens & _TITLE_QUALIFIER_TOKENS):
        return False
    event_core = event_tokens - _TITLE_QUALIFIER_TOKENS
    candidate_core = candidate_tokens - _TITLE_QUALIFIER_TOKENS
    if not event_core or not candidate_core:
        return False
    shared = event_core & candidate_core
    if not shared:
        return False
    return len(shared) / min(len(event_core), len(candidate_core)) >= _TITLE_RELATED_MIN_OVERLAP


def _candidate_score(
    event: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    as_of: datetime,
) -> int | None:
    event_time = _parse_time(event.get("scheduled_at_utc") or event.get("scheduled_at"))
    source_time = _parse_time(candidate.get("date"))
    if event_time is None or source_time is None or source_time > as_of:
        return None
    if abs((event_time - source_time).total_seconds()) > 120:
        return None
    event_currency = str(event.get("currency") or event.get("country_code") or "").upper()
    source_currency = str(candidate.get("currency") or "").upper()
    if not event_currency or source_currency != event_currency:
        return None
    if _format_source_value(candidate, "actual") is None:
        return None

    score = 0
    event_title_raw = event.get("title")
    candidate_title_raw = candidate.get("title")
    candidate_indicator_raw = candidate.get("indicator")
    event_title = _title_signature(event_title_raw)
    source_titles = {
        _title_signature(candidate_title_raw),
        _title_signature(candidate_indicator_raw),
    }
    source_titles.discard("")
    exact_title_match = bool(event_title) and event_title in source_titles
    if exact_title_match:
        score += 8
    elif not (
        _titles_are_related(event_title_raw, candidate_title_raw)
        or _titles_are_related(event_title_raw, candidate_indicator_raw)
    ):
        # AI-28b: same instant + same currency + coincidentally matching
        # forecast/previous is not enough — the candidate must also name a
        # related release, or it must not be used to fill in an actual.
        return None

    for field, weight in (("forecast", 4), ("previous", 2)):
        expected = _value_signature(event.get(field))
        observed = _value_signature(_format_source_value(candidate, field))
        if expected is not None and observed is not None and expected == observed:
            score += weight
    return score


def merge_recent_actuals(
    payload: Mapping[str, Any],
    source_rows: Sequence[Mapping[str, Any]],
    *,
    as_of: datetime,
) -> tuple[dict[str, Any], int, int]:
    """Return a copied payload with unambiguous recent actual values filled."""

    output = deepcopy(dict(payload))
    raw_items = output.get("items")
    if not isinstance(raw_items, list):
        return output, 0, 0
    rows = [row for row in source_rows if isinstance(row, Mapping)]
    attempted = 0
    filled = 0
    for value in raw_items:
        if not isinstance(value, dict):
            continue
        if _has_value(value.get("actual")):
            continue
        scheduled = _parse_time(value.get("scheduled_at_utc") or value.get("scheduled_at"))
        recent_start = as_of.date() - timedelta(days=_MAX_ENRICH_DAYS - 1)
        if (
            scheduled is None
            or scheduled > as_of
            or scheduled.date() < recent_start
        ):
            continue
        attempted += 1
        ranked: list[tuple[int, Mapping[str, Any]]] = []
        for candidate in rows:
            score = _candidate_score(value, candidate, as_of=as_of)
            if score is not None:
                ranked.append((score, candidate))
        ranked.sort(key=lambda item: item[0], reverse=True)
        if not ranked or ranked[0][0] < _MIN_CANDIDATE_SCORE:
            continue
        if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
            continue
        actual = _format_source_value(ranked[0][1], "actual")
        if actual is None:
            continue
        value["actual"] = actual
        value["release_status"] = "released"
        value["actual_source"] = _PROVIDER
        filled += 1
    return output, filled, attempted


def _prune_expired(now: float) -> None:
    """Drop expired windows: keys follow the calendar date, so they never repeat."""

    for key in [key for key, (expires, _rows) in _cache.items() if expires <= now]:
        del _cache[key]
    for key in [key for key, until in _failure_cache.items() if until <= now]:
        del _failure_cache[key]
    for key in [
        key
        for key in _cache_locks
        if (
            key not in _cache
            and key not in _failure_cache
            and key not in _cache_lock_users
        )
    ]:
        del _cache_locks[key]


async def _fetch_source_rows(date_from: date, date_to: date) -> list[dict[str, Any]]:
    key = (date_from.isoformat(), date_to.isoformat())
    now = monotonic_time.monotonic()
    _prune_expired(now)
    cached = _cache.get(key)
    if cached is not None and cached[0] > now:
        return deepcopy(cached[1])
    if _failure_cache.get(key, 0.0) > now:
        raise ValueError("economic calendar fallback is cooling down")
    lock = _cache_locks.setdefault(key, asyncio.Lock())
    _cache_lock_users[key] = _cache_lock_users.get(key, 0) + 1
    try:
        return await _fetch_source_rows_locked(key, lock, date_from, date_to)
    finally:
        users = _cache_lock_users[key] - 1
        if users:
            _cache_lock_users[key] = users
        else:
            del _cache_lock_users[key]
            if (
                key not in _cache
                and key not in _failure_cache
                and _cache_locks.get(key) is lock
            ):
                del _cache_locks[key]


async def _fetch_source_rows_locked(
    key: tuple[str, str],
    lock: asyncio.Lock,
    date_from: date,
    date_to: date,
) -> list[dict[str, Any]]:
    async with lock:
        cached = _cache.get(key)
        now = monotonic_time.monotonic()
        if cached is not None and cached[0] > now:
            return deepcopy(cached[1])
        if _failure_cache.get(key, 0.0) > now:
            raise ValueError("economic calendar fallback is cooling down")
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(_FETCH_TIMEOUT_SECONDS),
                headers={
                    "Origin": "https://www.tradingview.com",
                    "Referer": "https://www.tradingview.com/",
                    "User-Agent": "Optix-Pro/1.0 economic-calendar",
                    "Accept": "application/json",
                },
            ) as client:
                response = await client.get(
                    _TRADINGVIEW_CALENDAR_URL,
                    params={
                        "from": datetime.combine(
                            date_from,
                            time.min,
                            tzinfo=timezone.utc,
                        ).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                        "to": datetime.combine(
                            date_to,
                            time.max,
                            tzinfo=timezone.utc,
                        ).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                    },
                )
                response.raise_for_status()
                body = response.json()
            raw_rows = body.get("result") if isinstance(body, dict) else None
            if not isinstance(raw_rows, list):
                raise ValueError(
                    "economic calendar fallback returned an invalid payload"
                )
        except (httpx.HTTPError, TypeError, ValueError):
            _failure_cache[key] = (
                monotonic_time.monotonic() + _FAILURE_CACHE_TTL_SECONDS
            )
            raise
        rows = [
            dict(row)
            for row in raw_rows[:_MAX_SOURCE_ROWS]
            if isinstance(row, Mapping)
        ]
        _failure_cache.pop(key, None)
        _cache[key] = (monotonic_time.monotonic() + _CACHE_TTL_SECONDS, rows)
        return deepcopy(rows)


def _peek_cached_source_rows(
    date_from: date,
    date_to: date,
) -> list[dict[str, Any]] | None:
    """Return fresh cached provider rows without any network work."""

    cached = _cache.get((date_from.isoformat(), date_to.isoformat()))
    if cached is not None and cached[0] > monotonic_time.monotonic():
        return deepcopy(cached[1])
    return None


async def enrich_recent_actuals(
    payload: Mapping[str, Any],
    *,
    date_from: date,
    date_to: date,
    as_of: datetime,
    allow_fetch: bool = True,
) -> dict[str, Any]:
    """Fill recent missing actuals without making the calendar route fragile.

    ``allow_fetch=False`` keeps the call strictly local: a fresh in-process
    cache is still merged, but no external provider request is made. Visitors
    run in this mode unless the owner opted in via
    ``access.visitor_live_pulls``.
    """

    observed = as_of.astimezone(timezone.utc)
    recent_start = observed.date() - timedelta(days=_MAX_ENRICH_DAYS - 1)
    start_date = max(date_from, recent_start)
    end_date = min(date_to, observed.date())
    output = deepcopy(dict(payload))
    if end_date < start_date:
        return output
    missing_past = [
        item
        for item in output.get("items", [])
        if isinstance(item, Mapping)
        and not _has_value(item.get("actual"))
        and (
            scheduled := _parse_time(
                item.get("scheduled_at_utc") or item.get("scheduled_at")
            )
        )
        is not None
        and start_date <= scheduled.date() <= end_date
        and scheduled <= observed
    ]
    if not missing_past:
        output["actual_fallback"] = {
            "provider": _PROVIDER,
            "status": "not_needed",
            "attempted": 0,
            "filled": 0,
        }
        return output
    if not allow_fetch:
        cached_rows = _peek_cached_source_rows(start_date, end_date)
        if cached_rows is None:
            output["actual_fallback"] = {
                "provider": _PROVIDER,
                "status": "skipped",
                "reason": "visitor_read_only",
                "attempted": len(missing_past),
                "filled": 0,
            }
            return output
        output, filled, attempted = merge_recent_actuals(
            output,
            cached_rows,
            as_of=observed,
        )
        output["actual_fallback"] = {
            "provider": _PROVIDER,
            "status": "active" if filled else "no_match",
            "attempted": attempted,
            "filled": filled,
        }
        return output
    try:
        source_rows = await _fetch_source_rows(start_date, end_date)
        output, filled, attempted = merge_recent_actuals(
            output,
            source_rows,
            as_of=observed,
        )
        output["actual_fallback"] = {
            "provider": _PROVIDER,
            "status": "active" if filled else "no_match",
            "attempted": attempted,
            "filled": filled,
        }
    except (httpx.HTTPError, TypeError, ValueError):
        output["actual_fallback"] = {
            "provider": _PROVIDER,
            "status": "unavailable",
            "attempted": len(missing_past),
            "filled": 0,
        }
    return output
