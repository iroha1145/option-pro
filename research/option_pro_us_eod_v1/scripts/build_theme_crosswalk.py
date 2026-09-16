"""Write current-universe theme membership tables. Not PIT history."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.research_eod_v1.universe_audit import ETF_SUBASSET_HINTS, current_universe_audit  # noqa: E402
from app.services.research_eod_v1.venue import CURRENT_UNIVERSE_VENUE_NOTES, classify_venue  # noqa: E402
from app.services.sectors import SECTORS  # noqa: E402


def main() -> int:
    pack = Path(__file__).resolve().parents[1] / "return_pack"
    pack.mkdir(parents=True, exist_ok=True)
    crosswalk = pack / "theme_membership_crosswalk.csv"
    chars = pack / "theme_characteristics.csv"
    audit = current_universe_audit()
    with crosswalk.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            lineterminator="\n",
            fieldnames=[
                "theme_id",
                "ticker",
                "mapping_source",
                "parent_group",
                "asset_track",
                "venue_note",
                "pit_status",
            ],
        )
        writer.writeheader()
        for theme_id, sector in SECTORS.items():
            track = "etf" if theme_id == "etfs" else "stock"
            for ticker in sector["tickers"]:
                note = CURRENT_UNIVERSE_VENUE_NOTES.get(ticker, {})
                writer.writerow(
                    {
                        "theme_id": theme_id,
                        "ticker": ticker,
                        "mapping_source": "production_SECTORS_current_static",
                        "parent_group": str(sector["name"]).strip(),
                        "asset_track": ETF_SUBASSET_HINTS.get(ticker, track),
                        "venue_note": note.get("decision") or "none",
                        "pit_status": "CLASSIFICATION_CURRENT",
                    }
                )
    with chars.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            lineterminator="\n",
            fieldnames=[
                "theme_id",
                "n_members",
                "label_window",
                "label_mature_cutoff",
                "lambda",
                "prior_status",
                "independent_events",
                "evaluable_years",
            ],
        )
        writer.writeheader()
        for theme_id, sector in SECTORS.items():
            writer.writerow(
                {
                    "theme_id": theme_id,
                    "n_members": len(sector["tickers"]),
                    "label_window": "20/63 registered",
                    "label_mature_cutoff": "not_run_on_market_history",
                    "lambda": "n/(n+30) industry shrink; theme lambda<=0.5 reserved",
                    "prior_status": "REGISTERED_NOT_RUN",
                    "independent_events": "na",
                    "evaluable_years": "na",
                }
            )
    print({"themes": len(SECTORS), "overlaps": len(audit["overlap_tickers"])})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
