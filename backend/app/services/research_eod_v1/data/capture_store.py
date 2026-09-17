"""Append-only EOD capture versions. Recapture never rewrites old retrieved_at."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from typing import Mapping, Sequence

from app.services.research_eod_v1.calendar_asof import (
    HISTORICAL_RECONSTRUCTION,
    LIVE_CAPTURE,
    last_complete_eod_session,
    last_completed_session,
    require_aware,
    session_is_partial,
)
from app.services.research_eod_v1.data.contract import ResearchBar, hash_payload


PARTIAL_INTRADAY = "PARTIAL_INTRADAY"
VENDOR_LATE = "VENDOR_LATE"
COMPLETE_EOD = "COMPLETE_EOD"
INVALID_EOD_CAPTURE = "INVALID_EOD_CAPTURE"
RECAPTURE_AFTER_CLOSE = "recapture_after_close"


def normalize_late_securities(late_securities: Sequence[str] | None) -> tuple[str, ...]:
    if not late_securities:
        return ()
    return tuple(dict.fromkeys(item.upper() for item in late_securities if item))


def stamp_bars_for_clock(bars: Sequence[ResearchBar], clock: datetime) -> tuple[ResearchBar, ...]:
    """Assign this capture's retrieved_at. Does not mutate the input bars."""

    clock = require_aware(clock, name="clock")
    stamped: list[ResearchBar] = []
    for bar in bars:
        partial = session_is_partial(bar.session_date, clock)
        vintage = "PARTIAL" if partial else (
            bar.vintage_status if bar.vintage_status != "PARTIAL" else "download_time_not_pit"
        )
        stamped.append(
            replace(
                bar,
                retrieved_at=clock,
                partial=partial,
                vintage_status=vintage,
            )
        )
    return tuple(stamped)


def classify_capture(
    *,
    clock: datetime,
    claimed_session: date | None = None,
    source_finalized_through: date | None = None,
    bars: Sequence[ResearchBar] = (),
) -> tuple[str, date, tuple[date, ...]]:
    """Return (eod_status, last_complete_session, isolated_partial_sessions)."""

    clock = require_aware(clock, name="clock")
    session = last_complete_eod_session(clock, source_finalized_through=source_finalized_through)
    partial_sessions = tuple(
        sorted(
            {
                bar.session_date
                for bar in bars
                if bar.partial or bar.vintage_status == "PARTIAL" or session_is_partial(bar.session_date, clock)
            }
        )
    )
    if claimed_session is not None and claimed_session > session:
        return INVALID_EOD_CAPTURE, session, partial_sessions
    calendar_session = last_completed_session(clock)
    if source_finalized_through is not None and session < calendar_session:
        return VENDOR_LATE, session, partial_sessions
    eod_still_partial = any(
        bar.session_date == session and (bar.partial or bar.vintage_status == "PARTIAL") for bar in bars
    )
    if eod_still_partial:
        return PARTIAL_INTRADAY, session, partial_sessions
    return COMPLETE_EOD, session, partial_sessions


def _bar_digest_payload(bar: ResearchBar) -> dict:
    payload = asdict(bar)
    return payload


@dataclass(frozen=True)
class CaptureVersion:
    capture_id: str
    retrieved_at: datetime
    capture_clock: datetime
    last_complete_eod_session: date
    eod_status: str
    bars: tuple[ResearchBar, ...]
    content_sha256: str
    predecessor_id: str | None = None
    claimed_session: date | None = None
    source_finalized_through: date | None = None
    late_securities: tuple[str, ...] = ()
    isolated_partial_sessions: tuple[date, ...] = ()
    notes: tuple[str, ...] = ()
    capture_mode: str = HISTORICAL_RECONSTRUCTION

    def metadata(self) -> dict:
        return {
            "capture_id": self.capture_id,
            "retrieved_at": self.retrieved_at.isoformat(),
            "capture_clock": self.capture_clock.isoformat(),
            "last_complete_eod_session": self.last_complete_eod_session.isoformat(),
            "eod_status": self.eod_status,
            "content_sha256": self.content_sha256,
            "predecessor_id": self.predecessor_id,
            "claimed_session": None if self.claimed_session is None else self.claimed_session.isoformat(),
            "source_finalized_through": (
                None if self.source_finalized_through is None else self.source_finalized_through.isoformat()
            ),
            "late_securities": list(self.late_securities),
            "isolated_partial_sessions": [item.isoformat() for item in self.isolated_partial_sessions],
            "bar_count": len(self.bars),
            "notes": list(self.notes),
            "capture_mode": self.capture_mode,
        }


class ImmutableCaptureStore:
    """In-memory append-only capture journal. Old versions stay byte-identical."""

    def __init__(self) -> None:
        self._versions: dict[str, CaptureVersion] = {}
        self._order: list[str] = []

    def get(self, capture_id: str) -> CaptureVersion:
        try:
            return self._versions[capture_id]
        except KeyError as exc:
            raise KeyError(f"unknown capture_id {capture_id}") from exc

    def all_versions(self) -> tuple[CaptureVersion, ...]:
        return tuple(self._versions[item] for item in self._order)

    def overwrite(self, capture_id: str, **_fields: object) -> None:
        del capture_id, _fields
        raise TypeError("capture versions are immutable; recapture must append a new version")

    def rewrite_retrieved_at(self, capture_id: str, retrieved_at: datetime) -> None:
        del capture_id, retrieved_at
        raise TypeError("retrieved_at is frozen after append; create a new capture version")

    def record_capture(
        self,
        *,
        clock: datetime,
        bars: Sequence[ResearchBar],
        claimed_session: date | None = None,
        source_finalized_through: date | None = None,
        late_securities: Sequence[str] = (),
        predecessor_id: str | None = None,
        notes: Sequence[str] = (),
        stamp: bool = True,
        capture_mode: str = HISTORICAL_RECONSTRUCTION,
    ) -> CaptureVersion:
        clock = require_aware(clock, name="clock")
        if predecessor_id is not None:
            self.get(predecessor_id)
        stamped = stamp_bars_for_clock(bars, clock) if stamp else tuple(bars)
        status, session, partial_sessions = classify_capture(
            clock=clock,
            claimed_session=claimed_session,
            source_finalized_through=source_finalized_through,
            bars=stamped,
        )
        digest = hash_payload([_bar_digest_payload(bar) for bar in stamped])
        capture_id = f"cap_{len(self._order):04d}_{digest[:12]}"
        if capture_id in self._versions:
            raise ValueError(f"capture_id {capture_id} already exists")
        version = CaptureVersion(
            capture_id=capture_id,
            retrieved_at=clock,
            capture_clock=clock,
            last_complete_eod_session=session,
            eod_status=status,
            bars=stamped,
            content_sha256=digest,
            predecessor_id=predecessor_id,
            claimed_session=claimed_session,
            source_finalized_through=source_finalized_through,
            late_securities=normalize_late_securities(late_securities),
            isolated_partial_sessions=partial_sessions,
            notes=tuple(notes),
            capture_mode=capture_mode if capture_mode in {LIVE_CAPTURE, HISTORICAL_RECONSTRUCTION} else HISTORICAL_RECONSTRUCTION,
        )
        self._versions[capture_id] = version
        self._order.append(capture_id)
        return version

    def recapture_last_bar(
        self,
        *,
        predecessor_id: str,
        clock: datetime,
        bars: Sequence[ResearchBar],
        claimed_session: date | None = None,
        source_finalized_through: date | None = None,
        late_securities: Sequence[str] = (),
        notes: Sequence[str] = (),
        capture_mode: str = LIVE_CAPTURE,
    ) -> CaptureVersion:
        """After close, replace the last/partial root in a *new* version.

        Predecessor ``retrieved_at``, hash, and bars stay unchanged.
        Historical complete bars keep their original ``retrieved_at``.
        """

        predecessor = self.get(predecessor_id)
        predecessor_retrieved = predecessor.retrieved_at
        predecessor_hash = predecessor.content_sha256
        recapture_sessions = {
            bar.session_date
            for bar in predecessor.bars
            if bar.partial or bar.vintage_status == "PARTIAL"
        }
        if not recapture_sessions and predecessor.bars:
            recapture_sessions = {max(bar.session_date for bar in predecessor.bars)}
        merged = _merge_recapture(predecessor.bars, bars, recapture_sessions, clock)
        version = self.record_capture(
            clock=clock,
            bars=merged,
            claimed_session=claimed_session,
            source_finalized_through=source_finalized_through,
            late_securities=late_securities or predecessor.late_securities,
            predecessor_id=predecessor.capture_id,
            notes=tuple(notes) + (RECAPTURE_AFTER_CLOSE,),
            stamp=False,
            capture_mode=capture_mode,
        )
        stored = self.get(predecessor.capture_id)
        if stored.retrieved_at != predecessor_retrieved:
            raise RuntimeError("predecessor retrieved_at mutated")
        if stored.content_sha256 != predecessor_hash:
            raise RuntimeError("predecessor content hash mutated")
        if stored.bars != predecessor.bars:
            raise RuntimeError("predecessor bars mutated")
        return version


def _merge_recapture(
    predecessor_bars: Sequence[ResearchBar],
    new_bars: Sequence[ResearchBar],
    recapture_sessions: set[date],
    clock: datetime,
) -> tuple[ResearchBar, ...]:
    clock = require_aware(clock, name="clock")
    by_key: dict[tuple[str, date], ResearchBar] = {
        (bar.security_id, bar.session_date): bar for bar in predecessor_bars
    }
    for bar in new_bars:
        if bar.session_date not in recapture_sessions:
            if (bar.security_id, bar.session_date) not in by_key:
                by_key[(bar.security_id, bar.session_date)] = _stamp_recapture_bar(bar, clock)
            continue
        by_key[(bar.security_id, bar.session_date)] = _stamp_recapture_bar(bar, clock)
    return tuple(sorted(by_key.values(), key=lambda item: (item.security_id, item.session_date)))


def _stamp_recapture_bar(bar: ResearchBar, clock: datetime) -> ResearchBar:
    partial = session_is_partial(bar.session_date, clock)
    vintage = "PARTIAL" if partial else RECAPTURE_AFTER_CLOSE
    return replace(
        bar,
        retrieved_at=clock,
        partial=partial,
        vintage_status=vintage,
        finalized_at=bar.finalized_at,
    )


def eod_pool_exclusions(
    late_securities: Sequence[str] | None = None,
) -> frozenset[str]:
    return frozenset(normalize_late_securities(late_securities))


def series_is_late(series: Mapping[str, object] | object, late: frozenset[str]) -> bool:
    if not late:
        return False
    security_id = str(getattr(series, "security_id", "") or "").upper()
    ticker = str(getattr(series, "ticker_at_signal", "") or "").upper()
    return security_id in late or ticker in late
