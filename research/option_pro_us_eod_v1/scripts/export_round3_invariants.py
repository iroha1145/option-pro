"""Commit-sized evidence for F3 residual, F4 ledger, F1 calendar, and F2 events.

No network. Reads already-committed diagnostic JSON plus synthetic fixtures.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.research_eod_v1.calendar_asof import last_complete_eod_session, session_close_at  # noqa: E402
from app.services.research_eod_v1.fixtures import make_series, trading_days  # noqa: E402
from app.services.research_eod_v1.ledger import simulate_ledger  # noqa: E402
from app.services.research_eod_v1.residual import residual_raw_momentum  # noqa: E402

ET = ZoneInfo("America/New_York")
OUT = ROOT / "research" / "option_pro_us_eod_v1" / "return_pack" / "round3_invariants.json"
ABCD = ROOT / "research" / "option_pro_us_eod_v1" / "return_pack" / "round3_yahoo_abcd.json"
RECAPTURE = ROOT / "research" / "option_pro_us_eod_v1" / "return_pack" / "round3_after_close_capture.json"
MANIFEST = ROOT / "research" / "option_pro_us_eod_v1" / "return_pack" / "yahoo_current_universe" / "manifest.json"


def _residual_seed_174() -> dict:
    rng = np.random.default_rng(174)
    days = trading_days(date(2018, 1, 2), 700)
    rm = rng.normal(0.0003, 0.01, 700)
    pm = 100 * np.cumprod(1 + rm)
    ri = 1.3 * rm[-400:] + rng.normal(0.0004, 0.003, 400)
    pi = 30 * np.cumprod(1 + ri)
    stock = make_series("NEWER", days[-400:], pi, industry_id=None)
    full = make_series("SPY", days, pm, asset_track="etf", security_type="ETF")
    trimmed = make_series("SPY", days[-400:], pm[-400:], asset_track="etf", security_type="ETF")
    with_prefix = residual_raw_momentum(stock, full, {"NEWER": stock, "SPY": full})
    without = residual_raw_momentum(stock, trimmed, {"NEWER": stock, "SPY": trimmed})
    return {
        "status": with_prefix.status,
        "raw_with_unused_spy_prefix": with_prefix.raw,
        "raw_trimmed": without.raw,
        "identical": bool(with_prefix.status == without.status == "OK" and with_prefix.raw == without.raw),
    }


def _ledger_split_and_unknown_mark() -> dict:
    days = trading_days(date(2021, 1, 4), 12)

    def _flat(sid: str, price: float = 50.0):
        series = make_series(sid, days, np.full(12, price))
        series.open = np.full(12, price)
        series.raw_open = np.full(12, price)
        series.raw_close = np.full(12, price)
        return series

    split = _flat("SPL", 50.0)
    split.raw_open[:6] = 100.0
    split.raw_close[:6] = 100.0
    split.splits = ((days[6], 2.0),)
    split_result = simulate_ledger(
        start=days[0],
        end=days[-1],
        panel={"SPL": split},
        capital=10_000.0,
        holding_sessions=5,
        cost_multiple=0,
        signals=[{"security_id": "SPL", "session_date": days[1].isoformat(), "status": "eligible", "score": 90, "adv20": 80_000_000, "notional": 5000.0}],
    )
    a, b = _flat("AAA"), _flat("BBB")
    a.close[4] = np.nan
    a.raw_close[4] = np.nan
    unknown = simulate_ledger(
        start=days[0],
        end=days[-1],
        panel={"AAA": a, "BBB": b},
        capital=10_000.0,
        holding_sessions=20,
        cost_multiple=0,
        signals=[
            {"security_id": "AAA", "session_date": days[1].isoformat(), "status": "eligible", "score": 90, "adv20": 80_000_000, "notional": 2000.0},
            {"security_id": "BBB", "session_date": days[1].isoformat(), "status": "eligible", "score": 90, "adv20": 80_000_000, "notional": 2000.0},
        ],
    )
    row = next(item for item in unknown["daily_equity"] if item["session"] == days[4].isoformat())
    return {
        "split_ending_equity": split_result["ending_equity"],
        "split_net_return": split_result["trades"][0]["net_return"] if split_result["trades"] else None,
        "unknown_mark_equity_is_null": row.get("equity") is None,
        "unknown_mark_known_positions_value": row.get("known_positions_value"),
        "unknown_mark_partial_value": row.get("partial_value"),
    }


def _platform_samples() -> list[dict]:
    if not ABCD.is_file():
        return []
    payload = json.loads(ABCD.read_text(encoding="utf-8"))
    samples = []
    for card in payload.get("cards") or []:
        for row in card.get("eligible_rows") or []:
            samples.append(
                {
                    "theme_id": card.get("theme_id"),
                    "algorithm": card.get("algorithm"),
                    "session_date": card.get("session_date"),
                    "security_id": row.get("security_id"),
                    "platform_setup_id": row.get("platform_setup_id"),
                    "platform_lifecycle": row.get("platform_lifecycle"),
                    "platform_events": row.get("platform_events"),
                    "residual_status": row.get("residual_status"),
                    "residual_raw": row.get("residual_raw"),
                    "note": "signal diagnostic only; not a fill or winner",
                }
            )
    return samples


def main() -> int:
    recapture = json.loads(RECAPTURE.read_text(encoding="utf-8")) if RECAPTURE.is_file() else {}
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.is_file() else {}
    report = {
        "head_note": "round-3 invariant samples; not market PnL",
        "calendar": {
            "july3_2024_1259": last_complete_eod_session(datetime(2024, 7, 3, 12, 59, tzinfo=ET)).isoformat(),
            "july3_2024_1300": last_complete_eod_session(datetime(2024, 7, 3, 13, 0, tzinfo=ET)).isoformat(),
            "labor_day_2026_1000": last_complete_eod_session(datetime(2026, 9, 7, 10, 0, tzinfo=ET)).isoformat(),
            "dst_close_offsets": {
                "2026-03-06": str(session_close_at(date(2026, 3, 6)).utcoffset()),
                "2026-03-09": str(session_close_at(date(2026, 3, 9)).utcoffset()),
            },
        },
        "residual_seed_174": _residual_seed_174(),
        "ledger": _ledger_split_and_unknown_mark(),
        "yahoo_platform_and_residual_samples": _platform_samples(),
        "after_close_recapture": {
            "capture_clock": recapture.get("capture_clock"),
            "eod_status": recapture.get("eod_status"),
            "last_complete_eod_session": recapture.get("last_complete_eod_session"),
            "old_manifest_retrieved_at": recapture.get("old_manifest_retrieved_at"),
            "old_manifest_retrieved_at_unchanged": recapture.get("old_manifest_retrieved_at_unchanged"),
            "isolated_unfinalized_or_partial_bars": recapture.get("isolated_unfinalized_or_partial_bars"),
            "cache_sha256": recapture.get("cache_sha256"),
        },
        "invalid_eod_manifest": {
            "retrieved_at": manifest.get("retrieved_at"),
            "eod_status": manifest.get("eod_status"),
            "last_complete_eod_session": manifest.get("last_complete_eod_session"),
        },
        "executed_backtests": 0,
        "winners": [],
    }
    OUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(OUT), "residual_identical": report["residual_seed_174"]["identical"], "samples": len(report["yahoo_platform_and_residual_samples"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
