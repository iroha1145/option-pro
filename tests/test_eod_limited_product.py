from __future__ import annotations

import asyncio
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from app.api import strength
from app.services.algorithm_modes import EOD_LIMITED_V1, PRODUCTION_ALGORITHM
from app.services.eod_limited import (
    MODE_ID,
    PURPOSE_HISTORICAL,
    PURPOSE_LIVE,
    PURPOSE_SYNTHETIC,
    RESEARCH_SEALED_SESSION,
)
from app.services.eod_limited.price_only import resolve_capability_flags
from app.services.eod_limited.project import project_strength_payload
from app.services.eod_limited.store import publish_batch, read_variant, variant_key
from app.services.eod_limited.worker import seed_labeled_batch
from app.worker.tasks import StrengthRefreshTask
from tests.http_response_support import anonymous_get_request as _areq, response_payload as _rp


def _scored(*, session: str = "2024-06-28", purpose: str = PURPOSE_HISTORICAL, eligible: bool = False) -> dict:
    watch = {
        "security_id": "NVDA",
        "score": 71.2,
        "status": "watch",
        "qualification": "watch",
        "sector_context": "semiconductors",
        "algorithm_id": "A_trend_quality",
        "stock_or_etf_track": "stock",
        "rejection_reasons": ["DOLLAR_LIQUIDITY_UNVERIFIED"],
        "factors": {"T": 80, "M": 70, "S": 60, "B": 50, "P": 40, "V": 30, "R": 20, "G": 10},
        "price": 120.5,
        "session_date": session,
    }
    eligible_row = dict(watch)
    eligible_row["status"] = "eligible"
    eligible_row["qualification"] = "eligible"
    eligible_row["rejection_reasons"] = []
    return {
        "session_date": session,
        "served_session": session,
        "attempted_session": session,
        "purpose": purpose,
        "compute_version": "limited-current-v1.1",
        "feature_version": "us-eod-research-features-v1.6",
        "profile": "balanced",
        "horizon": "mid",
        "capability_flags": resolve_capability_flags(),
        "volume_scope": "VENDOR_DAILY_UNVERIFIED",
        "panel_n": 4,
        "complete_bar_n": 4,
        "family_results": [
            {
                "theme_id": "semiconductors",
                "algorithm_id": "A_trend_quality",
                "rows": [eligible_row if eligible else watch],
            }
        ],
        "composite_results": [eligible_row] if eligible else [],
        "watch_list": [] if eligible else [watch],
        "eligible_n": 1 if eligible else 0,
        "watch_n": 0 if eligible else 1,
        "rejected_n": 0,
        "composite_n": 1 if eligible else 0,
        "historical_example": purpose != PURPOSE_LIVE,
        "synthetic": purpose == PURPOSE_SYNTHETIC,
    }


def test_eod_bar_download_does_not_override_batch_threads() -> None:
    from app.services.eod_limited.bars import DOWNLOAD_PARAMS

    assert "threads" not in DOWNLOAD_PARAMS
    assert DOWNLOAD_PARAMS["group_by"] == "ticker"
    assert DOWNLOAD_PARAMS["auto_adjust"] is False


def test_select_universe_tickers_stays_bounded() -> None:
    from app.services.eod_limited.panel import current_universe_tickers, select_universe_tickers

    full = current_universe_tickers()
    bounded = select_universe_tickers(["nvda", "SPY", "NOTREAL"])
    assert set(bounded) == {"NVDA", "SPY"}
    assert bounded["NVDA"] == full["NVDA"]
    assert "NOTREAL" not in bounded
    assert len(bounded) < len(full)


def test_last_complete_session_respects_holiday_half_day_and_timezone() -> None:
    from app.services.research_eod_v1.calendar_asof import (
        last_complete_eod_session,
        require_aware,
        session_is_partial,
    )

    et = ZoneInfo("America/New_York")
    utc = ZoneInfo("UTC")
    assert last_complete_eod_session(datetime(2026, 9, 7, 17, 0, tzinfo=et)) == date(2026, 9, 4)
    assert last_complete_eod_session(datetime(2024, 7, 3, 12, 30, tzinfo=et)) == date(2024, 7, 2)
    assert last_complete_eod_session(datetime(2024, 7, 3, 13, 5, tzinfo=et)) == date(2024, 7, 3)
    assert last_complete_eod_session(datetime(2026, 9, 18, 9, 0, tzinfo=utc)) == date(2026, 9, 17)
    assert last_complete_eod_session(datetime(2026, 9, 18, 21, 0, tzinfo=utc)) == date(2026, 9, 18)
    assert session_is_partial(date(2026, 9, 18), datetime(2026, 9, 18, 12, 0, tzinfo=et)) is True
    assert session_is_partial(date(2026, 9, 18), datetime(2026, 9, 18, 16, 5, tzinfo=et)) is False
    with pytest.raises(ValueError, match="timezone-aware"):
        require_aware(datetime(2026, 9, 18, 16, 0))


def test_live_job_relabels_sealed_research_session(tmp_path: Path) -> None:
    from app.services.eod_limited.worker import build_synthetic_panel, run_eod_limited_job

    outcome = run_eod_limited_job(
        session=RESEARCH_SEALED_SESSION,
        purpose=PURPOSE_LIVE,
        panel=build_synthetic_panel(end=RESEARCH_SEALED_SESSION),
        root=tmp_path,
        themes=("semiconductors",),
        algorithms=("A_trend_quality",),
    )
    assert outcome["purpose"] == PURPOSE_HISTORICAL
    assert outcome["served_session"] == "2024-06-28"


def test_capability_flags_default_false() -> None:
    flags = resolve_capability_flags()
    assert flags == {
        "dollar_liquidity_verified": False,
        "volume_session_verified": False,
        "volume_verified": False,
    }


def test_project_keeps_watch_and_empty_composite() -> None:
    payload = project_strength_payload(_scored(), parameters={"profile": "balanced", "timeframe": "mid"})
    assert payload["effective_algorithm"] == MODE_ID
    assert payload["observation_n"] == 1
    assert payload["composite_n"] == 0
    assert payload["empty_eligible_reason"] == "data_qualification_unverified"
    assert payload["historical_example"] is True
    assert payload["rows"][0]["ticker"] == "NVDA"
    assert payload["rows"][0]["price"] == 120.5
    assert payload["rows"][0]["factor_dims"][0]["key"] == "factor_T"
    assert payload["capability_flags"]["volume_verified"] is False


def test_observation_rows_dedupe_same_security_across_themes() -> None:
    scored = _scored()
    second = dict(scored["watch_list"][0])
    second["sector_context"] = "ai_cloud"
    second["score"] = 60.0
    scored["watch_list"].append(second)
    payload = project_strength_payload(scored, parameters={"profile": "balanced", "timeframe": "mid"})
    tickers = [row["ticker"] for row in payload["rows"]]
    assert tickers == ["NVDA"]
    assert payload["observation_n"] == 1
    assert payload["rows"][0]["sort_score"] == 71.2


def test_store_is_isolated_from_strength_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.eod_limited import store as eod_store

    monkeypatch.setattr(eod_store, "snapshot_dir", lambda root=None: tmp_path / "eod-limited-v1")
    batch = {
        "purpose": PURPOSE_HISTORICAL,
        "served_session": "2024-06-28",
        "attempted_session": "2024-06-28",
        "variants": {variant_key("balanced", "mid"): _scored()},
    }
    published = publish_batch(batch, root=tmp_path)
    assert published["ok"] is True
    from app.services.eod_limited.store import read_batch

    assert published["published_at"] == read_batch(tmp_path)["published_at"]
    scored = read_variant("balanced", "mid", root=tmp_path)
    assert scored is not None
    assert scored["historical_example"] is True
    assert (tmp_path / "eod-limited-v1" / "batch.json").is_file()


def test_scan_reads_eod_snapshot_not_production(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.eod_limited import store as eod_store

    monkeypatch.setattr(eod_store, "snapshot_dir", lambda root=None: tmp_path / "eod-limited-v1")
    publish_batch(
        {
            "purpose": PURPOSE_HISTORICAL,
            "served_session": "2024-06-28",
            "attempted_session": "2024-06-28",
            "published_at": 1_700_000_000,
            "variants": {variant_key("balanced", "mid"): _scored()},
        },
        root=tmp_path,
    )
    result = _rp(
        asyncio.run(
            strength.scan(
                _areq(),
                universe="themes",
                timeframe="mid",
                profile="balanced",
                top=20,
                sector_id=None,
                min_price=5.0,
                min_avg_dollar_volume=10_000_000.0,
                ranking_algorithm=EOD_LIMITED_V1,
            )
        )
    )
    assert result["effective_algorithm"] == EOD_LIMITED_V1
    assert result["historical_example"] is True
    assert result["served_session"] == "2024-06-28"
    assert result["rows"][0]["ticker"] == "NVDA"
    assert result["snapshot_source"] == "eod_limited_worker"


def test_explicit_eod_all_timeframe_is_conflict() -> None:
    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            strength.scan(
                _areq(),
                universe="themes",
                timeframe="all",
                profile="balanced",
                top=20,
                sector_id=None,
                min_price=5.0,
                min_avg_dollar_volume=10_000_000.0,
                ranking_algorithm=EOD_LIMITED_V1,
            )
        )
    assert caught.value.status_code == 400
    assert caught.value.detail["code"] == "algorithm_view_conflict"


def test_missing_eod_snapshot_does_not_serve_production(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.eod_limited import store as eod_store

    monkeypatch.setattr(eod_store, "snapshot_dir", lambda root=None: tmp_path / "missing-eod")
    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            strength.scan(
                _areq(),
                universe="themes",
                timeframe="mid",
                profile="balanced",
                top=20,
                sector_id=None,
                min_price=5.0,
                min_avg_dollar_volume=10_000_000.0,
                ranking_algorithm=EOD_LIMITED_V1,
            )
        )
    assert caught.value.status_code == 503
    assert caught.value.detail["code"] in {
        "eod_limited_snapshot_unavailable",
        "eod_limited_snapshot_preparing",
    }
    assert caught.value.detail["effective_algorithm"] == EOD_LIMITED_V1


def test_worker_intercepts_eod_ranking() -> None:
    calls: list[dict] = []

    def runner(**kwargs):
        calls.append(kwargs)
        return {
            "status": "RAN",
            "session": "2024-06-27",
            "served_session": "2024-06-27",
            "purpose": PURPOSE_LIVE,
            "available_variants": ["balanced|mid"],
            "compute_version": "limited-current-v1.1",
            "published_at": 1_800_000_000.0,
            "publish": {"ok": True, "published_at": 1_800_000_000.0},
        }

    task = StrengthRefreshTask(eod_runner=runner)
    result = asyncio.run(
        task._run(
            {
                "universe": "themes",
                "timeframe": "mid",
                "profile": "balanced",
                "top": 20,
                "sector_id": None,
                "min_price": 5.0,
                "min_avg_dollar_volume": 10_000_000.0,
                "include_options": True,
                "ranking_algorithm": EOD_LIMITED_V1,
            }
        )
    )
    assert result.status == "idle"
    assert result.details["published"] is True
    assert calls[0]["purpose"] == PURPOSE_LIVE
    assert calls[0]["all_variants"] is True


def test_synthetic_panel_covers_sealed_session() -> None:
    from app.services.eod_limited.worker import build_synthetic_panel

    panel = build_synthetic_panel(end=RESEARCH_SEALED_SESSION)
    last = max(series.dates[-1] for series in panel.values())
    assert last == RESEARCH_SEALED_SESSION
    assert all(len(series.dates) >= 330 for series in panel.values())


def test_seed_historical_is_labeled(tmp_path: Path) -> None:
    outcome = seed_labeled_batch(
        purpose=PURPOSE_HISTORICAL,
        session=RESEARCH_SEALED_SESSION,
        profile="balanced",
        horizon="mid",
        root=tmp_path,
        all_variants=False,
        themes=("semiconductors",),
        algorithms=("A_trend_quality",),
    )
    assert outcome["purpose"] == PURPOSE_HISTORICAL
    scored = read_variant("balanced", "mid", root=tmp_path)
    assert scored is not None
    assert scored["historical_example"] is True
    assert scored["served_session"] == "2024-06-28"
    assert scored["purpose"] == PURPOSE_HISTORICAL
    assert scored["synthetic"] is True
    assert int(scored.get("complete_bar_n") or 0) >= 1
    assert int(scored.get("watch_n") or 0) + int(scored.get("eligible_n") or 0) >= 1
    assert scored.get("capability_flags", {}).get("volume_verified") is False


def test_seed_refuses_live_label() -> None:
    with pytest.raises(ValueError):
        seed_labeled_batch(purpose=PURPOSE_LIVE)


def test_composite_projection_displays_and_sorts_by_consensus() -> None:
    from app.services.research_eod_v1.composite import m1_consensus

    template = _scored(eligible=True)["family_results"][0]["rows"][0]
    family_rows = [
        {**template, "security_id": ticker, "algorithm_id": algorithm, "score": score}
        for ticker, algorithm, score in [
            ("NVDA", "A_trend_quality", 80.0),
            ("NVDA", "D_residual_momentum", 100.0),
            ("AMD", "A_trend_quality", 90.0),
            ("AMD", "D_residual_momentum", 80.0),
        ]
    ]
    scored = _scored(eligible=True)
    scored["composite_results"] = m1_consensus(family_rows, "balanced", 20)
    scored["composite_n"] = 2
    payload = project_strength_payload(scored, parameters={}, list_kind="composite")
    assert [(row["ticker"], row["score"], row["sort_score"]) for row in payload["rows"]] == [
        ("NVDA", 90.0, 90.0), ("AMD", 85.0, 85.0),
    ]


def test_normalize_accepts_eod_algorithm() -> None:
    payload = strength.normalize_strength_scan_parameters(
        {
            "universe": "themes",
            "timeframe": "mid",
            "profile": "balanced",
            "top": 20,
            "sector_id": None,
            "min_price": 5.0,
            "min_avg_dollar_volume": 10_000_000.0,
            "include_options": True,
            "ranking_algorithm": EOD_LIMITED_V1,
        }
    )
    assert payload["ranking_algorithm"] == EOD_LIMITED_V1
    default = strength.normalize_strength_scan_parameters(dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS))
    assert "ranking_algorithm" not in default
    assert default["timeframe"] == "all"
    scheduled = strength.scheduled_strength_scan_parameters(
        type(
            "Settings",
            (),
            {
                "algorithms": type(
                    "Algos",
                    (),
                    {
                        "screener_ranking_algorithm": EOD_LIMITED_V1,
                        "radar_sort_algorithm": PRODUCTION_ALGORITHM,
                    },
                )()
            },
        )()
    )
    assert scheduled["ranking_algorithm"] == EOD_LIMITED_V1
    assert scheduled["timeframe"] == "mid"


def test_unspecified_scan_consumes_eod_mid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.eod_limited import store as eod_store

    monkeypatch.setattr(eod_store, "snapshot_dir", lambda root=None: tmp_path / "eod-limited-v1")
    monkeypatch.setattr(
        strength,
        "get_effective_runtime_settings",
        lambda: type(
            "Settings",
            (),
            {
                "algorithms": type(
                    "Algos",
                    (),
                    {
                        "screener_ranking_algorithm": EOD_LIMITED_V1,
                        "radar_sort_algorithm": PRODUCTION_ALGORITHM,
                    },
                )()
            },
        )(),
    )
    publish_batch(
        {
            "purpose": PURPOSE_HISTORICAL,
            "served_session": "2024-06-28",
            "attempted_session": "2024-06-28",
            "published_at": 1_700_000_000,
            "variants": {variant_key("balanced", "mid"): _scored()},
        },
        root=tmp_path,
    )
    result = _rp(
        asyncio.run(
            strength.scan(
                _areq(),
                universe="themes",
                profile="balanced",
                top=20,
                sector_id=None,
                min_price=5.0,
                min_avg_dollar_volume=10_000_000.0,
            )
        )
    )
    assert result["effective_algorithm"] == EOD_LIMITED_V1
    assert result["resolved_timeframe"] == "mid"
    assert result["rows"][0]["ticker"] == "NVDA"


def test_scheduled_refresh_publishes_eod_when_default_is_eod() -> None:
    eod_calls: list[dict] = []
    scanner_calls: list[dict] = []

    def fake_eod(**kwargs):
        eod_calls.append(kwargs)
        return {
            "status": "RAN",
            "served_session": "2026-09-18",
            "available_variants": ["balanced:mid"] * 9,
            "purpose": PURPOSE_LIVE,
            "compute_version": "limited-current-v1.1",
            "published_at": 1_800_000_000.0,
            "publish": {"ok": True, "integrity": "ok", "published_at": 1_800_000_000.0},
        }

    async def fake_scanner(**kwargs):
        scanner_calls.append(kwargs)
        raise AssertionError("production scanner must not run for the EOD default")

    async def _run() -> None:
        from app.api import strength as strength_api

        original = strength_api.get_effective_runtime_settings
        strength_api.get_effective_runtime_settings = lambda: type(
            "Settings",
            (),
            {
                "algorithms": type(
                    "Algos",
                    (),
                    {
                        "screener_ranking_algorithm": EOD_LIMITED_V1,
                        "radar_sort_algorithm": PRODUCTION_ALGORITHM,
                    },
                )()
            },
        )()
        try:
            result = await StrengthRefreshTask(
                scanner=fake_scanner,
                eod_runner=fake_eod,
                clock=lambda: 1_800_000_000.0,
            )()
        finally:
            strength_api.get_effective_runtime_settings = original
        assert result.status == "idle"
        assert result.details.get("snapshot") == "eod-limited-v1/batch.json"
        assert eod_calls
        assert eod_calls[0]["horizon"] == "mid"
        assert not scanner_calls

    asyncio.run(_run())


@pytest.mark.parametrize("input_kind", ["empty", "partial", "invalid_ohlc"])
def test_failed_daily_input_retains_previous_batch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, input_kind: str) -> None:
    from app.services.eod_limited import worker
    from app.services.eod_limited.store import snapshot_path
    from app.services.research_eod_v1.data.contract import ResearchBar

    publish_batch({
        "purpose": PURPOSE_LIVE,
        "served_session": "2026-09-17",
        "variants": {variant_key("balanced", "mid"): _scored(session="2026-09-17", purpose=PURPOSE_LIVE)},
    }, root=tmp_path)
    original = snapshot_path(tmp_path).read_bytes()
    bar = ResearchBar(
        security_id="NVDA", session_date=date(2026, 9, 18),
        open=100, high=90 if input_kind == "invalid_ohlc" else 101, low=99, close=100,
        raw_open=100, raw_close=100, volume=1_000_000, dollar_volume=100_000_000, tri=100,
        partial=input_kind == "partial",
    )
    monkeypatch.setattr(worker, "fetch_current_universe_bars", lambda **kwargs: {} if input_kind == "empty" else {"NVDA": [bar]})
    outcome = worker.run_eod_limited_job(session=date(2026, 9, 18), root=tmp_path, tickers=["NVDA"])
    assert outcome["status"] == "DATA_UNAVAILABLE"
    assert outcome["publish"]["ok"] is False
    assert outcome["publish"]["attempted_session"] == "2026-09-18"
    assert outcome["served_session"] == "2026-09-17"
    assert snapshot_path(tmp_path).read_bytes() == original


def test_empty_input_without_previous_snapshot_is_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.eod_limited import worker
    from app.services.eod_limited.store import snapshot_path

    monkeypatch.setattr(worker, "fetch_current_universe_bars", lambda **kwargs: {})
    outcome = worker.run_eod_limited_job(session=date(2026, 9, 18), root=tmp_path)
    assert outcome["status"] == "DATA_UNAVAILABLE"
    assert outcome["served_session"] is None
    assert not snapshot_path(tmp_path).exists()


@pytest.mark.parametrize("older_session", [date(2026, 9, 17), RESEARCH_SEALED_SESSION])
def test_older_vendor_session_cannot_replace_newer_live_batch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, older_session: date) -> None:
    from app.services.eod_limited import worker
    from app.services.eod_limited.store import snapshot_path
    from app.services.research_eod_v1.data.contract import ResearchBar

    publish_batch({
        "purpose": PURPOSE_LIVE,
        "served_session": "2026-09-18",
        "variants": {variant_key("balanced", "mid"): _scored(session="2026-09-18", purpose=PURPOSE_LIVE)},
    }, root=tmp_path)
    original = snapshot_path(tmp_path).read_bytes()
    older_bar = ResearchBar(
        security_id="NVDA", session_date=older_session, open=100, high=101, low=99, close=100,
        raw_open=100, raw_close=100, volume=1_000_000, dollar_volume=100_000_000, tri=100,
    )
    monkeypatch.setattr(worker, "fetch_current_universe_bars", lambda **kwargs: {"NVDA": [older_bar]})
    outcome = worker.run_eod_limited_job(session=date(2026, 9, 21), root=tmp_path, tickers=["NVDA"])
    assert outcome["status"] == "DATA_UNAVAILABLE"
    assert outcome["publish"]["reason"] == "older_session_than_published"
    assert outcome["publish"]["attempted_session"] == "2026-09-21"
    assert outcome["served_session"] == "2026-09-18"
    assert snapshot_path(tmp_path).read_bytes() == original
    # Explicit historical examples remain available in isolated test roots.
    worker.seed_labeled_batch(
        purpose=PURPOSE_HISTORICAL, root=tmp_path,
        themes=["semiconductors"], algorithms=["A_trend_quality"],
    )
    assert read_variant("balanced", "mid", root=tmp_path)["historical_example"] is True


def test_complete_bars_without_qualified_signals_publish(tmp_path: Path) -> None:
    from app.services.eod_limited.worker import build_synthetic_panel, run_eod_limited_job

    panel = build_synthetic_panel(sessions=5, end=date(2026, 9, 18))
    outcome = run_eod_limited_job(
        session=date(2026, 9, 18), panel=panel, root=tmp_path,
        themes=["semiconductors"], algorithms=["A_trend_quality"],
    )
    assert outcome["status"] == "RAN"
    assert outcome["published_at"] == outcome["publish"]["published_at"]
    scored = read_variant("balanced", "mid", root=tmp_path)
    assert scored["complete_bar_n"] > 0
    assert scored["watch_n"] == scored["eligible_n"] == 0


def test_panel_excludes_known_foreign_and_otc_names() -> None:
    from app.services.eod_limited.panel import bars_to_panel
    from app.services.research_eod_v1.data.contract import ResearchBar

    names = ["RMS.PA", "LVMUY", "CFRUY", "NVDA"]
    bars = {
        name: [ResearchBar(
            security_id=name, session_date=date(2026, 9, 18), open=100, high=101, low=99, close=100,
            raw_open=100, raw_close=100, volume=1_000_000, dollar_volume=100_000_000, tri=100,
        )]
        for name in names
    }
    panel, coverage = bars_to_panel(bars, {name: ["luxury"] for name in names})
    assert set(panel) == {"NVDA"}
    statuses = {row["ticker"]: row["status"] for row in coverage}
    assert statuses["RMS.PA"] == "excluded:NON_US_LISTING"
    assert statuses["LVMUY"] == statuses["CFRUY"] == "excluded:OTC_EXCLUDED"


def test_all_variants_reuses_horizon_raws_without_changing_scores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json
    from dataclasses import asdict

    from app.services.eod_limited import inference, worker
    from app.services.eod_limited.panel import prepare_limited_panel
    from app.services.eod_limited.store import read_batch
    from app.services.research_eod_v1.config_load import load_registry
    from app.services.research_eod_v1.constants import ALGORITHMS, HORIZONS, PROFILES

    session = date(2026, 9, 18)
    panel = prepare_limited_panel(worker.build_synthetic_panel(end=session))
    original_precompute = worker.precompute_session_raws
    original_theme_precompute = worker.precompute_theme_raws
    original_apply_gates = inference.apply_sector_gates
    calls = []
    gate_calls = []
    retained_inputs = []

    def raw_fingerprint(raws):
        return json.dumps({sid: asdict(raw) for sid, raw in raws.items()}, sort_keys=True, default=str)

    def counted(*args, **kwargs):
        calls.append(kwargs["horizon"])
        return original_precompute(*args, **kwargs)

    def counted_gates(raw, series, gates):
        gate_calls.append((raw.security_id, id(gates)))
        return original_apply_gates(raw, series, gates)

    def checked_theme_precompute(raws, *args, **kwargs):
        before = raw_fingerprint(raws)
        prepared = original_theme_precompute(raws, *args, **kwargs)
        assert raw_fingerprint(raws) == before
        assert prepared["semiconductors"]["NVDA"] is not prepared["ai_cloud"]["NVDA"]
        retained_inputs.append((raws, before, prepared, {theme: raw_fingerprint(items) for theme, items in prepared.items()}))
        return prepared

    monkeypatch.setattr(worker, "precompute_session_raws", counted)
    monkeypatch.setattr(worker, "precompute_theme_raws", checked_theme_precompute)
    monkeypatch.setattr(inference, "apply_sector_gates", counted_gates)
    worker.run_eod_limited_job(
        session=session, panel=panel, root=tmp_path, all_variants=True,
        algorithms=ALGORITHMS,
    )
    assert calls == list(HORIZONS)
    # Six semiconductor names, one additional AI theme appearance, two ETFs.
    assert len(gate_calls) == len(HORIZONS) * 9
    assert len({id(raws) for raws, *_rest in retained_inputs}) == len(HORIZONS)
    for raws, before, prepared, prepared_before in retained_inputs:
        assert raw_fingerprint(raws) == before
        assert {theme: raw_fingerprint(items) for theme, items in prepared.items()} == prepared_before
    actual = read_batch(tmp_path)["variants"]
    registry = load_registry()
    for horizon in HORIZONS:
        for profile in PROFILES:
            # Recompute independently for every profile, as the previous worker did.
            raws, clipped = original_precompute(panel, session, registry=registry, horizon=horizon)
            scored = worker.score_eod_session(
                panel, session, registry=registry, profile=profile, horizon=horizon,
                precomputed_raws=raws, clipped_panel=clipped,
                algorithms=ALGORITHMS,
            )
            expected = worker._compact_variant(scored)
            observed = dict(actual[variant_key(profile, horizon)])
            expected.pop("generated_at")
            observed.pop("generated_at")
            # Serialized tuples become lists in the stored batch.
            assert observed == json.loads(json.dumps(expected, default=str))


def test_theme_precomputation_is_not_retained_after_failed_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.eod_limited import worker
    from app.services.eod_limited.store import snapshot_path

    session = date(2026, 9, 18)
    panel = worker.build_synthetic_panel(end=session)
    original_precompute = worker.precompute_theme_raws
    original_score = worker.score_eod_session
    calls = []

    def counted(*args, **kwargs):
        calls.append(args[0])
        return original_precompute(*args, **kwargs)

    def fail_scoring(*args, **kwargs):
        raise RuntimeError("injected scoring failure")

    monkeypatch.setattr(worker, "precompute_theme_raws", counted)
    monkeypatch.setattr(worker, "score_eod_session", fail_scoring)
    kwargs = dict(session=session, panel=panel, root=tmp_path, themes=["semiconductors"], algorithms=["A_trend_quality"])
    with pytest.raises(RuntimeError, match="injected scoring failure"):
        worker.run_eod_limited_job(**kwargs)
    assert not snapshot_path(tmp_path).exists()
    monkeypatch.setattr(worker, "score_eod_session", original_score)
    assert worker.run_eod_limited_job(**kwargs)["status"] == "RAN"
    assert len(calls) == 2
    assert calls[0] is not calls[1]


@pytest.mark.parametrize("volume_verified", [False, True])
def test_compact_variants_preserve_full_public_projection(volume_verified: bool) -> None:
    import json

    from app.services.eod_limited import worker
    from app.services.eod_limited.panel import prepare_limited_panel
    from app.services.research_eod_v1.config_load import load_registry
    from app.services.research_eod_v1.constants import HORIZONS, PROFILES

    session = date(2026, 9, 18)
    panel = prepare_limited_panel(worker.build_synthetic_panel(end=session))
    registry = load_registry()
    retained_eligible = 0
    observed_watch = 0
    omitted_rows = 0
    for horizon in HORIZONS:
        raws, clipped = worker.precompute_session_raws(panel, session, registry=registry, horizon=horizon)
        theme_raws = worker.precompute_theme_raws(raws, clipped, session, registry=registry)
        for profile in PROFILES:
            full = worker.score_eod_session(
                panel, session, registry=registry, profile=profile, horizon=horizon,
                volume_verified=volume_verified, precomputed_raws=raws, clipped_panel=clipped,
                precomputed_theme_raws=theme_raws,
            )
            original = json.dumps(full, sort_keys=True, default=str)
            compact = worker._compact_variant(full)
            assert json.dumps(full, sort_keys=True, default=str) == original
            for before, after in zip(full["family_results"], compact["family_results"], strict=True):
                assert {key: value for key, value in before.items() if key != "rows"} == {
                    key: value for key, value in after.items() if key != "rows"
                }
                assert after["rows"] == [row for row in before["rows"] if row["status"] == "eligible"]
                retained_eligible += len(after["rows"])
                omitted_rows += len(before["rows"]) - len(after["rows"])
            observed_watch += len(full["watch_list"])
            for list_kind in ("observation", "composite"):
                kwargs = {"parameters": {"profile": profile, "timeframe": horizon}, "list_kind": list_kind}
                assert project_strength_payload(compact, **kwargs) == project_strength_payload(full, **kwargs)
    assert omitted_rows > 0
    if volume_verified:
        assert retained_eligible > 0
    else:
        assert observed_watch > 0
