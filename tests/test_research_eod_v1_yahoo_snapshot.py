from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.services.research_eod_v1.paths import RETURN_PACK_DIR

SNAP = RETURN_PACK_DIR / "yahoo_current_universe"


def test_yahoo_current_universe_snapshot_is_in_the_return_pack() -> None:
    bars = pd.read_parquet(SNAP / "daily_bars.parquet")
    master = pd.read_csv(SNAP / "security_master.csv")
    rows = pd.read_parquet(SNAP / "theme_snapshot_rows.parquet")
    assert len(master) == 214
    assert bars["security_id"].nunique() == 214
    assert int((master["bars"] == 0).sum()) == 0
    assert bars["session_date"].max() == "2026-09-16"
    assert set(rows["theme_id"]) and rows["theme_id"].nunique() == 24
    assert Path(SNAP / "manifest.json").is_file()
