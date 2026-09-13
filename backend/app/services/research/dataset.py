"""Offline OHLCV dataset for research replay.

Fetch is a separate command. Replay only reads regular files, never the
network. A content hash proves the imported file is unchanged, not that the
vendor print is true.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd

from app.services.research.protocol import NEW_YORK, assert_split_access
from app.services.strength.market_regime import MARKET_BENCHMARKS
from app.services.strength.scanner import _theme_universe


OHLCV_SCHEMA_VERSION = "research-ohlcv-v1"
MAX_DATASET_BYTES = 512 * 1024 * 1024


def research_universe() -> list[str]:
    tickers, _meta = _theme_universe()
    return list(dict.fromkeys([*tickers, *MARKET_BENCHMARKS]))


def _finite_positive(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _finite_non_negative(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _parse_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


class OfflineOHLCV:
    """Point-in-time daily bars keyed by ticker and session date."""

    def __init__(self, payload: Mapping[str, Any], *, source_path: Path | None = None):
        if payload.get("schema_version") != OHLCV_SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {OHLCV_SCHEMA_VERSION}")
        bars = payload.get("bars")
        if not isinstance(bars, list):
            raise ValueError("bars must be a list")
        by_ticker: dict[str, dict[date, dict[str, float]]] = {}
        dropped: list[dict[str, Any]] = []
        for raw in bars:
            if not isinstance(raw, Mapping):
                raise ValueError("each bar must be an object")
            ticker = str(raw.get("ticker") or "").strip().upper()
            session = _parse_date(raw.get("date"))
            close = _finite_positive(raw.get("close"))
            adj_close = _finite_positive(raw.get("adj_close") if raw.get("adj_close") is not None else raw.get("close"))
            volume = _finite_non_negative(raw.get("volume"))
            if not ticker or close is None or adj_close is None:
                dropped.append(
                    {
                        "ticker": ticker or None,
                        "date": session.isoformat() if session else None,
                        "reason": "non_positive_or_non_finite_price",
                    }
                )
                continue
            factor = adj_close / close
            record = {
                "open": _finite_positive(raw.get("open")) or close,
                "high": _finite_positive(raw.get("high")) or close,
                "low": _finite_positive(raw.get("low")) or close,
                "close": close,
                "adj_close": adj_close,
                "volume": 0.0 if volume is None else volume,
                "adj_open": (_finite_positive(raw.get("open")) or close) * factor,
                "adj_high": (_finite_positive(raw.get("high")) or close) * factor,
                "adj_low": (_finite_positive(raw.get("low")) or close) * factor,
            }
            bucket = by_ticker.setdefault(ticker, {})
            if session in bucket:
                raise ValueError(f"duplicate bar {ticker} {session}")
            bucket[session] = record
        self.by_ticker = {
            ticker: dict(sorted(points.items())) for ticker, points in by_ticker.items()
        }
        self.manifest = {
            "schema_version": OHLCV_SCHEMA_VERSION,
            "dataset_id": payload.get("dataset_id"),
            "source": payload.get("source"),
            "adjustment": payload.get("adjustment"),
            "timezone": payload.get("timezone") or "America/New_York",
            "calendar": payload.get("calendar") or "XNYS",
            "as_of": payload.get("as_of"),
            "content_sha256": payload.get("content_sha256"),
            "trust": payload.get("trust") or "external_unverified",
            "ticker_count": len(self.by_ticker),
            "bar_count": sum(len(points) for points in self.by_ticker.values()),
            "dropped_bar_count": len(dropped),
            "dropped_bars_head": dropped[:20],
            "source_path": str(source_path) if source_path is not None else None,
        }

    def tickers(self) -> list[str]:
        return sorted(self.by_ticker)

    def dates(self, ticker: str) -> list[date]:
        return list(self.by_ticker.get(ticker.upper(), {}))

    def bar(self, ticker: str, session: date) -> dict[str, float] | None:
        return self.by_ticker.get(ticker.upper(), {}).get(session)

    def coverage(self) -> dict[str, Any]:
        rows = []
        for ticker, points in sorted(self.by_ticker.items()):
            if not points:
                rows.append({"ticker": ticker, "bars": 0, "start": None, "end": None})
                continue
            first, last = min(points), max(points)
            rows.append(
                {
                    "ticker": ticker,
                    "bars": len(points),
                    "start": first.isoformat(),
                    "end": last.isoformat(),
                }
            )
        return {
            "manifest": self.manifest,
            "tickers": rows,
            "missing_universe": sorted(set(research_universe()) - set(self.by_ticker)),
        }

    def frame(
        self,
        ticker: str,
        *,
        through: date | None = None,
        adjusted: bool = True,
        allow_sealed: bool = False,
    ) -> pd.DataFrame:
        if through is not None:
            assert_split_access(through, allow_sealed=allow_sealed, purpose="price_slice")
        points = self.by_ticker.get(ticker.upper(), {})
        records: list[dict[str, Any]] = []
        for session, bar in points.items():
            if through is not None and session > through:
                continue
            if adjusted:
                records.append(
                    {
                        "date": pd.Timestamp(session),
                        "Open": bar["adj_open"],
                        "High": bar["adj_high"],
                        "Low": bar["adj_low"],
                        "Close": bar["adj_close"],
                        "Volume": bar["volume"],
                    }
                )
            else:
                records.append(
                    {
                        "date": pd.Timestamp(session),
                        "Open": bar["open"],
                        "High": bar["high"],
                        "Low": bar["low"],
                        "Close": bar["close"],
                        "Volume": bar["volume"],
                    }
                )
        if not records:
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        frame = pd.DataFrame.from_records(records).set_index("date").sort_index()
        frame.index = pd.DatetimeIndex(frame.index)
        return frame

    def adjusted_panel(
        self,
        tickers: Iterable[str] | None = None,
        *,
        through: date | None = None,
        allow_sealed: bool = False,
    ) -> pd.DataFrame:
        symbols = list(dict.fromkeys(tickers or self.tickers()))
        frames = {
            symbol: self.frame(
                symbol,
                through=through,
                adjusted=True,
                allow_sealed=allow_sealed,
            )
            for symbol in symbols
        }
        panel = pd.concat(frames, axis=1)
        panel.attrs["price_source"] = {
            "provider": self.manifest.get("source") or "offline",
            "status": "active",
            "message": "offline research OHLCV panel",
            "dataset_id": self.manifest.get("dataset_id"),
            "content_sha256": self.manifest.get("content_sha256"),
            "trust": self.manifest.get("trust"),
        }
        return panel

    def close_map(self, ticker: str, *, adjusted: bool = True) -> dict[date, float]:
        key = "adj_close" if adjusted else "close"
        return {
            session: float(bar[key])
            for session, bar in self.by_ticker.get(ticker.upper(), {}).items()
        }


def dataset_from_records(
    records: list[Mapping[str, Any]],
    *,
    dataset_id: str,
    source: str,
    as_of: datetime | None = None,
) -> OfflineOHLCV:
    payload = {
        "schema_version": OHLCV_SCHEMA_VERSION,
        "dataset_id": dataset_id,
        "source": source,
        "adjustment": {
            "ohlc": "unadjusted",
            "adj_close": "split_dividend_adjusted",
        },
        "timezone": "America/New_York",
        "calendar": "XNYS",
        "as_of": (as_of or datetime.now(timezone.utc)).isoformat(),
        "trust": "external_unverified",
        "bars": [dict(row) for row in records],
    }
    return OfflineOHLCV(payload)


def load_dataset(path: str | Path) -> OfflineOHLCV:
    supplied = Path(path).expanduser()
    if supplied.exists() and supplied.is_symlink():
        raise ValueError("dataset path must not be a symbolic link")
    resolved = supplied.resolve()
    if resolved.is_dir():
        manifest_path = resolved / "manifest.json"
        bars_path = resolved / "bars.jsonl"
        if not manifest_path.is_file() or not bars_path.is_file():
            raise ValueError("dataset directory requires manifest.json and bars.jsonl")
        if manifest_path.is_symlink() or bars_path.is_symlink():
            raise ValueError("dataset files must not be symbolic links")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        raw = bars_path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if manifest.get("content_sha256") and manifest["content_sha256"] != digest:
            raise ValueError("bars.jsonl does not match manifest content_sha256")
        records = [
            json.loads(line)
            for line in raw.decode("utf-8").splitlines()
            if line.strip()
        ]
        payload = {**manifest, "bars": records, "content_sha256": digest}
        return OfflineOHLCV(payload, source_path=resolved)
    if not resolved.is_file():
        raise ValueError("dataset must be an existing file or directory")
    size = resolved.stat().st_size
    if size > MAX_DATASET_BYTES:
        raise ValueError(f"dataset exceeds {MAX_DATASET_BYTES} bytes")
    raw = resolved.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    payload = json.loads(raw.decode("utf-8"))
    payload.setdefault("content_sha256", digest)
    return OfflineOHLCV(payload, source_path=resolved)


def write_dataset_dir(
    destination: str | Path,
    records: list[Mapping[str, Any]],
    *,
    dataset_id: str,
    source: str,
    as_of: datetime | None = None,
) -> Path:
    dest = Path(destination)
    dest.mkdir(parents=True, exist_ok=True)
    bars_path = dest / "bars.jsonl"
    with bars_path.open("w", encoding="utf-8") as handle:
        for row in records:
            handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n")
    digest = hashlib.sha256(bars_path.read_bytes()).hexdigest()
    tickers = {str(row.get("ticker")) for row in records}
    manifest = {
        "schema_version": OHLCV_SCHEMA_VERSION,
        "dataset_id": dataset_id,
        "source": source,
        "adjustment": {
            "ohlc": "unadjusted",
            "adj_close": "split_dividend_adjusted",
        },
        "timezone": "America/New_York",
        "calendar": "XNYS",
        "as_of": (as_of or datetime.now(timezone.utc)).isoformat(),
        "content_sha256": digest,
        "trust": "external_unverified",
        "ticker_count": len(tickers),
        "bar_count": len(records),
    }
    (dest / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return dest
