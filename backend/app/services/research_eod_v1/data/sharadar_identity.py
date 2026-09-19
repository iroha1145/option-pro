"""Security identity, daily pool tags, and holding-window terminal labels."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Iterable, Mapping, Sequence

from app.services.research_eod_v1.data.sharadar_schema import (
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

_COMMON_STOCK_RE = re.compile(
    r"^(domestic|adr)\b.*\bcommon stock\b",
    re.IGNORECASE,
)
_PRIMARY_CLASS_RE = re.compile(r"primary class", re.IGNORECASE)
_SECONDARY_CLASS_RE = re.compile(r"secondary class", re.IGNORECASE)
_BANKRUPTCY_RE = re.compile(
    r"bankrupt|chapter\s*11|chapter\s*7|liquidation|cancelled|fdic|receivership",
    re.IGNORECASE,
)
_ACQUISITION_ACTIONS = frozenset({
    "acquisitioncash",
    "acquisitionstock",
    "acquisitionelectcash",
    "acquisitionelectstock",
    "acquired",
    "merger",
})


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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
    return tuple(part.strip() for part in str(value).replace(";", " ").split() if part.strip())


def share_class_from_category(category: str | None) -> str | None:
    text = category or ""
    if _SECONDARY_CLASS_RE.search(text):
        return "secondary"
    if _PRIMARY_CLASS_RE.search(text):
        return "primary"
    return None


def is_strategy_common_stock(category: str | None) -> bool:
    return bool(category and _COMMON_STOCK_RE.search(category.strip()))


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
    if not exchange or not str(exchange).strip():
        return "venue_unverified"
    token = str(exchange).strip().upper().replace(" ", "")
    aliases = {
        "NYSE": "NYSE",
        "NASDAQ": "NASDAQ",
        "NYSEMKT": "NYSEMKT",
        "AMEX": "NYSEMKT",
        "NYSEAMERICAN": "NYSEMKT",
        "NYSEARCA": "NYSEARCA",
        "ARCA": "NYSEARCA",
        "BATS": "BATS",
        "BZX": "BATS",
    }
    canonical = aliases.get(token, token)
    if canonical in VENUE_OK:
        return "venue_ok"
    return "venue_unverified"


def identity_from_ticker_row(row: Mapping[str, Any]) -> SharadarIdentity:
    permaticker = str(row.get("permaticker") or "").strip()
    if not permaticker:
        raise ValueError("tickers_row_missing_permaticker")
    ticker = str(row.get("ticker") or "").strip()
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
    issuer_id = f"sharadar-issuer:{min((ticker, *related)) if ticker else permaticker}"
    flags = [
        VENUE_HISTORY_UNVERIFIED,
        PRICE_HISTORY_RECONSTRUCTED,
        CLASSIFICATION_CURRENT,
    ]
    if fund == "ETF":
        flags.append(ETF_SUBASSET_MAPPING_MANUAL)
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
    )


def unadj_adv20(tracks: Sequence[PriceTracks]) -> float | None:
    """Prior 20 complete sessions only. The caller must exclude T."""

    dollars = [item.raw_dollar_volume for item in tracks[-20:] if item.raw_dollar_volume is not None]
    if len(dollars) < 20:
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
    adv = unadj_adv20(prior)
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


def classify_terminal(actions: Sequence[Mapping[str, Any]], *, last_trade: float | None = None) -> dict[str, Any]:
    rows = list(actions)
    cash_values = []
    bankruptcy = False
    acquisition = False
    for row in rows:
        action = str(row.get("action") or "").strip().lower()
        blob = " ".join(str(row.get(key) or "") for key in ("action", "name", "contraname"))
        if action in _ACQUISITION_ACTIONS or "acquisition" in action:
            acquisition = True
            if action == "acquisitioncash" and row.get("value") not in (None, ""):
                try:
                    cash_values.append(float(row["value"]))
                except (TypeError, ValueError):
                    pass
        if action == "delisted" and _BANKRUPTCY_RE.search(blob):
            bankruptcy = True
        if _BANKRUPTCY_RE.search(blob) and action in {"delisted", "bankruptcy"}:
            bankruptcy = True
    if bankruptcy:
        if last_trade is None:
            return {"label": TERMINAL_UNKNOWN, "reason": "bankruptcy_without_last_trade", "value": None}
        return {"label": TERMINAL_BANKRUPTCY, "reason": "last_trade", "value": last_trade}
    if acquisition:
        if cash_values:
            return {"label": TERMINAL_ACQUISITION_CASH, "reason": "actions.acquisitioncash", "value": cash_values[-1]}
        return {"label": TERMINAL_UNKNOWN, "reason": "acquisition_without_cash", "value": None}
    return {"label": TERMINAL_UNKNOWN, "reason": "no_settlement_action", "value": None}


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
