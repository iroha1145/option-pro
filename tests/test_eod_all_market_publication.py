from datetime import date

import pytest

from app.api import strength
from app.services.eod_limited import PURPOSE_LIVE, market_data, worker
from app.services.eod_limited.project import project_strength_payload
from app.services.eod_limited.store import publish_batch, read_batch, snapshot_path
from tests.test_eod_limited_product import _scored


def test_legacy_universe_hash_is_checked_before_execution_alias():
    legacy = dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS)
    before = strength.strength_scan_parameters_hash(legacy)
    current = strength.strength_execution_parameters(legacy)
    assert current["universe"] == "all_market"
    assert legacy["universe"] == "themes"
    assert strength.strength_scan_parameters_hash(legacy) == before
    assert strength.strength_scan_parameters_hash(current) != before


def test_manual_refresh_schema_accepts_whole_market_and_legacy_alias():
    from app.api.worker_actions import StrengthRefreshParameters
    for universe in ("all_market", "themes"):
        parsed = StrengthRefreshParameters.model_validate({
            **strength.DEFAULT_STRENGTH_SCAN_PARAMETERS, "universe": universe,
        })
        assert parsed.universe == universe


def test_live_subset_cannot_replace_whole_market(tmp_path):
    with pytest.raises(ValueError, match="does_not_allow_ticker_subsets"):
        worker.run_eod_limited_job(session=date(2026, 9, 18), root=tmp_path, tickers=["NVDA"])
    assert not snapshot_path(tmp_path).exists()


def test_incomplete_market_keeps_previous_atomic_batch(tmp_path, monkeypatch):
    old = {"purpose": PURPOSE_LIVE, "served_session": "2026-09-17", "variants": {"balanced|mid": _scored()}}
    publish_batch(old, root=tmp_path)
    before = snapshot_path(tmp_path).read_bytes()
    panel = worker.build_synthetic_panel(end=date(2026, 9, 18))
    monkeypatch.setattr(market_data, "load_all_market_panel", lambda **kw: (
        panel, [], {"status": "complete", "eligible_count": 100, "complete_bar_count": len(panel)},
    ))
    result = worker.run_eod_limited_job(session=date(2026, 9, 18), root=tmp_path)
    assert result["publish"]["reason"] == "all_market_coverage_incomplete"
    assert snapshot_path(tmp_path).read_bytes() == before


def test_live_market_always_replaces_all_nine_views(tmp_path, monkeypatch):
    from app.services.eod_limited import inference

    target = date(2026, 9, 18)
    panel = worker.build_synthetic_panel(end=target)
    old_variant = {**_scored(), "old_marker": True, "volume_scope": "old"}
    publish_batch({"purpose": PURPOSE_LIVE, "served_session": str(target),
                   "variants": {"balanced|mid": old_variant}}, root=tmp_path)
    previous = read_batch(tmp_path)
    manifest = {"status": "complete", "eligible_count": len(panel), "complete_bar_count": len(panel),
                "source_hash": "new-input", "volume_session_scope": market_data.VOLUME_SCOPE}
    monkeypatch.setattr(market_data, "load_all_market_panel", lambda **kw: (panel, [], manifest))
    calls = []
    monkeypatch.setattr(inference, "precompute_all_horizon_inputs", lambda *a, **kw: {
        h: ({}, panel, {}) for h in kw["horizons"]
    })
    def score(*a, **kw):
        calls.append((kw["profile"], kw["horizon"]))
        return {**_scored(session=str(target), purpose=PURPOSE_LIVE),
                "profile": kw["profile"], "horizon": kw["horizon"],
                "scored_security_count": len(panel)}
    monkeypatch.setattr(worker, "score_eod_session", score)
    result = worker.run_eod_limited_job(session=target, root=tmp_path, all_variants=False, refresh_context=False)
    assert result["status"] == "RAN"
    assert len(calls) == len(set(calls)) == 9
    current = read_batch(tmp_path)
    assert current["universe"] == "all_market"
    assert all(v["volume_scope"] == market_data.VOLUME_SCOPE for v in current["variants"].values())
    assert all("old_marker" not in v for v in current["variants"].values())
    assert previous["variants"]["balanced|mid"]["volume_scope"] == "old"


def test_public_coverage_separates_data_and_selection():
    payload = project_strength_payload({**_scored(), "universe": "all_market", "coverage": {
        "eligible_count": 11000, "complete_bar_count": 10600, "missing_session_count": 400,
        "scored_count": 10600, "provider": "Massive", "cache_path": "/private/storage.sqlite",
    }}, parameters={})
    assert payload["universe_count"] == 11000
    assert payload["screened_count"] == 10600
    assert payload["observation_n"] == 1
    assert payload["coverage"]["missing_session_count"] == 400
    assert "cache_path" not in payload["coverage"]
