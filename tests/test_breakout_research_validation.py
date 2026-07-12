from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pytest

from app.services.breakouts.research_validation import (
    PRICE_DATA_SCHEMA_VERSION,
    attach_forward_return_labels,
    build_walk_forward_ablation,
    merge_completed_research_observations,
    run_range_persistence_validation,
)


def _business_dates(start: date, count: int) -> list[date]:
    values: list[date] = []
    cursor = start
    while len(values) < count:
        if cursor.weekday() < 5:
            values.append(cursor)
        cursor += timedelta(days=1)
    return values


def _price_dataset(prices: dict[str, list[dict]]) -> dict:
    return {
        "schema_version": PRICE_DATA_SCHEMA_VERSION,
        "dataset_id": "fixture-prices",
        "source": "fixture",
        "adjustment": "unadjusted",
        "timezone": "America/New_York",
        "calendar": "XNYS",
        "as_of": "2026-12-31T23:59:59Z",
        "content_sha256": "a" * 64,
        "prices": prices,
    }


def _observation(
    trading_date: date,
    *,
    ticker: str = "AAPL",
    event_price: float | None = 100.0,
    production_score: float = 50.0,
    hypothetical_score: float = 51.0,
) -> dict:
    event_at = datetime.combine(
        trading_date, time(15, 0), tzinfo=timezone.utc
    )
    return {
        "observation_id": f"{trading_date}:{ticker}",
        "ticker": ticker,
        "trading_date": trading_date.isoformat(),
        "raw_as_of": event_at.isoformat(),
        "feature_cutoff_at": event_at.isoformat(),
        "event_at": event_at.isoformat(),
        "published_at": (event_at + timedelta(minutes=1)).isoformat(),
        "event_price": event_price,
        "production_score": production_score,
        "hypothetical_score": hypothetical_score,
    }


def test_forward_labels_use_strict_future_sessions_for_1_5_20_63_days():
    event_date = date(2026, 1, 2)
    future_dates = _business_dates(date(2026, 1, 5), 63)
    prices = [{"date": event_date.isoformat(), "close": 999.0}]
    prices.extend(
        {"date": value.isoformat(), "close": 100.0 + index}
        for index, value in enumerate(future_dates, 1)
    )

    result = attach_forward_return_labels(
        [_observation(event_date)],
        _price_dataset({"AAPL": prices}),
    )

    row = result["observations"][0]
    assert result["status"] == "active"
    assert row["point_in_time"]["status"] == "active"
    assert row["label_entry_price"] == 100
    assert row["labels"]["1"]["forward_return"] == pytest.approx(0.01)
    assert row["labels"]["5"]["forward_return"] == pytest.approx(0.05)
    assert row["labels"]["20"]["forward_return"] == pytest.approx(0.20)
    assert row["labels"]["63"]["forward_return"] == pytest.approx(0.63)
    assert row["labels"]["1"]["end_date"] == future_dates[0].isoformat()
    assert row["labels"]["20"]["end_date"] == future_dates[19].isoformat()
    assert row["labels"]["63"]["end_date"] == future_dates[-1].isoformat()
    assert result["coverage"]["by_horizon"]["20"]["coverage_ratio"] == 1


def test_appending_prices_after_label_horizon_does_not_change_old_labels():
    event_date = date(2026, 1, 2)
    future_dates = _business_dates(date(2026, 1, 5), 25)
    points = [
        {"date": value.isoformat(), "close": 100 + index}
        for index, value in enumerate(future_dates, 1)
    ]

    original = attach_forward_return_labels(
        [_observation(event_date)],
        _price_dataset({"AAPL": points[:20]}),
    )
    extended = attach_forward_return_labels(
        [_observation(event_date)],
        _price_dataset({"AAPL": points}),
    )

    assert original["observations"][0]["labels"] == extended["observations"][0][
        "labels"
    ]


def test_missing_or_temporally_invalid_labels_stay_unavailable_without_zero_fill():
    event_date = date(2026, 1, 2)
    invalid = _observation(event_date)
    invalid["feature_cutoff_at"] = "2026-01-02T16:00:00Z"
    missing_history = _observation(event_date, ticker="MSFT")

    result = attach_forward_return_labels(
        [invalid, missing_history],
        _price_dataset(
            {
                "AAPL": [
                    {"date": "2026-01-05", "close": 101.0},
                ]
            }
        ),
    )

    assert result["status"] == "unavailable"
    first, second = result["observations"]
    assert first["point_in_time"]["status"] == "unavailable"
    assert first["labels"]["1"] == {
        "status": "unavailable",
        "reason": "feature_cutoff_after_event",
        "forward_return": None,
        "return_type": "raw_decimal_return",
        "end_date": None,
        "end_close": None,
    }
    assert second["labels"]["1"]["reason"] == "missing_ticker_price_history"
    assert second["labels"]["1"]["forward_return"] is None
    assert result["coverage"]["by_horizon"]["1"]["labeled"] == 0


def test_price_dataset_rejects_unsorted_or_duplicate_session_dates():
    observations = [_observation(date(2026, 1, 2))]
    dataset = _price_dataset(
        {
            "AAPL": [
                {"date": "2026-01-06", "close": 102.0},
                {"date": "2026-01-05", "close": 101.0},
            ]
        }
    )

    with pytest.raises(ValueError, match="strictly increasing"):
        attach_forward_return_labels(observations, dataset)


def test_merge_keeps_first_completed_event_per_feature_version():
    event_date = date(2026, 1, 2)
    event_at = "2026-01-02T15:00:00Z"

    def event(scan_run_id: str, published_at: str) -> dict:
        return {
            "scan_run_id": scan_run_id,
            "published_at": published_at,
            "event_id": "event-1",
            "ticker": "AAPL",
            "event_at": event_at,
            "event_snapshot": {
                "ticker": "AAPL",
                "trading_date": event_date.isoformat(),
                "event_at": event_at,
                "event_price": 100,
                "source_snapshot_id": "source-1",
                "features": {
                    "raw_as_of": event_at,
                    "feature_cutoff_at": event_at,
                },
                "versions": {"universe_version": "universe-v1"},
            },
        }

    def shadow(scan_run_id: str, version: str) -> dict:
        return {
            "scan_run_id": scan_run_id,
            "event_id": "event-1",
            "ticker": "AAPL",
            "production_score": 50,
            "hypothetical_score": 52,
            "version": version,
            "shadow": {
                "score_version": "strength-v1",
                "feature": {
                    "status": "active",
                    "range_persistence": 70,
                    "version": version,
                },
            },
        }

    result = merge_completed_research_observations(
        [
            event("scan-2", "2026-01-02T15:02:00Z"),
            event("scan-1", "2026-01-02T15:01:00Z"),
            event("scan-3", "2026-01-02T15:03:00Z"),
        ],
        [
            shadow("scan-2", "range-v1"),
            shadow("scan-1", "range-v1"),
            shadow("scan-3", "range-v2"),
        ],
    )

    assert [row["scan_run_id"] for row in result["observations"]] == [
        "scan-1",
        "scan-3",
    ]
    assert result["coverage"]["matched_shadow_rows"] == 3
    assert result["coverage"]["unique_experiment_observations"] == 2
    assert result["coverage"]["deduplicated_repeated_event_rows"] == 1
    assert result["score_versions"] == {"strength-v1": 2}


def _labeled_rows_for_walk_forward() -> tuple[list[dict], list[date]]:
    dates = _business_dates(date(2026, 1, 2), 35)
    tickers = ("AAA", "BBB", "CCC")
    returns = (-0.02, 0.0, 0.02)
    rows: list[dict] = []
    for index, trading_date in enumerate(dates[:30]):
        for ticker_index, ticker in enumerate(tickers):
            row = _observation(
                trading_date,
                ticker=ticker,
                production_score=float(3 - ticker_index),
                hypothetical_score=float(ticker_index + 1),
            )
            row["labels"] = {
                "5": {
                    "status": "active",
                    "reason": None,
                    "forward_return": returns[ticker_index],
                    "end_date": dates[index + 5].isoformat(),
                    "end_close": 100 * (1 + returns[ticker_index]),
                }
            }
            rows.append(row)
    return rows, dates


def test_walk_forward_purges_overlap_embargoes_dates_and_uses_validation_only():
    rows, dates = _labeled_rows_for_walk_forward()

    result = build_walk_forward_ablation(
        rows,
        horizon=5,
        train_dates=10,
        validation_dates=8,
        test_dates=8,
        step_dates=8,
        embargo_dates=1,
        minimum_rows_per_split=2,
        top_k=1,
    )

    assert result["status"] == "active"
    window = result["windows"][0]
    assert window["status"] == "active"
    assert window["audit"]["leakage_check"] == "passed"
    assert window["audit"]["purged_rows"] == {"train": 12, "validation": 12}
    assert window["audit"]["embargoed_rows"] == {"validation": 3, "test": 3}
    assert window["audit"]["maximum_train_label_end"] < window["audit"][
        "validation_start"
    ]
    assert window["audit"]["maximum_validation_label_end"] < window["audit"][
        "test_start"
    ]
    assert window["dates"]["validation"][0] == dates[11].isoformat()
    assert window["dates"]["test"][0] == dates[19].isoformat()
    assert window["selection"] == {
        "status": "active",
        "selected_on": "validation_only",
        "selected_model": "same_family_replacement",
        "test_metrics": window["test"]["same_family_replacement"],
    }
    assert window["test"]["baseline"]["rank_ic_mean"] == -1
    assert window["test"]["same_family_replacement"]["rank_ic_mean"] == 1
    assert window["test"]["same_family_replacement_minus_baseline"][
        "rank_ic_mean"
    ] == 2
    assert result["aggregate"]["majority_positive"] is True


def test_changing_test_returns_does_not_change_validation_model_selection():
    rows, dates = _labeled_rows_for_walk_forward()
    original = build_walk_forward_ablation(
        rows,
        horizon=5,
        train_dates=10,
        validation_dates=8,
        test_dates=8,
        step_dates=8,
        embargo_dates=1,
        minimum_rows_per_split=2,
        top_k=1,
    )
    for row in rows:
        if row["trading_date"] >= dates[19].isoformat():
            row["labels"]["5"]["forward_return"] *= -1
    changed = build_walk_forward_ablation(
        rows,
        horizon=5,
        train_dates=10,
        validation_dates=8,
        test_dates=8,
        step_dates=8,
        embargo_dates=1,
        minimum_rows_per_split=2,
        top_k=1,
    )

    assert original["windows"][0]["selection"]["selected_model"] == (
        changed["windows"][0]["selection"]["selected_model"]
    )
    assert original["windows"][0]["selection"]["selected_model"] == (
        "same_family_replacement"
    )
    assert original["windows"][0]["test"][
        "same_family_replacement_minus_baseline"
    ] != changed["windows"][0]["test"][
        "same_family_replacement_minus_baseline"
    ]


def test_walk_forward_requires_paired_scores_and_never_fabricates_metrics():
    row = _observation(date(2026, 1, 2))
    row["hypothetical_score"] = None
    row["labels"] = {
        "1": {
            "status": "active",
            "forward_return": 0.01,
            "end_date": "2026-01-05",
        }
    }

    result = build_walk_forward_ablation(
        [row],
        horizon=1,
        train_dates=1,
        validation_dates=2,
        test_dates=2,
        embargo_dates=1,
        minimum_rows_per_split=1,
        top_k=1,
    )

    assert result["status"] == "unavailable"
    assert result["coverage"]["eligible_labeled_observations"] == 0
    assert result["coverage"]["ineligible_reasons"] == {
        "unpaired_model_scores": 1
    }
    assert result["aggregate"]["mean_test_rank_ic_delta"] is None
    assert result["aggregate"]["majority_positive"] is None


def test_full_report_keeps_shadow_mode_when_history_cannot_form_windows():
    event_at = "2026-01-02T15:00:00Z"
    events = [
        {
            "scan_run_id": "scan-1",
            "published_at": "2026-01-02T15:01:00Z",
            "event_id": "event-1",
            "ticker": "AAPL",
            "event_at": event_at,
            "event_snapshot": {
                "event_id": "event-1",
                "ticker": "AAPL",
                "trading_date": "2026-01-02",
                "event_at": event_at,
                "event_price": 100,
                "source_snapshot_id": "source-1",
                "features": {
                    "raw_as_of": event_at,
                    "feature_cutoff_at": event_at,
                },
            },
        }
    ]
    shadows = [
        {
            "scan_run_id": "scan-1",
            "event_id": "event-1",
            "ticker": "AAPL",
            "production_score": 50,
            "hypothetical_score": 52,
            "version": "range-v1",
            "shadow": {
                "feature": {
                    "status": "active",
                    "range_persistence": 70,
                    "version": "range-v1",
                }
            },
        }
    ]
    future_dates = _business_dates(date(2026, 1, 5), 20)
    prices = [
        {"date": value.isoformat(), "close": 100 + index}
        for index, value in enumerate(future_dates, 1)
    ]

    report = run_range_persistence_validation(
        events,
        shadows,
        _price_dataset({"AAPL": prices}),
        train_dates=1,
        validation_dates=2,
        test_dates=2,
        embargo_dates=1,
        minimum_rows_per_split=1,
        top_k=1,
    )

    assert report["status"] == "unavailable"
    assert report["coverage"]["labels"]["by_horizon"]["20"]["labeled"] == 1
    assert report["coverage"]["active_horizons"] == 0
    assert report["models"]["baseline"]["attachment_model"] == "A"
    assert report["models"]["additive"] == {
        "status": "unavailable",
        "attachment_model": "B",
        "model_id": "model_b_additive_range_persistence",
        "reason": "point_in_time_additive_score_not_stored",
    }
    assert report["models"]["same_family_replacement"]["attachment_model"] == "C"
    assert report["coverage"]["requested_horizons"] == 4
    assert report["coverage"]["labels"]["by_horizon"]["63"]["labeled"] == 0
    assert report["decision_status"] == "insufficient_for_production_decision"
    assert report["production_mode_recommendation"] == "shadow"
    assert report["research_config_hash"]
    assert "production_mode_remains_shadow" in report["warnings"]


def test_research_documentation_states_leakage_and_shadow_boundaries():
    content = Path("docs/breakout-radar/research-validation.md").read_text(
        encoding="utf-8"
    )

    assert "1、5、20、63" in content
    assert "Walk-Forward Validation" in content
    assert "Purge" in content
    assert "Embargo" in content
    assert "external_unverified" in content
    assert "production_mode_recommendation" in content
    assert "shadow" in content
