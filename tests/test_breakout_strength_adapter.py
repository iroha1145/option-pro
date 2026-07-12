from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from app.services.breakouts.adapters.strength import ExistingStrengthAdapter
from app.services.strength import scanner


def _history(offset: float = 0.0, size: int = 420) -> pd.DataFrame:
    index = pd.date_range("2025-01-01", periods=size, freq="B")
    close = 50.0 + offset + np.arange(size) * 0.12 + np.sin(np.arange(size) / 6)
    return pd.DataFrame(
        {
            "Open": close - 0.2,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": 2_000_000 + np.arange(size) * 1000,
        },
        index=index,
    )


def _panel() -> pd.DataFrame:
    panel = pd.concat(
        {
            "AAA": _history(),
            "BBB": _history(10),
            "SPY": _history(-5),
        },
        axis=1,
    )
    panel.attrs["price_source"] = {"provider": "fixture", "status": "active"}
    return panel


def test_explicit_ticker_set_score_is_invariant_to_candidate_set(monkeypatch) -> None:
    panel = _panel()
    monkeypatch.setattr(scanner, "_download_history", lambda _tickers, period="2y": panel)
    as_of = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)

    single = asyncio.run(
        scanner.score_ticker_set(["AAA"], as_of=as_of, range_mode="shadow")
    )
    combined = asyncio.run(
        scanner.score_ticker_set(["BBB", "AAA"], as_of=as_of, range_mode="shadow")
    )
    first = single["rows"][0]
    second = next(row for row in combined["rows"] if row["ticker"] == "AAA")
    assert first["score"] == second["score"]
    assert first["score_scope"] == "intrinsic"
    assert first["market_regime_score"] is None
    assert first["sector_score"] is None
    assert first["option_context"]["status"] == "skipped"
    assert first["range_persistence_shadow"]["production_unchanged"] is True


def test_explicit_scoring_rejects_option_enrichment(monkeypatch) -> None:
    monkeypatch.setattr(scanner, "_download_history", lambda _tickers, period="2y": _panel())
    with pytest.raises(ValueError, match="option"):
        asyncio.run(scanner.score_ticker_set(["AAA"], include_options=True))


def test_strength_adapter_preserves_intrinsic_provenance(monkeypatch) -> None:
    async def fake_score(tickers, **kwargs):
        assert kwargs["include_options"] is False
        return {
            "rows": [
                {
                    "ticker": tickers[0],
                    "score": 77.0,
                    "score_scope": "intrinsic",
                    "confidence": 0.75,
                    "score_version": "strength-intrinsic-v1",
                    "included_features": ["momentum_63d"],
                    "factor_breakdown": {},
                    "coverage": {"ratio": 0.75},
                }
            ]
        }

    monkeypatch.setattr(scanner, "score_ticker_set", fake_score)
    as_of = datetime(2026, 7, 10, tzinfo=timezone.utc)
    result = asyncio.run(
        ExistingStrengthAdapter().score_ticker_set(["aaa"], as_of=as_of)
    )
    assert result["AAA"].score_scope == "intrinsic"
    assert result["AAA"].score == 77.0
    assert result["AAA"].included_features == ["momentum_63d"]
