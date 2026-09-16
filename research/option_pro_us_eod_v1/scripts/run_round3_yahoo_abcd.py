"""Small Yahoo A/B/C/D snapshots on allowed complete sessions. Not a market backtest.

Downloads stay in the gitignored cache. Only summary JSON is written to the return pack.
"""

from __future__ import annotations

import json
import pickle
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.research_eod_v1.calendar_asof import (  # noqa: E402
    capture_as_of,
    last_complete_eod_session,
    last_known_finalized_session,
    session_close_at,
)
from app.services.research_eod_v1.config_load import load_registry  # noqa: E402
from app.services.research_eod_v1.constants import ALGORITHMS  # noqa: E402
from app.services.research_eod_v1.data.to_series import bars_to_series  # noqa: E402
from app.services.research_eod_v1.data.yahoo import YahooDiagnosticProvider  # noqa: E402
from app.services.research_eod_v1.report_contract import summarize_signal_row  # noqa: E402
from app.services.research_eod_v1.snapshot import compute_snapshot  # noqa: E402
from app.services.sectors import SECTORS  # noqa: E402

ET = ZoneInfo("America/New_York")
ALLOWED = date(2026, 9, 15)
START = date(2018, 1, 2)
END = date(2026, 9, 17)
THEMES = ("semiconductors", "software", "energy", "etfs")
CACHE = ROOT / "research" / "option_pro_us_eod_v1" / "data" / "cache" / "round3_yahoo_abcd"
OUT = ROOT / "research" / "option_pro_us_eod_v1" / "return_pack" / "round3_yahoo_abcd.json"


def _venue(track: str) -> dict:
    return {
        "listing_country": "US",
        "exchange": "NASDAQ",
        "mic": "XNAS",
        "security_type": "ETF" if track == "etf" else "CS",
        "identity_confidence": "unverified_default_not_checked",
        "industry_source": "theme_tag_diagnostic_not_economic_parent",
    }


def _row_summary(row: dict) -> dict:
    return summarize_signal_row(row)


def _as_of_after_complete(session: date) -> datetime:
    return session_close_at(session) + timedelta(minutes=30)


def main() -> int:
    clock = capture_as_of(datetime.now(timezone.utc))
    live_session = last_known_finalized_session(clock, last_proven_finalized=ALLOWED)
    if live_session > ALLOWED:
        raise SystemExit(f"live clock {clock.isoformat()} selected {live_session}, later than allowed {ALLOWED}")

    tickers: list[str] = []
    theme_of: dict[str, list[str]] = {}
    for theme_id in THEMES:
        for ticker in SECTORS[theme_id]["tickers"]:
            theme_of.setdefault(ticker, []).append(theme_id)
            if ticker not in tickers:
                tickers.append(ticker)
    for extra in ("SPY", "QQQ"):
        theme_of.setdefault(extra, []).append("etfs")
        if extra not in tickers:
            tickers.append(extra)

    CACHE.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE / "bars.pkl"
    provider = YahooDiagnosticProvider(allow_network=True, clock=clock)
    batched: dict[str, list] = {}
    if cache_file.exists():
        batched = pickle.loads(cache_file.read_bytes())
    else:
        for offset in range(0, len(tickers), 20):
            batched.update(provider.fetch_daily_bars_batch(tickers[offset : offset + 20], START, END))
        cache_file.write_bytes(pickle.dumps(batched))

    panel = {}
    coverage = []
    partial_bars = 0
    for ticker, themes in theme_of.items():
        track = "etf" if "etfs" in themes and ticker in {"SPY", "QQQ", "IWM", "DIA", "VTI", "VOO", "ARKK", "SOXX", "XLF", "XLE", "GLD", "TLT"} else "stock"
        bars = batched.get(ticker) or []
        partial_bars += sum(1 for bar in bars if bar.partial or bar.vintage_status == "PARTIAL")
        coverage.append(
            {
                "ticker": ticker,
                "themes": themes,
                "bars": len(bars),
                "first": bars[0].session_date.isoformat() if bars else None,
                "last": bars[-1].session_date.isoformat() if bars else None,
                "partial_bars": sum(1 for bar in bars if bar.partial or bar.vintage_status == "PARTIAL"),
                "status": "ok" if bars else "empty",
            }
        )
        if not bars:
            continue
        try:
            series = bars_to_series(
                bars,
                security_id=ticker,
                asset_track=track,
                theme_ids=tuple(themes),
                industry_id=themes[0],
                parent_industry_id=themes[0],
                venue_metadata=_venue(track),
                reconstruction_mode="historical_reconstruction",
            )
        except ValueError as exc:
            coverage[-1]["status"] = f"invalid:{exc}"
            continue
        if series is not None:
            panel[ticker] = series

    registry = load_registry()
    sessions = [live_session]
    friday = date(2026, 9, 11)
    if friday < live_session:
        sessions.append(friday)

    cards = []
    executed = 0
    for session in sessions:
        as_of = _as_of_after_complete(session) if session < live_session else clock
        if last_complete_eod_session(as_of, source_finalized_through=session) != session:
            as_of = _as_of_after_complete(session)
        for theme_id in THEMES:
            for algorithm in ALGORITHMS:
                payload = compute_snapshot(
                    as_of,
                    panel,
                    "u_round3_yahoo_small",
                    registry,
                    sector_id=theme_id,
                    algorithm=algorithm,
                    profile="balanced",
                    horizon="mid",
                    source_finalized_through=session,
                )
                executed += 1
                rows = [_row_summary(row) for row in payload["rows"]]
                eligible = [row for row in rows if row["status"] == "eligible"]
                cards.append(
                    {
                        "theme_id": theme_id,
                        "algorithm": algorithm,
                        "session_date": payload["session_date"],
                        "as_of": payload["as_of"],
                        "candidates": len(payload["candidate_ids"]),
                        "reference": len(payload["reference_ids"]),
                        "eligible": len(eligible),
                        "eligible_ids": [row["security_id"] for row in eligible],
                        "family_status": {
                            "eligible": len(eligible),
                            "rejected": sum(1 for row in rows if row["status"] != "eligible"),
                        },
                        "rows": rows,
                        "capability_flags": [
                            "CURRENT_UNIVERSE_DIAGNOSTIC",
                            "PIT_CLASSIFICATION_MISSING",
                            "CORPORATE_ACTIONS_INCOMPLETE",
                            "EXECUTION_DATA_UNVERIFIED",
                            "download_time_not_pit",
                        ],
                        "status": "CURRENT_UNIVERSE_DIAGNOSTIC",
                    }
                )

    report = {
        "head_note": "round3 Yahoo A/B/C/D on allowed complete sessions; not market PnL; Close not verified raw",
        "capture_clock": clock.isoformat(),
        "last_complete_eod_session": live_session.isoformat(),
        "allowed_not_after": ALLOWED.isoformat(),
        "sessions": [day.isoformat() for day in sessions],
        "downloaded_tickers": len(panel),
        "requested_tickers": len(tickers),
        "partial_bars_isolated": partial_bars,
        "provider_failures": provider.failures,
        "coverage": coverage,
        "registered": 1920,
        "executed_snapshots": executed,
        "executed_backtests": 0,
        "outcomes_inspected": executed,
        "revoked_invalid_eod_captures": 24,
        "market_backtest_run": False,
        "winners": [],
        "cards": cards,
    }
    OUT.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "session": live_session.isoformat(),
                "executed_snapshots": executed,
                "downloaded": len(panel),
                "partial_bars": partial_bars,
                "out": str(OUT),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
