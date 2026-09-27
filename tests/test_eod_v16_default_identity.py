"""The default scoring path must stay identical to v1.6 while v1.7 candidates are added.

``tests/fixtures/eod_v16_golden.json.gz`` was written from the v1.6 production code
(commit 3de252f9) before any v1.7 switch existed: nine views of a deterministic
synthetic panel, compacted the way the worker stores them and projected the way
``/api/strength/scan`` reads them. Floats are rounded to six decimals so the file
survives BLAS/LAPACK differences between machines. Regenerate deliberately with
``EOD_GOLDEN_REWRITE=1`` only when v1.6 behaviour is meant to change.

``score_eod_session`` and ``precompute_all_horizon_inputs`` without options are
the replay's ``v16`` baseline and stay this fixture. Production (``LIVE_CONFIG``)
is v1.7 and is covered by ``tests/test_eod_v17_adoption.py``; only the version
label in the compact payload follows the code version, so it is compared apart.
"""
from __future__ import annotations

import gzip
import json
import os
from pathlib import Path

import pytest

from app.services.eod_limited import COMPUTE_VERSION, PURPOSE_SYNTHETIC
from app.services.eod_limited.inference import precompute_all_horizon_inputs, score_eod_session
from app.services.eod_limited.market_registry import ALL_MARKET_STOCKS, load_market_registry
from app.services.eod_limited.project import project_strength_payload
from app.services.eod_limited.worker import _compact_variant
from app.services.research_eod_v1.constants import HORIZONS, PROFILES

from eod_v17_fixtures import SESSION, build_panel, rounded

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "eod_v16_golden.json.gz"
THEMES = [ALL_MARKET_STOCKS, "semiconductors", "etfs"]
VOLATILE_KEYS = {"generated_at"}
# The label names the code version, not the scoring path; the fixture holds v1.6's.
VERSION_KEYS = {"compute_version": "limited-all-market-v1.6"}


def nine_views(**kwargs) -> dict[str, dict]:
    """Score every view the worker publishes; ``kwargs`` reach both production entry points."""
    registry = kwargs.pop("registry", None) or load_market_registry()
    panel = build_panel()
    inputs = precompute_all_horizon_inputs(panel, SESSION, registry=registry, horizons=HORIZONS, themes=THEMES,
                                           **kwargs)
    cache: dict = {}
    views = {}
    for horizon in HORIZONS:
        raws, clipped, themed = inputs[horizon]
        for profile in PROFILES:
            scored = score_eod_session(
                panel, SESSION, registry=registry, profile=profile, horizon=horizon, themes=THEMES,
                purpose=PURPOSE_SYNTHETIC, precomputed_raws=raws, clipped_panel=clipped,
                precomputed_theme_raws=themed, compact=True, snapshot_cache=cache, **kwargs,
            )
            compact = {key: value for key, value in _compact_variant(scored).items() if key not in VOLATILE_KEYS}
            payload = project_strength_payload(compact, parameters={"profile": profile, "timeframe": horizon})
            views[f"{profile}/{horizon}"] = {"compact": compact, "rows": payload["rows"],
                                             "factor_capabilities": payload["factor_capabilities"]}
    return views


def test_default_path_reproduces_the_v16_golden_views():
    current = rounded(nine_views())
    if os.environ.get("EOD_GOLDEN_REWRITE") == "1":
        with gzip.GzipFile(GOLDEN, "wb", mtime=0) as handle:
            handle.write(json.dumps(current, sort_keys=True, default=str).encode())
    golden = json.loads(gzip.open(GOLDEN, "rt").read())
    assert set(current) == set(golden)
    for key in golden:
        assert current[key]["rows"] == golden[key]["rows"], key
        assert current[key]["factor_capabilities"] == golden[key]["factor_capabilities"], key
        for version_key, golden_value in VERSION_KEYS.items():
            assert golden[key]["compact"][version_key] == golden_value, key
            assert current[key]["compact"][version_key] == COMPUTE_VERSION, key
        assert {k: v for k, v in current[key]["compact"].items() if k not in VERSION_KEYS} == \
            {k: v for k, v in golden[key]["compact"].items() if k not in VERSION_KEYS}, key
    listed = sum(len(view["rows"]) for view in golden.values())
    assert listed > 40, "the fixture must exercise non-trivial lists"


@pytest.mark.parametrize("profile", PROFILES)
def test_golden_views_carry_stock_and_fund_rows(profile):
    golden = json.loads(gzip.open(GOLDEN, "rt").read())
    tracks = {row["stock_or_etf_track"] for row in golden[f"{profile}/mid"]["rows"]}
    assert "stock" in tracks
    assert all(row["factors"].get("G") is None for view in golden.values() for row in view["rows"])
