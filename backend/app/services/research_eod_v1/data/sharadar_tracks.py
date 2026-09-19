"""Three Sharadar price/volume tracks. Raw OHLCV is derived, not tape prints."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Mapping

from app.services.research_eod_v1.data.sharadar_schema import DERIVED_FLAG, FORMULA_VERSION


def _finite_positive(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _finite(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


@dataclass(frozen=True)
class PriceTracks:
    ticker: str
    session_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    closeadj: float | None
    closeunadj: float
    lastupdated: str | None
    split_scale: float
    raw_open: float
    raw_high: float
    raw_low: float
    raw_close: float
    raw_volume: float
    raw_dollar_volume: float
    split_dollar_volume: float
    dollar_volume_abs_error: float
    formula_version: str
    derivation: str
    vendor_fields: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["session_date"] = self.session_date.isoformat()
        return payload


def convert_vendor_row(row: Mapping[str, Any]) -> PriceTracks:
    close = _finite_positive(row.get("close"))
    closeunadj = _finite_positive(row.get("closeunadj"))
    open_ = _finite_positive(row.get("open"))
    high = _finite_positive(row.get("high"))
    low = _finite_positive(row.get("low"))
    volume = _finite(row.get("volume"))
    if None in (close, closeunadj, open_, high, low) or volume is None or volume < 0:
        raise ValueError("sharadar_row_missing_required_positive_prices")
    if high < max(open_, close) or low > min(open_, close):
        raise ValueError("sharadar_ohlc_relationship_violated")
    split_scale = closeunadj / close
    raw_open = open_ * split_scale
    raw_high = high * split_scale
    raw_low = low * split_scale
    raw_close = closeunadj
    raw_volume = volume / split_scale
    raw_dollar = raw_close * raw_volume
    split_dollar = close * volume
    session = row.get("date")
    if isinstance(session, date):
        session_date = session
    else:
        session_date = date.fromisoformat(str(session)[:10])
    vendor = {key: row.get(key) for key in (
        "ticker", "date", "open", "high", "low", "close", "volume", "closeadj", "closeunadj", "lastupdated"
    )}
    return PriceTracks(
        ticker=str(row.get("ticker") or ""),
        session_date=session_date,
        open=float(open_),
        high=float(high),
        low=float(low),
        close=float(close),
        volume=volume,
        closeadj=_finite_positive(row.get("closeadj")),
        closeunadj=closeunadj,  # type: ignore[arg-type]
        lastupdated=None if row.get("lastupdated") in (None, "") else str(row.get("lastupdated")),
        split_scale=split_scale,
        raw_open=raw_open,
        raw_high=raw_high,
        raw_low=raw_low,
        raw_close=raw_close,
        raw_volume=raw_volume,
        raw_dollar_volume=raw_dollar,
        split_dollar_volume=split_dollar,
        dollar_volume_abs_error=abs(raw_dollar - split_dollar),
        formula_version=FORMULA_VERSION,
        derivation=DERIVED_FLAG,
        vendor_fields=vendor,
    )


def closeadj_total_return(previous_closeadj: float | None, closeadj: float | None) -> float | None:
    prev = _finite_positive(previous_closeadj)
    current = _finite_positive(closeadj)
    if prev is None or current is None:
        return None
    return current / prev - 1.0


def dollar_volume_ok(tracks: PriceTracks, *, rel_tol: float = 1e-9, abs_tol: float = 1e-6) -> bool:
    scale = max(abs(tracks.split_dollar_volume), abs(tracks.raw_dollar_volume), 1.0)
    return tracks.dollar_volume_abs_error <= max(abs_tol, rel_tol * scale)
