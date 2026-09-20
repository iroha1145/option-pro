"""Current-theme panel construction for packaged EOD scoring."""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping, Sequence

from app.services.research_eod_v1.data.contract import ResearchBar
from app.services.research_eod_v1.data.to_series import bars_to_series
from app.services.research_eod_v1.venue import CURRENT_UNIVERSE_VENUE_NOTES, classify_venue
from app.services.sectors import SECTORS

from . import RETURN_BASIS

ETF_SUBASSET_HINTS = {
    "SPY": "broad_equity",
    "QQQ": "broad_equity",
    "IWM": "broad_equity",
    "DIA": "broad_equity",
    "VTI": "broad_equity",
    "VOO": "broad_equity",
    "ARKK": "thematic_equity",
    "SOXX": "sector_equity",
    "XLF": "sector_equity",
    "XLE": "sector_equity",
    "GLD": "gold",
    "TLT": "long_bond",
}


def current_universe_tickers() -> dict[str, list[str]]:
    appearances: dict[str, list[str]] = {}
    for theme_id, sector in SECTORS.items():
        for ticker in sector["tickers"]:
            appearances.setdefault(str(ticker).upper(), []).append(theme_id)
    for extra in ("SPY", "QQQ"):
        appearances.setdefault(extra, appearances.get(extra, []))
    return appearances


def select_universe_tickers(tickers: Sequence[str] | None = None) -> dict[str, list[str]]:
    appearances = current_universe_tickers()
    if tickers is None:
        return appearances
    wanted = [str(item).upper() for item in tickers]
    return {key: appearances[key] for key in wanted if key in appearances}


def prepare_limited_panel(panel: Mapping[str, Any]) -> dict[str, Any]:
    prepared = {}
    for sid, series in panel.items():
        viewed = series.with_close_price_return() if hasattr(series, "with_close_price_return") else series
        viewed.industry_id = None
        viewed.parent_industry_id = None
        prepared[sid] = viewed
    return prepared


def _venue(track: str, ticker: str) -> dict[str, str]:
    metadata = {
        "listing_country": "US",
        "exchange": "NASDAQ",
        "mic": "XNAS",
        "security_type": "ETF" if track == "etf" else "CS",
        "identity_confidence": "unverified_default_not_checked",
        "industry_source": "none_without_independent_classification",
        "return_basis": RETURN_BASIS,
        "return_transform_version": "limited-v1-close-price-return-v1",
    }
    known = CURRENT_UNIVERSE_VENUE_NOTES.get(ticker)
    if known:
        metadata.update(known)
        metadata["mic"] = ""
        metadata["identity_confidence"] = "current_universe_venue_notes"
    return metadata


def bars_to_panel(
    bars: Mapping[str, Sequence[ResearchBar]],
    appearances: Mapping[str, Sequence[str]],
    *,
    end: date | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    panel: dict[str, Any] = {}
    coverage: list[dict[str, Any]] = []
    for ticker, themes in appearances.items():
        track = "etf" if ticker in ETF_SUBASSET_HINTS or list(themes) == ["etfs"] else "stock"
        venue = _venue(track, ticker)
        decision = classify_venue(venue)
        if not decision.eligible:
            coverage.append({"ticker": ticker, "themes": list(themes), "bars": 0, "status": f"excluded:{decision.reason}"})
            continue
        usable = [
            bar
            for bar in (bars.get(ticker) or [])
            if end is None or bar.session_date <= end
        ]
        row = {"ticker": ticker, "themes": list(themes), "bars": len(usable), "status": "ok" if usable else "empty"}
        if not usable:
            coverage.append(row)
            continue
        try:
            series = bars_to_series(
                usable,
                security_id=ticker,
                asset_track=track,
                theme_ids=tuple(themes) or (("etfs",) if track == "etf" else ()),
                industry_id=None,
                parent_industry_id=None,
                venue_metadata=venue,
            )
        except ValueError as exc:
            row["status"] = f"invalid:{exc}"
            coverage.append(row)
            continue
        if series is None:
            row["status"] = "empty"
            coverage.append(row)
            continue
        panel[ticker] = series.with_close_price_return()
        coverage.append(row)
    return panel, coverage
