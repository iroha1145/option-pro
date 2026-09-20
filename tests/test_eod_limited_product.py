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
            "publish": {"ok": True},
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
    assert int(scored.get("complete_bar_n") or 0) >= 1
    assert int(scored.get("watch_n") or 0) + int(scored.get("eligible_n") or 0) >= 1
    assert scored.get("capability_flags", {}).get("volume_verified") is False


def test_seed_refuses_live_label() -> None:
    with pytest.raises(ValueError):
        seed_labeled_batch(purpose=PURPOSE_LIVE)


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
