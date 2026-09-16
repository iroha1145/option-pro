"""24-theme A/B/C/D cards on one allowed complete session. Not a market backtest.

Reuses the gitignored Yahoo cache, downloads only missing current-universe names,
and writes compact per-theme family counts. Large bars stay offline.
"""

from __future__ import annotations

import json
import pickle
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.research_eod_v1.calendar_asof import (  # noqa: E402
    capture_as_of,
    last_complete_eod_session,
    session_close_at,
)
from app.services.research_eod_v1.config_load import load_registry  # noqa: E402
from app.services.research_eod_v1.constants import ALGORITHMS  # noqa: E402
from app.services.research_eod_v1.data.to_series import bars_to_series  # noqa: E402
from app.services.research_eod_v1.data.yahoo import YahooDiagnosticProvider  # noqa: E402
from app.services.research_eod_v1.snapshot import compute_snapshot  # noqa: E402
from app.services.research_eod_v1.universe_audit import ETF_SUBASSET_HINTS  # noqa: E402
from app.services.sectors import SECTORS  # noqa: E402

ET = ZoneInfo("America/New_York")
ALLOWED = date(2026, 9, 15)
START = date(2018, 1, 2)
END = date(2026, 9, 17)
CACHE = ROOT / "research" / "option_pro_us_eod_v1" / "data" / "cache" / "round3_yahoo_abcd"
OUT = ROOT / "research" / "option_pro_us_eod_v1" / "return_pack" / "round3_theme_cards.json"


def _venue(track: str) -> dict:
    return {
        "listing_country": "US",
        "exchange": "NASDAQ",
        "mic": "XNAS",
        "security_type": "ETF" if track == "etf" else "CS",
        "identity_confidence": "unverified_default_not_checked",
        "industry_source": "theme_tag_diagnostic_not_economic_parent",
    }


def main() -> int:
    clock = capture_as_of(datetime.now(timezone.utc))
    session = last_complete_eod_session(clock)
    if session > ALLOWED:
        raise SystemExit(f"clock {clock.isoformat()} selected {session}, later than {ALLOWED}")
    as_of = clock if last_complete_eod_session(clock) == session else session_close_at(session) + timedelta(minutes=30)

    appearances: dict[str, list[str]] = {}
    for theme_id, sector in SECTORS.items():
        for ticker in sector["tickers"]:
            appearances.setdefault(ticker, []).append(theme_id)
    tickers = list(appearances)
    CACHE.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE / "bars.pkl"
    batched: dict[str, list] = pickle.loads(cache_file.read_bytes()) if cache_file.exists() else {}
    missing = [ticker for ticker in tickers if ticker not in batched or not batched[ticker]]
    provider = YahooDiagnosticProvider(allow_network=True, clock=clock)
    for offset in range(0, len(missing), 20):
        batched.update(provider.fetch_daily_bars_batch(missing[offset : offset + 20], START, END))
    cache_file.write_bytes(pickle.dumps(batched))

    panel = {}
    coverage = []
    partial_bars = 0
    for ticker, themes in appearances.items():
        track = "etf" if ticker in ETF_SUBASSET_HINTS or themes == ["etfs"] else "stock"
        bars = batched.get(ticker) or []
        partial_bars += sum(1 for bar in bars if bar.partial or bar.vintage_status == "PARTIAL")
        coverage.append({"ticker": ticker, "themes": themes, "bars": len(bars), "status": "ok" if bars else "empty"})
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
            )
        except ValueError as exc:
            coverage[-1]["status"] = f"invalid:{exc}"
            continue
        if series is not None:
            panel[ticker] = series

    registry = load_registry()
    cards = []
    executed = 0
    for theme_id in SECTORS:
        members = set(SECTORS[theme_id]["tickers"]) | {"SPY", "QQQ"}
        theme_panel = {sid: series for sid, series in panel.items() if sid in members}
        family = {}
        for algorithm in ALGORITHMS:
            payload = compute_snapshot(
                as_of,
                theme_panel,
                "u_round3_yahoo_24theme",
                registry,
                sector_id=theme_id,
                algorithm=algorithm,
                profile="balanced",
                horizon="mid",
                source_finalized_through=session,
            )
            executed += 1
            reasons = Counter()
            eligible_ids = []
            for row in payload["rows"]:
                for reason in row.get("rejection_reasons") or ():
                    reasons[str(reason)] += 1
                if row.get("status") == "eligible":
                    eligible_ids.append(row["security_id"])
            family[algorithm] = {
                "candidates": len(payload["candidate_ids"]),
                "reference": len(payload["reference_ids"]),
                "eligible": len(eligible_ids),
                "eligible_ids": eligible_ids,
                "top_rejections": reasons.most_common(5),
            }
        cards.append(
            {
                "theme_id": theme_id,
                "session_date": session.isoformat(),
                "status": "CURRENT_UNIVERSE_DIAGNOSTIC",
                "families": family,
                "capability_flags": [
                    "CURRENT_UNIVERSE_DIAGNOSTIC",
                    "PIT_CLASSIFICATION_MISSING",
                    "CORPORATE_ACTIONS_INCOMPLETE",
                    "EXECUTION_DATA_UNVERIFIED",
                    "download_time_not_pit",
                ],
                "limitations": [
                    "current survivor list only",
                    "theme[0] is not an economic parent",
                    "Yahoo Close is not verified raw",
                    "not a 10-year PIT backtest",
                ],
            }
        )

    report = {
        "head_note": "24-theme A/B/C/D on one allowed complete session; signal diagnostics only",
        "capture_clock": clock.isoformat(),
        "last_complete_eod_session": session.isoformat(),
        "downloaded_tickers": len(panel),
        "requested_tickers": len(tickers),
        "partial_bars_isolated": partial_bars,
        "provider_failures": provider.failures,
        "empty_tickers": [row["ticker"] for row in coverage if row["status"] != "ok"],
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
    print(json.dumps({"session": session.isoformat(), "executed_snapshots": executed, "downloaded": len(panel), "out": str(OUT)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
