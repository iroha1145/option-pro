"""Controlled A/B/C/D diagnostics on an allowed complete session. Not a market backtest."""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.research_eod_v1.calendar_asof import last_complete_eod_session  # noqa: E402
from app.services.research_eod_v1.config_load import load_registry  # noqa: E402
from app.services.research_eod_v1.constants import ALGORITHMS  # noqa: E402
from app.services.research_eod_v1.fixtures import make_series, trading_days, trending_close  # noqa: E402
from app.services.research_eod_v1.snapshot import compute_snapshot  # noqa: E402
from app.services.sectors import SECTORS  # noqa: E402

ET = ZoneInfo("America/New_York")
CLOCK = datetime(2026, 9, 16, 13, 24, 41, tzinfo=ET)
ALLOWED = date(2026, 9, 15)


def _panel_for(session: date) -> dict:
    days = [day for day in trading_days(date(2018, 1, 2), 2300) if day <= session][-420:]
    if not days or days[-1] != session:
        raise SystemExit(f"synthetic calendar does not end on {session}: last={days[-1] if days else None}")
    panel = {}
    for i, ticker in enumerate(["NVDA", "AMD", "AVGO", "MSFT", "AAPL", "XOM", "JPM", "UNH"]):
        panel[ticker] = make_series(
            ticker,
            days,
            trending_close(len(days), 40 + i, 0.08 + i * 0.005),
            theme_ids=tuple(theme for theme, sector in SECTORS.items() if ticker in sector.get("tickers", [])) or ("semiconductors",),
        )
    panel["SPY"] = make_series("SPY", days, trending_close(len(days), 200, 0.05), asset_track="etf", security_type="ETF", theme_ids=("etfs",))
    panel["QQQ"] = make_series("QQQ", days, trending_close(len(days), 180, 0.06), asset_track="etf", security_type="ETF", theme_ids=("etfs",))
    return panel


def main() -> int:
    session = last_complete_eod_session(CLOCK)
    if session > ALLOWED:
        raise SystemExit(f"clock {CLOCK.isoformat()} produced session {session} later than {ALLOWED}")
    registry = load_registry()
    panel = _panel_for(session)
    as_of = CLOCK
    themes = ["semiconductors", "software", "energy", "etfs"]
    cards = []
    executed = 0
    for theme_id in themes:
        if theme_id not in registry["sectors"]:
            continue
        for algorithm in ALGORITHMS:
            payload = compute_snapshot(
                as_of,
                panel,
                "u_round3_engineering_fixture",
                registry,
                sector_id=theme_id,
                algorithm=algorithm,
                profile="balanced",
                horizon="mid",
            )
            executed += 1
            eligible = [row for row in payload["rows"] if row.get("status") == "eligible"]
            cards.append(
                {
                    "theme_id": theme_id,
                    "algorithm": algorithm,
                    "session_date": payload["session_date"],
                    "candidates": len(payload["candidate_ids"]),
                    "reference": len(payload["reference_ids"]),
                    "eligible": len(eligible),
                    "capability_flags": [
                        "ENGINEERING_ONLY",
                        "CURRENT_UNIVERSE_DIAGNOSTIC",
                        "EXECUTION_DATA_UNVERIFIED",
                    ],
                    "status": "ENGINEERING_ONLY",
                }
            )
    out = {
        "head_note": "round3 engineering diagnostic; synthetic bars; not market PnL",
        "capture_clock": CLOCK.isoformat(),
        "last_complete_eod_session": session.isoformat(),
        "registered": 1920,
        "executed_snapshots": executed,
        "executed_backtests": 0,
        "outcomes_inspected": executed,
        "revoked_invalid_eod_captures": 24,
        "market_backtest_run": False,
        "cards": cards,
    }
    dest = Path(__file__).resolve().parents[1] / "return_pack" / "round3_diagnostic.json"
    dest.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"session": session.isoformat(), "executed_snapshots": executed, "out": str(dest)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
