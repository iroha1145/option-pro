from __future__ import annotations

import math

from app.services.research_eod_v1.config_load import (
    load_composite_manifest,
    load_etf_subasset_manifest,
    load_experiment_manifest,
    load_registry,
)
from app.services.research_eod_v1.constants import ALGORITHMS, FACTORS, HORIZONS, PROFILES
from app.services.research_eod_v1.paths import REFERENCE_DIR
import sys

if str(REFERENCE_DIR) not in sys.path:
    sys.path.insert(0, str(REFERENCE_DIR))
from registry import resolve_weights  # type: ignore


def test_registry_has_24_sectors_and_four_families() -> None:
    data = load_registry()
    assert len(data["sectors"]) == 24
    assert set(data["base_algorithm_weights"]) == set(ALGORITHMS)
    assert set(data["profiles"]) == set(PROFILES)
    assert set(data["horizons"]) == set(HORIZONS)
    for sector in data["sectors"].values():
        assert set(sector["candidates"]) == set(ALGORITHMS)


def test_experiment_manifest_has_864_explicit_weights() -> None:
    data = load_registry()
    manifest = load_experiment_manifest()
    assert manifest["count"] == 864
    assert len(manifest["experiments"]) == 864
    ids = [row["experiment_id"] for row in manifest["experiments"]]
    assert len(ids) == len(set(ids))
    for row in manifest["experiments"]:
        expected = resolve_weights(data, row["sector_id"], row["algorithm"], row["profile"], row["horizon"])
        for factor in FACTORS:
            assert math.isclose(row["weights"][factor], expected[factor], abs_tol=1e-8)
        assert math.isclose(sum(row["weights"].values()), 1.0, abs_tol=1e-8)


def test_etf_and_composite_counts() -> None:
    etf = load_etf_subasset_manifest()
    composite = load_composite_manifest()
    assert etf["count"] == 180
    assert len(etf["experiments"]) == 180
    gold = [row for row in etf["experiments"] if row["subasset_id"] == "gold"]
    assert gold
    assert all(row["spy_residual_allowed"] is False for row in gold)
    bonds = [row for row in etf["experiments"] if row["subasset_id"] == "long_bond"]
    assert all(row["spy_residual_allowed"] is False for row in bonds)
    assert composite["count"] == 12
    assert {row["method"] for row in composite["experiments"]} == {
        "M1_consensus_veto",
        "M2_conservative_utility",
        "M3_diversified_rank",
        "M4_regime_experts",
    }
