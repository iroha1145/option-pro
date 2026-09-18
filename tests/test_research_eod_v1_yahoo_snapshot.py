from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from app.services.research_eod_v1.data.contract import hash_file_bytes
from app.services.research_eod_v1.data.local_parquet import LocalParquetProvider
from app.services.research_eod_v1.paths import RETURN_PACK_DIR

SNAP = RETURN_PACK_DIR / "yahoo_current_universe"
FIXTURE = RETURN_PACK_DIR / "fixtures" / "synthetic_daily_bars.parquet"


def test_invalid_eod_capture_is_flagged_and_large_tape_is_offline() -> None:
    manifest = json.loads((SNAP / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["eod_status"] == "INVALID_EOD_CAPTURE"
    assert "INVALID_EOD_CAPTURE" in manifest.get("notes", [])
    assert not (SNAP / "daily_bars.parquet").is_file()


def test_synthetic_parquet_roundtrip_and_byte_hash(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    frame = pd.DataFrame(
        {
            "security_id": ["AAA", "AAA", "SPY", "SPY"],
            "session_date": ["2024-01-02", "2024-01-03", "2024-01-02", "2024-01-03"],
            "open": [10.0, 10.5, 400.0, 401.0],
            "high": [11.0, 11.0, 402.0, 403.0],
            "low": [9.5, 10.0, 399.0, 400.0],
            "close": [10.5, 10.8, 401.0, 402.0],
            "raw_open": [10.0, 10.5, 400.0, 401.0],
            "raw_close": [10.5, 10.8, 401.0, 402.0],
            "volume": [1000.0, 1100.0, 1_000_000.0, 1_100_000.0],
            "dollar_volume": [10500.0, 11880.0, 401_000_000.0, 442_200_000.0],
            "tri": [10.5, 10.8, 401.0, 402.0],
        }
    )
    combined = tmp_path / "daily_bars.parquet"
    frame.to_parquet(combined, index=False)
    provider = LocalParquetProvider(tmp_path)
    rows = provider.fetch_daily_bars("AAA", date(2024, 1, 2), date(2024, 1, 4))
    assert isinstance(rows, list) and len(rows) == 2
    assert rows[0].high == 11.0
    assert rows[0].tri == 10.5
    digest = hash_file_bytes([combined])
    again = hash_file_bytes([combined])
    assert digest == again
    assert len(digest) == 64
    listing_hash = hash_file_bytes([combined])
    assert listing_hash != ""


def test_committed_synthetic_fixture_is_readable() -> None:
    pytest.importorskip("pyarrow")
    assert FIXTURE.is_file()
    frame = pd.read_parquet(FIXTURE)
    assert {"security_id", "session_date", "open", "high", "low", "close"} <= set(frame.columns)
    assert len(frame) >= 4
    assert frame["security_id"].nunique() >= 2
