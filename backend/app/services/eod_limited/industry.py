"""SIC-based industry classification for the v1.7 candidates.

The engine has always carried ``industry_id`` / ``parent_industry_id`` on each
series (industry-relative quantiles, the two-factor D residual, the G factor),
but the all-market panel never had a classification, so the fields were cleared
and G was disabled. This module supplies one from SEC SIC codes as Massive
reports them on ``/v3/reference/tickers/{ticker}`` (``sic_code``).

Table rows are ``{"ticker", "cik", "as_of", "sic_code", "sic_description"}``.
A ticker can appear several times with different CIKs (reused symbols); the
directory row's CIK picks the right one, and rows without a CIK fall back to
the ticker. Funds never receive an industry. Only common stocks and ADRs do.

With an industry mode switched on (``LIVE_CONFIG`` keeps it off), the table
has two sources:

* the table lives in ``DATA_DIR/eod-limited-v1/industry-sic-v1.json.gz`` and is
  refreshed by ``ensure_industry_tags``: every run looks up a bounded number of
  still-unclassified stock tickers through the Massive client and never blocks
  publication (a missing classification leaves G None and the security ranks
  against the whole track, exactly as today);
* ``scripts/eod_industry_table.py`` seeds that table from the frozen research
  table so the first live run does not start from an empty classification.

A static table alone would rot as listings change; unbounded runtime lookups
would put ~7,000 requests behind the worker's 30-minute first-run budget at the
client's four-request gate. Seeded plus bounded refresh keeps both problems small.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
import gzip
import json
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from app.data_paths import get_data_paths

from .universe import STOCK_PROVIDER_TYPES

TABLE_NAME = "industry-sic-v1.json.gz"
SIC_LEVELS = (2, 3, 4)
DEFAULT_LEVEL = 3
DEFAULT_LOOKUP_BUDGET = 400
SOURCE_MASSIVE_DETAIL = "massive_reference_ticker_detail"


@dataclass(frozen=True)
class IndustryTag:
    industry_id: str | None
    parent_industry_id: str | None = None
    sic_code: str | None = None

    def as_pair(self) -> tuple[str | None, str | None]:
        return self.industry_id, self.parent_industry_id


def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def sic_group(sic_code: Any, level: int) -> str | None:
    """``sic3:283`` for code 2834 at level 3; None for malformed codes."""
    if level not in SIC_LEVELS:
        raise ValueError(f"SIC level must be one of {SIC_LEVELS}")
    code = _text(sic_code)
    if code is None or not code.isdigit() or len(code) != 4:
        return None
    return f"sic{level}:{code[:level]}"


def tag_for(sic_code: Any, *, level: int = DEFAULT_LEVEL, parent_level: int | None = None) -> IndustryTag:
    """Industry at ``level`` digits; the parent stays empty unless asked for.

    The engine's parent industry is both a fallback for thin groups and the base
    group of every quantile (``PARENT_MIN_FOR_Q``). A two-digit parent would turn
    the quantiles of large groups into within-major-group ranks, so v1.7 leaves it
    empty by default.
    """
    parent = None if parent_level is None else sic_group(sic_code, parent_level)
    return IndustryTag(sic_group(sic_code, level), parent, _text(sic_code))


class SicTable:
    """Ticker/CIK -> SIC records with lookup by directory row."""

    def __init__(self, records: Iterable[Mapping[str, Any]] = ()) -> None:
        self.records: list[dict[str, Any]] = []
        self._by_pair: dict[tuple[str, str], dict[str, Any]] = {}
        self._by_ticker: dict[str, list[dict[str, Any]]] = {}
        self.extend(records)

    def __len__(self) -> int:
        return len(self.records)

    def extend(self, records: Iterable[Mapping[str, Any]]) -> int:
        added = 0
        for raw in records:
            ticker = _text(raw.get("ticker"))
            if ticker is None:
                continue
            record = {
                "ticker": ticker,
                "cik": _text(raw.get("cik")),
                "as_of": _text(raw.get("as_of")),
                "sic_code": _text(raw.get("sic_code")),
                "sic_description": _text(raw.get("sic_description")),
            }
            key = (ticker, record["cik"] or "")
            previous = self._by_pair.get(key)
            if previous is not None:
                if (previous["as_of"] or "") >= (record["as_of"] or ""):
                    continue
                self.records.remove(previous)
                self._by_ticker[ticker].remove(previous)
            self._by_pair[key] = record
            self._by_ticker.setdefault(ticker, []).append(record)
            self.records.append(record)
            added += 1
        return added

    def sic_for(self, ticker: Any, cik: Any = None) -> str | None:
        symbol = _text(ticker)
        if symbol is None:
            return None
        cik_text = _text(cik)
        if cik_text is not None:
            exact = self._by_pair.get((symbol, cik_text))
            if exact is not None:
                return exact["sic_code"]
        candidates = self._by_ticker.get(symbol) or []
        if cik_text is not None:
            candidates = [record for record in candidates if record["cik"] is None]
        if not candidates:
            return None
        latest = max(candidates, key=lambda record: record["as_of"] or "")
        return latest["sic_code"]

    def has_ticker(self, ticker: Any) -> bool:
        return _text(ticker) in self._by_ticker

    def has_pair(self, ticker: Any, cik: Any = None) -> bool:
        """True when ``sic_for(ticker, cik)`` is answered by a record (with or without a code).

        A reused ticker with a new CIK is unknown even when the old issuer is on
        file, so it is looked up again; a CIK-less record answers for any CIK.
        """
        symbol = _text(ticker)
        if symbol is None:
            return False
        records = self._by_ticker.get(symbol) or []
        cik_text = _text(cik)
        if cik_text is None:
            return bool(records)
        return (symbol, cik_text) in self._by_pair or any(record["cik"] is None for record in records)

    def classify(
        self,
        directory: Sequence[Mapping[str, Any]],
        *,
        level: int = DEFAULT_LEVEL,
        parent_level: int | None = None,
        tickers: Iterable[str] | None = None,
    ) -> dict[str, IndustryTag]:
        """One tag per classified stock-type directory row; funds and unknown codes are absent."""
        wanted = None if tickers is None else {str(item) for item in tickers}
        tags: dict[str, IndustryTag] = {}
        for raw in directory:
            ticker = _text(raw.get("ticker"))
            if ticker is None or (wanted is not None and ticker not in wanted):
                continue
            if str(raw.get("type") or "").strip().upper() not in STOCK_PROVIDER_TYPES:
                continue
            tag = tag_for(self.sic_for(ticker, raw.get("cik")), level=level, parent_level=parent_level)
            if tag.industry_id is not None:
                tags[ticker] = tag
        return tags

    @classmethod
    def load(cls, path: Path | str) -> "SicTable":
        path = Path(path)
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8") as handle:  # type: ignore[operator]
            payload = json.load(handle)
        records = payload["records"] if isinstance(payload, dict) else payload
        return cls(records)

    def save(self, path: Path | str) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        ordered = sorted(self.records, key=lambda item: (item["ticker"], item["cik"] or "", item["as_of"] or ""))
        body = json.dumps({"version": 1, "records": ordered}, sort_keys=True, ensure_ascii=False)
        temporary = path.with_suffix(path.suffix + ".tmp")
        if path.suffix == ".gz":
            with gzip.open(temporary, "wt", encoding="utf-8") as handle:
                handle.write(body)
        else:
            temporary.write_text(body, encoding="utf-8")
        temporary.replace(path)


def table_path(root: Path | str | None = None) -> Path:
    base = get_data_paths(root).root if root is not None else get_data_paths().root
    return base / "eod-limited-v1" / TABLE_NAME


def load_table(root: Path | str | None = None) -> SicTable:
    path = table_path(root)
    return SicTable.load(path) if path.exists() else SicTable()


def _massive_detail(ticker: str) -> Mapping[str, Any]:
    from app.services import massive

    return massive.reference_ticker_detail(ticker)


def directory_pairs(directory: Sequence[Mapping[str, Any]], tickers: Iterable[str] | None = None) -> list[tuple[str, str | None]]:
    """(ticker, cik) of every stock-type directory row, optionally limited to ``tickers``."""
    wanted = None if tickers is None else {str(item) for item in tickers}
    pairs = []
    for row in directory:
        ticker = _text(row.get("ticker"))
        if ticker is None or (wanted is not None and ticker not in wanted):
            continue
        if str(row.get("type") or "").strip().upper() in STOCK_PROVIDER_TYPES:
            pairs.append((ticker, _text(row.get("cik"))))
    return pairs


def refresh_missing(
    table: SicTable,
    pairs: Iterable[tuple[str, str | None] | str],
    *,
    budget: int = DEFAULT_LOOKUP_BUDGET,
    as_of: date | None = None,
    fetch: Callable[[str], Mapping[str, Any]] = _massive_detail,
) -> dict[str, int]:
    """Look up at most ``budget`` (ticker, cik) pairs the table cannot answer; failures retry next run.

    The record is stored under the directory's CIK, so a reused ticker gets one
    record per issuer; a bare ticker string means "no CIK known".
    """
    counts: Counter = Counter()
    stamp = (as_of or date.today()).isoformat()
    normalized = sorted({(str(item), None) if isinstance(item, str) else (str(item[0]), _text(item[1]))
                         for item in pairs}, key=lambda pair: (pair[0], pair[1] or ""))
    for ticker, cik in normalized:
        if table.has_pair(ticker, cik):
            continue
        if counts["looked_up"] >= budget:
            counts["deferred"] += 1
            continue
        counts["looked_up"] += 1
        try:
            detail = fetch(ticker)
        except Exception:  # noqa: BLE001 - provider trouble must not stop the worker
            counts["failed"] += 1
            continue
        table.extend([{"ticker": ticker, "cik": cik or detail.get("cik"), "as_of": stamp,
                       "sic_code": detail.get("sic_code"), "sic_description": detail.get("sic_description")}])
        counts["classified" if _text(detail.get("sic_code")) else "no_sic"] += 1
    return dict(counts)


def ensure_industry_tags(
    directory: Sequence[Mapping[str, Any]],
    *,
    root: Path | str | None = None,
    level: int = DEFAULT_LEVEL,
    parent_level: int | None = None,
    budget: int = DEFAULT_LOOKUP_BUDGET,
    tickers: Iterable[str] | None = None,
    fetch: Callable[[str], Mapping[str, Any]] = _massive_detail,
) -> tuple[dict[str, IndustryTag], dict[str, Any]]:
    """Production entry: persistent table, bounded refresh of unseen stock tickers, then classify."""
    table = load_table(root)
    wanted = None if tickers is None else {str(item) for item in tickers}
    pairs = directory_pairs(directory, wanted)
    refresh = refresh_missing(table, pairs, budget=budget, fetch=fetch) if budget > 0 else {}
    if refresh.get("looked_up"):
        table.save(table_path(root))
    tags = table.classify(directory, level=level, parent_level=parent_level, tickers=wanted)
    summary = {
        "source": "sic_table",
        "table_records": len(table),
        "level": level,
        "parent_level": parent_level,
        "stock_tickers": len({ticker for ticker, _cik in pairs}),
        "classified": len(tags),
        "groups": len({tag.industry_id for tag in tags.values()}),
        "refresh": refresh,
    }
    return tags, summary


__all__ = [
    "DEFAULT_LEVEL",
    "DEFAULT_LOOKUP_BUDGET",
    "IndustryTag",
    "SicTable",
    "TABLE_NAME",
    "directory_pairs",
    "ensure_industry_tags",
    "load_table",
    "refresh_missing",
    "sic_group",
    "table_path",
    "tag_for",
]
