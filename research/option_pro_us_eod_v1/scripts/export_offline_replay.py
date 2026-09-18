"""Write a tiny LocalParquet export from the gitignored cache and hash the bytes."""

from __future__ import annotations

import pickle
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.research_eod_v1.data.contract import hash_file_bytes  # noqa: E402
from app.services.research_eod_v1.data.local_parquet import LocalParquetProvider  # noqa: E402

CACHE = ROOT / "research" / "option_pro_us_eod_v1" / "data" / "cache" / "round3_yahoo_abcd" / "bars.pkl"
OUT = ROOT / "research" / "option_pro_us_eod_v1" / "data" / "cache" / "offline_replay"


def main() -> int:
    if not CACHE.is_file():
        raise SystemExit("missing gitignored Yahoo cache; run run_round3_yahoo_abcd.py first")
    batched = pickle.loads(CACHE.read_bytes())
    rows = []
    for symbol in ("SPY", "NVDA"):
        for bar in (batched.get(symbol) or [])[:5]:
            rows.append(
                {
                    "security_id": symbol,
                    "session_date": bar.session_date.isoformat(),
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "raw_open": bar.raw_open,
                    "raw_close": bar.raw_close,
                    "volume": bar.volume,
                    "tri": bar.tri,
                    "vintage_status": bar.vintage_status,
                    "partial": bar.partial,
                    "halted": bar.halted,
                    "price_adjustment": bar.price_adjustment,
                    "volume_adjustment": bar.volume_adjustment,
                }
            )
    if not rows:
        raise SystemExit("cache has no SPY/NVDA bars")
    try:
        import pandas as pd
    except ImportError as exc:
        raise SystemExit(f"pandas required: {exc}") from exc
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "daily_bars.parquet"
    pd.DataFrame(rows).to_parquet(path, index=False)
    from datetime import date, timedelta

    provider = LocalParquetProvider(OUT)
    first = date.fromisoformat(rows[0]["session_date"])
    last = max(date.fromisoformat(row["session_date"]) for row in rows) + timedelta(days=1)
    spy = provider.fetch_daily_bars("SPY", first, last)
    digest = hash_file_bytes([path])
    print({"rows": len(spy), "sha256": digest, "path": str(path)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
