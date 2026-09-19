"""Security identity, daily pool tags, and holding-window terminal labels."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Iterable, Mapping, Sequence

from app.services.research_eod_v1.data.sharadar_schema import (
    ACTION_ACQUISITION_CASH,
    ACTION_ACQUISITION_ELECT_CASH,
    ACTION_ACQUISITION_STOCK,
    ACTION_BANKRUPTCY,
    ACTION_DELISTED,
    ADV20_MAX_GAP_DAYS,
    CLASSIFICATION_CURRENT,
    CLOSEUNADJ_MIN,
    ETF_SUBASSET_MAPPING_MANUAL,
    ETF_SUBASSETS,
    FUND_CATEGORIES,
    PRICE_HISTORY_RECONSTRUCTED,
    TERMINAL_ACQUISITION_CASH,
    TERMINAL_BANKRUPTCY,
    TERMINAL_UNKNOWN,
    UNADJ_ADV20_MIN,
    VENUE_HISTORY_UNVERIFIED,
    VENUE_OK,
)
from app.services.research_eod_v1.data.sharadar_tracks import PriceTracks

# Exact vendor categories that form the strategy pool. Preferred stock, warrants,
# units, Canadian stock and every fund kind are outside by construction.
_COMMON_STOCK_RE = re.compile(
    r"^(domestic|adr) common stock( primary class| secondary class)?$",
    re.IGNORECASE,
)
_PRIMARY_CLASS_RE = re.compile(r"primary class", re.IGNORECASE)
_SECONDARY_CLASS_RE = re.compile(r"secondary class", re.IGNORECASE)
_BANKRUPTCY_RE = re.compile(
    r"bankrupt|chapter\s*11|chapter\s*7|liquidat|cancelled|fdic|receivership",
    re.IGNORECASE,
)
# Sharadar appends a number to the ticker of a delisted company whose symbol was
# later reused (DELL1 = the 2013 take-private company, DELL = the current one).
_TICKER_SUFFIX_RE = re.compile(r"^(?P<base>[A-Z][A-Z0-9.\-]*[A-Z.])(?P<suffix>\d+)$")


def split_ticker_suffix(ticker: str | None) -> tuple[str, str | None]:
    text = (ticker or "").strip().upper()
    match = _TICKER_SUFFIX_RE.match(text)
    if not match:
        return text, None
    return match.group("base"), match.group("suffix")


@dataclass(frozen=True)
class SharadarIdentity:
    security_id: str
    permaticker: str
    ticker: str
    name: str | None
    share_class: str | None
    category: str | None
    currency: str | None
    exchange: str | None
    isdelisted: bool | None
    relatedtickers: tuple[str, ...]
    issuer_id: str
    asset_track: str
    security_type: str
    firstpricedate: str | None
    lastpricedate: str | None
    flags: tuple[str, ...]
    identity_confidence: str = "permaticker"
    ticker_base: str = ""
    ticker_suffix: str | None = None
    master_table: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def covers(self, session: date) -> bool | None:
        """True/False when coverage dates are known, None when the master row has none."""

        first = _parse_iso(self.firstpricedate)
        last = _parse_iso(self.lastpricedate)
        if first is None and last is None:
            return None
        if first is not None and session < first:
            return False
        if last is not None and session > last:
            return False
        return True


@dataclass(frozen=True)
class DailyPoolRow:
    security_id: str
    session_date: date
    in_raw_universe: bool
    in_strategy_pool: bool
    venue_tag: str
    closeunadj: float
    unadj_adv20: float | None
    category: str | None
    currency: str | None
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["session_date"] = self.session_date.isoformat()
        return payload


def _parse_iso(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def parse_bool(value: Any) -> bool | None:
    if value in (None, ""):
        return None
    text = str(value).strip().upper()
    if text in {"Y", "YES", "TRUE", "1"}:
        return True
    if text in {"N", "NO", "FALSE", "0"}:
        return False
    return None


def split_related(value: Any) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return tuple(part.strip() for part in str(value).replace(";", " ").replace(",", " ").split() if part.strip())


def share_class_from_category(category: str | None) -> str | None:
    text = category or ""
    if _SECONDARY_CLASS_RE.search(text):
        return "secondary"
    if _PRIMARY_CLASS_RE.search(text):
        return "primary"
    return None


def is_strategy_common_stock(category: str | None) -> bool:
    return bool(category and _COMMON_STOCK_RE.match(category.strip()))


def fund_kind(category: str | None) -> str | None:
    if not category:
        return None
    token = category.strip().upper()
    if token in FUND_CATEGORIES:
        return token
    for item in FUND_CATEGORIES:
        if item in token:
            return item
    return None


def venue_tag(exchange: str | None) -> str:
    """Normalise vendor venue spellings ('NASDAQ Global Select', 'NYSE Arca', 'BATS Global Markets')."""

    if not exchange or not str(exchange).strip():
        return "venue_unverified"
    token = re.sub(r"[^A-Z]", "", str(exchange).strip().upper())
    canonical: str | None = None
    if token.startswith("NYSEARCA") or token == "ARCA":
        canonical = "NYSEARCA"
    elif token.startswith("NYSEMKT") or token.startswith("NYSEAMERICAN") or token.startswith("AMEX"):
        canonical = "NYSEMKT"
    elif token.startswith("NYSE"):
        canonical = "NYSE"
    elif token.startswith("NASDAQ"):
        canonical = "NASDAQ"
    elif token.startswith("BATS") or token.startswith("CBOEBZX") or token == "BZX":
        canonical = "BATS"
    if canonical in VENUE_OK:
        return "venue_ok"
    return "venue_unverified"


def identity_from_ticker_row(row: Mapping[str, Any]) -> SharadarIdentity:
    permaticker = str(row.get("permaticker") or "").strip()
    if not permaticker:
        raise ValueError("tickers_row_missing_permaticker")
    ticker = str(row.get("ticker") or "").strip().upper()
    base, suffix = split_ticker_suffix(ticker)
    category = None if row.get("category") in (None, "") else str(row["category"])
    fund = fund_kind(category)
    if fund:
        asset_track = "etf" if fund == "ETF" else "fund"
        security_type = fund
    elif is_strategy_common_stock(category):
        asset_track = "stock"
        security_type = "common_stock"
    else:
        asset_track = "other"
        security_type = (category or "UNKNOWN").split()[-1] if category else "UNKNOWN"
    related = split_related(row.get("relatedtickers"))
    issuer_id = f"sharadar-issuer:{min((base, *related)) if base else permaticker}"
    flags = [
        VENUE_HISTORY_UNVERIFIED,
        PRICE_HISTORY_RECONSTRUCTED,
        CLASSIFICATION_CURRENT,
    ]
    if fund == "ETF":
        flags.append(ETF_SUBASSET_MAPPING_MANUAL)
    master_table = None if row.get("table") in (None, "") else str(row["table"])
    return SharadarIdentity(
        security_id=f"sharadar:{permaticker}",
        permaticker=permaticker,
        ticker=ticker,
        name=None if row.get("name") in (None, "") else str(row["name"]),
        share_class=share_class_from_category(category),
        category=category,
        currency=None if row.get("currency") in (None, "") else str(row["currency"]),
        exchange=None if row.get("exchange") in (None, "") else str(row["exchange"]),
        isdelisted=parse_bool(row.get("isdelisted")),
        relatedtickers=related,
        issuer_id=issuer_id,
        asset_track=asset_track,
        security_type=security_type,
        firstpricedate=None if row.get("firstpricedate") in (None, "") else str(row["firstpricedate"])[:10],
        lastpricedate=None if row.get("lastpricedate") in (None, "") else str(row["lastpricedate"])[:10],
        flags=tuple(flags),
        ticker_base=base,
        ticker_suffix=suffix,
        master_table=master_table,
    )


def resolve_identity_for_session(
    candidates: Sequence[SharadarIdentity],
    session: date,
) -> SharadarIdentity | None:
    """A reused ticker is several permatickers. Vendor coverage picks which one owns a date.

    A single candidate is still checked against its coverage window: a 2012 row
    cannot belong to a company whose first price date is 2018.
    """

    covering = []
    unknown = []
    for identity in candidates:
        state = identity.covers(session)
        if state is True:
            covering.append(identity)
        elif state is None:
            unknown.append(identity)
    if len(covering) == 1:
        return covering[0]
    if not covering and len(unknown) == 1 and len(candidates) == 1:
        return unknown[0]
    return None


def resolve_identity_for_event_year(
    candidates: Sequence[SharadarIdentity],
    year: int,
) -> tuple[SharadarIdentity | None, list[SharadarIdentity], str | None]:
    """Pick the identity whose coverage spans the event year. Returns (identity, covering, reason)."""

    covering = []
    for identity in candidates:
        first = _parse_iso(identity.firstpricedate)
        last = _parse_iso(identity.lastpricedate)
        if first is not None and first.year > year:
            continue
        if last is not None and last.year < year:
            continue
        covering.append(identity)
    if len(covering) == 1:
        return covering[0], covering, None
    if not candidates:
        return None, covering, "no_ticker_row"
    if not covering:
        return None, covering, "no_candidate_covers_event_year"
    # Prefer the delisted (suffixed) company when the live one also nominally covers.
    suffixed = [item for item in covering if item.ticker_suffix is not None or item.isdelisted is True]
    if len(suffixed) == 1:
        return suffixed[0], covering, None
    return None, covering, "ambiguous_candidates"


def unadj_adv20(
    tracks: Sequence[PriceTracks],
    *,
    session: date | None = None,
    max_gap_days: int = ADV20_MAX_GAP_DAYS,
) -> float | None:
    """Prior 20 complete sessions only. The caller must exclude T.

    The 20 prior rows must be recent: a security that resumed trading after a
    long halt does not get an ADV from rows years old.
    """

    recent = list(tracks[-20:])
    dollars = [item.raw_dollar_volume for item in recent if item.raw_dollar_volume is not None]
    if len(dollars) < 20:
        return None
    if session is not None:
        oldest = min(item.session_date for item in recent)
        if (session - oldest).days > max_gap_days:
            return None
    return float(sum(dollars) / 20.0)


def daily_pool_row(
    identity: SharadarIdentity,
    tracks: PriceTracks,
    history: Sequence[PriceTracks],
) -> DailyPoolRow:
    reasons: list[str] = []
    if not is_strategy_common_stock(identity.category):
        reasons.append("CATEGORY")
    if (identity.currency or "").upper() != "USD":
        reasons.append("CURRENCY")
    if tracks.closeunadj < CLOSEUNADJ_MIN:
        reasons.append("CLOSEUNADJ")
    prior = [item for item in history if item.session_date < tracks.session_date]
    adv = unadj_adv20(prior, session=tracks.session_date)
    if adv is None or adv < UNADJ_ADV20_MIN:
        reasons.append("UNADJ_ADV20")
    return DailyPoolRow(
        security_id=identity.security_id,
        session_date=tracks.session_date,
        in_raw_universe=True,
        in_strategy_pool=not reasons,
        venue_tag=venue_tag(identity.exchange),
        closeunadj=tracks.closeunadj,
        unadj_adv20=adv,
        category=identity.category,
        currency=identity.currency,
        reasons=tuple(reasons),
    )


def venue_unverified_report(rows: Iterable[DailyPoolRow], *, signal_ids: set[str] | None = None) -> dict[str, Any]:
    pool = [row for row in rows if row.in_strategy_pool]
    unverified = [row for row in pool if row.venue_tag == "venue_unverified"]
    signals = signal_ids or set()
    signal_unverified = [row for row in unverified if row.security_id in signals]
    return {
        "pool_n": len(pool),
        "venue_unverified_n": len(unverified),
        "venue_unverified_share": None if not pool else len(unverified) / len(pool),
        "signal_n": len(signals),
        "venue_unverified_signal_n": len(signal_unverified),
        "venue_unverified_signal_share": None if not signals else len(signal_unverified) / len(signals),
    }


def _cash_value(row: Mapping[str, Any]) -> float | None:
    raw = row.get("value")
    if raw in (None, ""):
        return None
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def classify_terminal(actions: Sequence[Mapping[str, Any]], *, last_trade: float | None = None) -> dict[str, Any]:
    """Terminal label for a holding window that ends in a delisting.

    Bankruptcy / liquidation -> last observed trade. Cash acquisition -> the cash
    consideration carried on the action row. Anything else -> TERMINAL_UNKNOWN
    (never zero). The action strings seen are returned as evidence.
    """

    rows = list(actions)
    cash_values: list[float] = []
    bankruptcy = False
    acquisition = False
    stock_deal = False
    seen: list[str] = []
    for row in rows:
        action = str(row.get("action") or "").strip().lower()
        if action and action not in seen:
            seen.append(action)
        blob = " ".join(str(row.get(key) or "") for key in ("action", "name", "contraname"))
        if action in ACTION_BANKRUPTCY or (action in ACTION_DELISTED and _BANKRUPTCY_RE.search(blob)):
            bankruptcy = True
            continue
        if action in ACTION_ACQUISITION_STOCK:
            acquisition = True
            stock_deal = True
            continue
        if action in ACTION_ACQUISITION_CASH or action in ACTION_ACQUISITION_ELECT_CASH or action.startswith("acquisition") or action.startswith("merger"):
            acquisition = True
            value = _cash_value(row)
            if value is not None and not stock_deal:
                cash_values.append(value)
    if bankruptcy:
        if last_trade is None:
            return {"label": TERMINAL_UNKNOWN, "reason": "bankruptcy_without_last_trade", "value": None, "actions_seen": seen}
        return {"label": TERMINAL_BANKRUPTCY, "reason": "last_trade", "value": last_trade, "actions_seen": seen}
    if acquisition:
        if cash_values and not stock_deal:
            return {"label": TERMINAL_ACQUISITION_CASH, "reason": "actions.cash_consideration", "value": cash_values[-1], "actions_seen": seen}
        return {
            "label": TERMINAL_UNKNOWN,
            "reason": "acquisition_stock_or_mixed" if stock_deal else "acquisition_without_cash",
            "value": None,
            "actions_seen": seen,
        }
    return {"label": TERMINAL_UNKNOWN, "reason": "no_settlement_action", "value": None, "actions_seen": seen}


def issuer_dedup_keys(identities: Sequence[SharadarIdentity]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for item in identities:
        groups.setdefault(item.issuer_id, []).append(item.security_id)
    return groups


def etf_subasset_schema() -> dict[str, Any]:
    return {
        "flag": ETF_SUBASSET_MAPPING_MANUAL,
        "sharadar_category_stops_at_fund_kind": True,
        "fund_kinds": sorted(FUND_CATEGORIES),
        "subassets": list(ETF_SUBASSETS),
        "members_stage": "not_this_round",
        "historical_themes_rebuilt": False,
    }


@dataclass
class IdentityFlags:
    flags: tuple[str, ...] = field(default_factory=tuple)


__all__ = [
    "DailyPoolRow",
    "IdentityFlags",
    "SharadarIdentity",
    "classify_terminal",
    "daily_pool_row",
    "etf_subasset_schema",
    "fund_kind",
    "identity_from_ticker_row",
    "is_strategy_common_stock",
    "issuer_dedup_keys",
    "parse_bool",
    "resolve_identity_for_event_year",
    "resolve_identity_for_session",
    "share_class_from_category",
    "split_related",
    "split_ticker_suffix",
    "unadj_adv20",
    "venue_tag",
    "venue_unverified_report",
]
