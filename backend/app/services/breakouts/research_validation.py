"""Point-in-time labels and chronological validation for Breakout Radar research.

The functions in this module are deterministic and perform no network or database
writes.  Forward prices must be supplied as a versioned offline dataset; missing
prices stay unavailable instead of being imputed.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import date, datetime
from statistics import fmean
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo


RESEARCH_VALIDATION_VERSION = "breakout-research-validation-v1"
PRICE_DATA_SCHEMA_VERSION = "breakout-forward-prices-v1"
DEFAULT_FORWARD_HORIZONS = (1, 5, 20, 63)

_VALIDATION_LIMITATIONS = (
    "Forward returns use the triggered event price as the point-in-time entry and later "
    "completed daily closes supplied by the offline price dataset.",
    "The event price is a trigger-time mark, not a guaranteed executable fill after "
    "publication; execution-return claims require later intraday bars.",
    "The validator does not fetch missing prices, backfill delisted securities, "
    "or infer transaction costs, slippage, dividends, or survivorship controls.",
    "Baseline and same-family replacement scores are fixed inputs; no test-window "
    "result is used for model selection or parameter fitting.",
    "The stored hypothetical score is the same-family weight-replacement model. "
    "No additive model is reconstructed when its point-in-time score was not saved.",
    "External price files are treated as unverified inputs; a content hash proves "
    "repeatability after import, not the truth of the market data.",
    "A validation report is research evidence only and never enables production mode.",
)


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _positive_number(value: Any) -> float | None:
    number = _finite_number(value)
    return number if number is not None and number > 0 else None


def _parse_date(value: Any, *, field: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    try:
        return date.fromisoformat(text)
    except ValueError:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except ValueError as exc:
            raise ValueError(f"{field} must be an ISO date or datetime") from exc


def _parse_aware_datetime(value: Any, *, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field} must be an ISO datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed


def _normalize_horizons(horizons: Sequence[int]) -> tuple[int, ...]:
    if isinstance(horizons, (str, bytes)):
        raise ValueError("forward horizons must be an integer sequence")
    values: list[int] = []
    for value in horizons:
        if isinstance(value, bool):
            raise ValueError("forward horizons must be integers")
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("forward horizons must be integers") from exc
        if parsed != value:
            raise ValueError("forward horizons must be integers")
        values.append(parsed)
    normalized = tuple(sorted(values))
    if (
        not normalized
        or len(set(normalized)) != len(normalized)
        or normalized[0] < 1
        or normalized[-1] > 252
    ):
        raise ValueError("forward horizons must be unique integers between 1 and 252")
    return normalized


def _stable_hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize_price_dataset(
    dataset: Mapping[str, Any],
) -> tuple[dict[str, tuple[tuple[date, float], ...]], dict[str, Any]]:
    schema_version = str(dataset.get("schema_version") or "")
    if schema_version != PRICE_DATA_SCHEMA_VERSION:
        raise ValueError(
            f"price dataset schema_version must be {PRICE_DATA_SCHEMA_VERSION}"
        )
    source = str(dataset.get("source") or "").strip()
    adjustment = str(dataset.get("adjustment") or "").strip()
    dataset_id = str(dataset.get("dataset_id") or "").strip() or None
    timezone_name = str(dataset.get("timezone") or "").strip() or None
    calendar = str(dataset.get("calendar") or "").strip() or None
    content_sha256 = str(dataset.get("content_sha256") or "").strip().lower() or None
    if content_sha256 is not None and (
        len(content_sha256) != 64
        or any(character not in "0123456789abcdef" for character in content_sha256)
    ):
        raise ValueError("price dataset content_sha256 must be a SHA-256 hex digest")
    if not source or not adjustment:
        raise ValueError("price dataset requires non-empty source and adjustment")
    as_of = _parse_aware_datetime(dataset.get("as_of"), field="price dataset as_of")
    raw_prices = dataset.get("prices")
    if not isinstance(raw_prices, Mapping):
        raise ValueError("price dataset prices must be a ticker mapping")

    prices: dict[str, tuple[tuple[date, float], ...]] = {}
    point_count = 0
    for raw_ticker, raw_points in raw_prices.items():
        ticker = str(raw_ticker or "").strip().upper()
        if not ticker or not isinstance(raw_points, Sequence) or isinstance(
            raw_points, (str, bytes)
        ):
            raise ValueError("each price ticker must map to a sequence of points")
        if ticker in prices:
            raise ValueError(f"duplicate normalized price ticker: {ticker}")
        by_date: dict[date, float] = {}
        prior_date: date | None = None
        for raw_point in raw_points:
            if not isinstance(raw_point, Mapping):
                raise ValueError(f"price point for {ticker} must be an object")
            trading_date = _parse_date(
                raw_point.get("date"), field=f"price date for {ticker}"
            )
            close = _positive_number(raw_point.get("close"))
            if close is None:
                raise ValueError(f"price close for {ticker} must be finite and positive")
            if trading_date > as_of.date():
                raise ValueError(f"price point for {ticker} is later than dataset as_of")
            if prior_date is not None and trading_date <= prior_date:
                raise ValueError(f"price dates for {ticker} must be strictly increasing")
            if trading_date in by_date:
                raise ValueError(f"duplicate price date for {ticker}: {trading_date}")
            by_date[trading_date] = close
            prior_date = trading_date
        ordered = tuple(by_date.items())
        prices[ticker] = ordered
        point_count += len(ordered)

    return prices, {
        "schema_version": schema_version,
        "source": source,
        "adjustment": adjustment,
        "as_of": as_of.isoformat(),
        "dataset_id": dataset_id,
        "timezone": timezone_name,
        "calendar": calendar,
        "content_sha256": content_sha256,
        "trust": "external_unverified",
        "ticker_count": len(prices),
        "price_point_count": point_count,
    }


def merge_completed_research_observations(
    events: Iterable[Mapping[str, Any]],
    shadows: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Join completed snapshots and keep one first-published row per experiment."""

    event_rows = [dict(row) for row in events]
    shadow_rows = [dict(row) for row in shadows]
    event_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    duplicate_event_keys = 0
    for row in event_rows:
        key = (str(row.get("scan_run_id") or ""), str(row.get("event_id") or ""))
        if key in event_by_key:
            duplicate_event_keys += 1
            continue
        event_by_key[key] = row

    def triggered_anchor(
        event: Mapping[str, Any],
        snapshot: Mapping[str, Any],
    ) -> Any:
        if "triggered_at" in event:
            return event.get("triggered_at")
        if "triggered_at" in snapshot:
            return snapshot.get("triggered_at")
        state = str(
            event.get("lifecycle_state") or snapshot.get("lifecycle_state") or ""
        )
        if state in {"DISCOVERED", "WATCHING"}:
            return None
        return event.get("event_at") or snapshot.get("event_at")

    candidates: list[dict[str, Any]] = []
    missing_events = 0
    malformed_snapshots = 0
    for shadow in shadow_rows:
        key = (
            str(shadow.get("scan_run_id") or ""),
            str(shadow.get("event_id") or ""),
        )
        event = event_by_key.get(key)
        if event is None:
            missing_events += 1
            continue
        snapshot = event.get("event_snapshot")
        if not isinstance(snapshot, Mapping):
            malformed_snapshots += 1
            continue
        raw_shadow = shadow.get("shadow")
        shadow_payload = dict(raw_shadow) if isinstance(raw_shadow, Mapping) else {}
        feature = shadow_payload.get("feature")
        feature_payload = dict(feature) if isinstance(feature, Mapping) else {}
        snapshot_features = snapshot.get("features")
        feature_snapshot = (
            dict(snapshot_features) if isinstance(snapshot_features, Mapping) else {}
        )
        snapshot_versions = snapshot.get("versions")
        event_versions = (
            dict(snapshot_versions) if isinstance(snapshot_versions, Mapping) else {}
        )
        version = str(
            shadow.get("version") or feature_payload.get("version") or "unknown"
        )
        candidates.append(
            {
                "observation_id": f"{key[0]}:{key[1]}",
                "scan_run_id": key[0],
                "published_at": event.get("published_at"),
                "event_id": key[1],
                "ticker": str(event.get("ticker") or snapshot.get("ticker") or "")
                .strip()
                .upper(),
                "trading_date": snapshot.get("trading_date"),
                "event_at": event.get("event_at") or snapshot.get("event_at"),
                "first_seen_at": event.get("first_seen_at")
                or snapshot.get("first_seen_at"),
                "triggered_at": triggered_anchor(event, snapshot),
                "state_changed_at": event.get("state_changed_at")
                or snapshot.get("state_changed_at"),
                "last_seen_at": event.get("last_seen_at")
                or snapshot.get("last_seen_at"),
                "feature_cutoff_at": feature_snapshot.get("feature_cutoff_at"),
                "raw_as_of": feature_snapshot.get("raw_as_of"),
                "event_price": snapshot.get("event_price"),
                "source_snapshot_id": snapshot.get("source_snapshot_id"),
                "session": event.get("session") or snapshot.get("session"),
                "setup_type": event.get("setup_type") or snapshot.get("setup_type"),
                "lifecycle_state": event.get("lifecycle_state")
                or snapshot.get("lifecycle_state"),
                "production_score": shadow.get("production_score"),
                "hypothetical_score": shadow.get("hypothetical_score"),
                "production_rank": shadow.get("production_rank"),
                "hypothetical_rank": shadow.get("hypothetical_rank"),
                "rank_delta": shadow.get("rank_delta"),
                "score_version": shadow_payload.get("score_version")
                or event_versions.get("strength_score_version"),
                "range_persistence_effective_weight": shadow_payload.get(
                    "effective_weight"
                ),
                "range_persistence_contribution_cap": shadow_payload.get(
                    "contribution_cap"
                ),
                "breakout_production_priority": shadow_payload.get(
                    "breakout_production_priority"
                ),
                "breakout_hypothetical_priority": shadow_payload.get(
                    "breakout_hypothetical_priority"
                ),
                "range_persistence": feature_payload.get("range_persistence"),
                "range_persistence_slope_5d": feature_payload.get(
                    "range_persistence_slope_5d"
                ),
                "range_persistence_ratio_10d": feature_payload.get(
                    "range_persistence_ratio_10d"
                ),
                "range_persistence_status": feature_payload.get("status"),
                "range_persistence_quality": feature_payload.get("quality"),
                "range_persistence_version": version,
                "canonical_universe_version": feature_payload.get(
                    "canonical_universe_version"
                )
                or event_versions.get("universe_version"),
                "event_versions": event_versions,
            }
        )

    candidates.sort(
        key=lambda row: (
            str(row.get("published_at") or ""),
            str(row.get("trading_date") or ""),
            str(row.get("observation_id") or ""),
        )
    )
    selected_experiments: dict[tuple[str, str], dict[str, Any]] = {}
    repeated_event_rows = 0
    for row in candidates:
        event_id = str(row.get("event_id") or "")
        version = str(row.get("range_persistence_version") or "unknown")
        experiment_key = (event_id, version)
        selected = selected_experiments.get(experiment_key)
        if selected is None:
            selected_experiments[experiment_key] = row
            continue
        repeated_event_rows += 1
        if selected.get("triggered_at") in (None, "") and row.get(
            "triggered_at"
        ) not in (None, ""):
            # A WATCHING snapshot is useful descriptive evidence, but it cannot
            # anchor a post-breakout return. Prefer the first later snapshot
            # that has an actual trigger for the same versioned experiment.
            selected_experiments[experiment_key] = row

    observations = list(selected_experiments.values())

    observations.sort(
        key=lambda row: (
            str(row.get("trading_date") or ""),
            str(row.get("published_at") or ""),
            str(row.get("observation_id") or ""),
        )
    )
    versions = Counter(
        str(row.get("range_persistence_version") or "unknown")
        for row in observations
    )
    score_versions = Counter(
        str(row.get("score_version") or "unknown") for row in observations
    )
    universe_versions = Counter(
        str(row.get("canonical_universe_version") or "unknown")
        for row in observations
    )
    matched = len(candidates)
    accepted = len(observations)
    return {
        "status": "active" if accepted else "unavailable",
        "schema_version": RESEARCH_VALIDATION_VERSION,
        "observations": observations,
        "coverage": {
            "completed_event_rows": len(event_rows),
            "completed_shadow_rows": len(shadow_rows),
            "matched_shadow_rows": matched,
            "unique_experiment_observations": accepted,
            "missing_event_for_shadow": missing_events,
            "malformed_event_snapshots": malformed_snapshots,
            "duplicate_event_keys": duplicate_event_keys,
            "deduplicated_repeated_event_rows": repeated_event_rows,
            "match_ratio": (
                round(matched / len(shadow_rows), 6) if shadow_rows else 0.0
            ),
        },
        "versions": dict(sorted(versions.items())),
        "score_versions": dict(sorted(score_versions.items())),
        "canonical_universe_versions": dict(sorted(universe_versions.items())),
        "warnings": ["no_matched_completed_observations"] if not accepted else [],
    }


def _point_in_time_audit(row: Mapping[str, Any]) -> dict[str, Any]:
    parsed: dict[str, datetime] = {}
    reasons: list[str] = []
    for field in ("raw_as_of", "feature_cutoff_at", "triggered_at", "published_at"):
        value = row.get(field)
        if value in (None, ""):
            reasons.append(f"missing_{field}")
            continue
        try:
            parsed[field] = _parse_aware_datetime(value, field=field)
        except ValueError:
            reasons.append(f"invalid_{field}")
    if (
        "raw_as_of" in parsed
        and "feature_cutoff_at" in parsed
        and parsed["raw_as_of"] > parsed["feature_cutoff_at"]
    ):
        reasons.append("raw_as_of_after_feature_cutoff")
    if (
        "feature_cutoff_at" in parsed
        and "triggered_at" in parsed
        and parsed["feature_cutoff_at"] > parsed["triggered_at"]
    ):
        reasons.append("feature_cutoff_after_event")
    if (
        "triggered_at" in parsed
        and "published_at" in parsed
        and parsed["triggered_at"] > parsed["published_at"]
    ):
        reasons.append("event_after_publication")
    return {
        "status": "active" if not reasons else "unavailable",
        "reasons": reasons,
        "raw_as_of": (
            parsed["raw_as_of"].isoformat() if "raw_as_of" in parsed else None
        ),
        "feature_cutoff_at": (
            parsed["feature_cutoff_at"].isoformat()
            if "feature_cutoff_at" in parsed
            else None
        ),
        "triggered_at": (
            parsed["triggered_at"].isoformat() if "triggered_at" in parsed else None
        ),
        "published_at": (
            parsed["published_at"].isoformat() if "published_at" in parsed else None
        ),
    }


def attach_forward_return_labels(
    observations: Iterable[Mapping[str, Any]],
    price_dataset: Mapping[str, Any],
    *,
    horizons: Sequence[int] = DEFAULT_FORWARD_HORIZONS,
) -> dict[str, Any]:
    """Attach strict future-close returns to point-in-time event observations."""

    normalized_horizons = _normalize_horizons(horizons)
    prices, price_metadata = _normalize_price_dataset(price_dataset)
    rows = [dict(row) for row in observations]
    coverage = {
        str(horizon): {
            "requested": len(rows),
            "labeled": 0,
            "unavailable": 0,
            "coverage_ratio": 0.0,
        }
        for horizon in normalized_horizons
    }
    missing_reasons: Counter[str] = Counter()
    labeled_rows: list[dict[str, Any]] = []
    any_labels = 0
    fully_labeled = 0

    for raw_row in rows:
        row = dict(raw_row)
        lifecycle_state = str(row.get("lifecycle_state") or "")
        if (
            "triggered_at" not in row
            and lifecycle_state not in {"DISCOVERED", "WATCHING"}
        ):
            # Compatibility for v1 research files.  New exports always carry
            # triggered_at explicitly, including a null for untriggered rows.
            row["triggered_at"] = row.get("event_at")
        ticker = str(row.get("ticker") or "").strip().upper()
        point_in_time = _point_in_time_audit(row)
        try:
            if point_in_time["triggered_at"] is not None:
                event_date = _parse_aware_datetime(
                    point_in_time["triggered_at"],
                    field="triggered_at",
                ).astimezone(
                    ZoneInfo(
                        str(price_metadata.get("timezone") or "America/New_York")
                    )
                ).date()
            else:
                event_date = _parse_date(
                    row.get("trading_date"), field="trading_date"
                )
            date_error = None
        except ValueError:
            event_date = None
            date_error = "invalid_trading_date"
        event_price = _positive_number(row.get("event_price"))
        ticker_prices = prices.get(ticker, ())
        future_prices = (
            [point for point in ticker_prices if point[0] > event_date]
            if event_date is not None
            else []
        )
        labels: dict[str, dict[str, Any]] = {}
        active_for_row = 0
        for horizon in normalized_horizons:
            reason = date_error
            if reason is None and point_in_time["status"] != "active":
                reason = str(point_in_time["reasons"][0])
            if reason is None and not ticker:
                reason = "missing_ticker"
            if reason is None and event_price is None:
                reason = "missing_event_price"
            if reason is None and not ticker_prices:
                reason = "missing_ticker_price_history"
            if reason is None and len(future_prices) < horizon:
                reason = "incomplete_future_history"
            if reason is not None:
                labels[str(horizon)] = {
                    "status": "unavailable",
                    "reason": reason,
                    "forward_return": None,
                    "return_type": "raw_decimal_return",
                    "end_date": None,
                    "end_close": None,
                }
                coverage[str(horizon)]["unavailable"] += 1
                missing_reasons[reason] += 1
                continue
            end_date, end_close = future_prices[horizon - 1]
            forward_return = end_close / event_price - 1.0
            labels[str(horizon)] = {
                "status": "active",
                "reason": None,
                "forward_return": round(forward_return, 10),
                "return_type": "raw_decimal_return",
                "end_date": end_date.isoformat(),
                "end_close": end_close,
            }
            coverage[str(horizon)]["labeled"] += 1
            active_for_row += 1
        if active_for_row:
            any_labels += 1
        if active_for_row == len(normalized_horizons):
            fully_labeled += 1
        row["trading_date"] = event_date.isoformat() if event_date is not None else None
        row["point_in_time"] = point_in_time
        row["raw_as_of"] = point_in_time["raw_as_of"]
        row["feature_cutoff_at"] = point_in_time["feature_cutoff_at"]
        row["triggered_at"] = point_in_time["triggered_at"]
        row["published_at"] = point_in_time["published_at"]
        row["label_entry_at"] = point_in_time["triggered_at"]
        row["label_entry_price"] = event_price
        row["label_entry_type"] = "triggered_event_mark"
        row["execution_return_status"] = "unavailable"
        row["labels"] = labels
        row["label_status"] = (
            "active"
            if active_for_row == len(normalized_horizons)
            else "degraded"
            if active_for_row
            else "unavailable"
        )
        labeled_rows.append(row)

    for horizon in normalized_horizons:
        item = coverage[str(horizon)]
        item["coverage_ratio"] = (
            round(item["labeled"] / item["requested"], 6) if item["requested"] else 0.0
        )
    status = (
        "active"
        if rows and fully_labeled == len(rows)
        else "degraded"
        if any_labels
        else "unavailable"
    )
    return {
        "status": status,
        "schema_version": RESEARCH_VALIDATION_VERSION,
        "horizons": list(normalized_horizons),
        "price_data": price_metadata,
        "observations": labeled_rows,
        "coverage": {
            "observation_count": len(rows),
            "observations_with_any_label": any_labels,
            "observations_with_all_labels": fully_labeled,
            "by_horizon": coverage,
            "missing_reasons": dict(sorted(missing_reasons.items())),
        },
        "warnings": ["no_forward_returns_available"] if not any_labels else [],
    }


def _average_ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: (values[index], index))
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(order):
        end = cursor + 1
        while end < len(order) and values[order[end]] == values[order[cursor]]:
            end += 1
        rank = (cursor + 1 + end) / 2.0
        for position in range(cursor, end):
            ranks[order[position]] = rank
        cursor = end
    return ranks


def _pearson(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean = fmean(left)
    right_mean = fmean(right)
    numerator = sum(
        (left_value - left_mean) * (right_value - right_mean)
        for left_value, right_value in zip(left, right)
    )
    left_scale = math.sqrt(sum((value - left_mean) ** 2 for value in left))
    right_scale = math.sqrt(sum((value - right_mean) ** 2 for value in right))
    denominator = left_scale * right_scale
    if denominator == 0:
        return None
    return numerator / denominator


def _spearman(left: Sequence[float], right: Sequence[float]) -> float | None:
    value = _pearson(_average_ranks(left), _average_ranks(right))
    return round(value, 8) if value is not None else None


def _population_std(values: Sequence[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = fmean(values)
    return math.sqrt(fmean((value - mean) ** 2 for value in values))


def _model_metrics(
    rows: Sequence[Mapping[str, Any]],
    *,
    score_field: str,
    horizon: int,
    top_k: int,
) -> dict[str, Any]:
    grouped: dict[date, list[tuple[float, float, str]]] = defaultdict(list)
    for row in rows:
        score = _finite_number(row.get(score_field))
        labels = row.get("labels")
        label = labels.get(str(horizon), {}) if isinstance(labels, Mapping) else {}
        forward_return = (
            _finite_number(label.get("forward_return"))
            if isinstance(label, Mapping) and label.get("status") == "active"
            else None
        )
        if score is None or forward_return is None:
            continue
        trading_date = _parse_date(row.get("trading_date"), field="trading_date")
        grouped[trading_date].append(
            (score, forward_return, str(row.get("observation_id") or ""))
        )

    daily_ic: list[float] = []
    daily_top_excess: list[float] = []
    top_returns: list[float] = []
    sample_count = sum(len(items) for items in grouped.values())
    for items in grouped.values():
        if len(items) >= 2:
            ic = _spearman(
                [item[0] for item in items],
                [item[1] for item in items],
            )
            if ic is not None:
                daily_ic.append(ic)
            ranked = sorted(items, key=lambda item: (-item[0], item[2]))
            effective_k = min(top_k, max(1, len(ranked) // 2))
            selected = [item[1] for item in ranked[:effective_k]]
            universe = [item[1] for item in ranked]
            daily_top_excess.append(fmean(selected) - fmean(universe))
            top_returns.extend(selected)

    ic_mean = fmean(daily_ic) if daily_ic else None
    ic_std = _population_std(daily_ic)
    icir = (
        ic_mean / ic_std * math.sqrt(len(daily_ic))
        if ic_mean is not None and ic_std is not None and ic_std > 0
        else None
    )
    metric_available = bool(daily_ic or daily_top_excess)
    return {
        "status": "active" if metric_available else "unavailable",
        "model_id": (
            "model_a_production_baseline"
            if score_field == "production_score"
            else "model_c_range_same_family_replacement"
        ),
        "score_field": score_field,
        "sample_count": sample_count,
        "date_count": len(grouped),
        "rank_ic_date_count": len(daily_ic),
        "rank_ic_mean": round(ic_mean, 8) if ic_mean is not None else None,
        "rank_ic_std": round(ic_std, 8) if ic_std is not None else None,
        "icir": round(icir, 8) if icir is not None else None,
        "top_k_date_count": len(daily_top_excess),
        "top_k_excess_return": (
            round(fmean(daily_top_excess), 10) if daily_top_excess else None
        ),
        "top_k_hit_rate": (
            round(sum(value > 0 for value in top_returns) / len(top_returns), 8)
            if top_returns
            else None
        ),
    }


def _metric_delta(left: Any, right: Any) -> float | None:
    baseline = _finite_number(left)
    augmented = _finite_number(right)
    return (
        round(augmented - baseline, 10)
        if baseline is not None and augmented is not None
        else None
    )


def _ablation_metrics(
    rows: Sequence[Mapping[str, Any]], *, horizon: int, top_k: int
) -> dict[str, Any]:
    paired_rows: list[Mapping[str, Any]] = []
    for row in rows:
        labels = row.get("labels")
        label = labels.get(str(horizon), {}) if isinstance(labels, Mapping) else {}
        if (
            isinstance(label, Mapping)
            and label.get("status") == "active"
            and _finite_number(label.get("forward_return")) is not None
            and _finite_number(row.get("production_score")) is not None
            and _finite_number(row.get("hypothetical_score")) is not None
        ):
            paired_rows.append(row)
    baseline = _model_metrics(
        paired_rows,
        score_field="production_score",
        horizon=horizon,
        top_k=top_k,
    )
    augmented = _model_metrics(
        paired_rows,
        score_field="hypothetical_score",
        horizon=horizon,
        top_k=top_k,
    )
    return {
        "status": (
            "active"
            if baseline["status"] == "active" and augmented["status"] == "active"
            else "unavailable"
        ),
        "source_observation_count": len(rows),
        "paired_observation_count": len(paired_rows),
        "excluded_unpaired_count": len(rows) - len(paired_rows),
        "baseline": baseline,
        "additive": {
            "status": "unavailable",
            "model_id": "model_b_additive_range_persistence",
            "reason": "point_in_time_additive_score_not_stored",
        },
        "same_family_replacement": augmented,
        "same_family_replacement_minus_baseline": {
            "rank_ic_mean": _metric_delta(
                baseline.get("rank_ic_mean"), augmented.get("rank_ic_mean")
            ),
            "icir": _metric_delta(baseline.get("icir"), augmented.get("icir")),
            "top_k_excess_return": _metric_delta(
                baseline.get("top_k_excess_return"),
                augmented.get("top_k_excess_return"),
            ),
            "top_k_hit_rate": _metric_delta(
                baseline.get("top_k_hit_rate"), augmented.get("top_k_hit_rate")
            ),
        },
    }


def _selection_metric(model: Mapping[str, Any]) -> float | None:
    rank_ic = _finite_number(model.get("rank_ic_mean"))
    if rank_ic is not None:
        return rank_ic
    return _finite_number(model.get("top_k_excess_return"))


def build_walk_forward_ablation(
    labeled_observations: Iterable[Mapping[str, Any]],
    *,
    horizon: int,
    train_dates: int,
    validation_dates: int,
    test_dates: int,
    step_dates: int | None = None,
    embargo_dates: int = 1,
    minimum_rows_per_split: int = 2,
    top_k: int = 5,
) -> dict[str, Any]:
    """Evaluate fixed baseline and range scores in purged chronological windows."""

    if (
        min(
            train_dates,
            validation_dates,
            test_dates,
            minimum_rows_per_split,
            top_k,
        )
        < 1
    ):
        raise ValueError("window sizes, minimum rows, and top_k must be positive")
    if embargo_dates < 0 or embargo_dates >= min(validation_dates, test_dates):
        raise ValueError("embargo_dates must fit inside validation and test windows")
    step = int(step_dates if step_dates is not None else test_dates)
    if step < 1:
        raise ValueError("step_dates must be positive")

    normalized_horizon = _normalize_horizons((horizon,))[0]
    source_rows = [dict(row) for row in labeled_observations]
    eligible: list[dict[str, Any]] = []
    eligibility_reasons: Counter[str] = Counter()
    for row in source_rows:
        labels = row.get("labels")
        label = (
            labels.get(str(normalized_horizon), {})
            if isinstance(labels, Mapping)
            else {}
        )
        if not isinstance(label, Mapping) or label.get("status") != "active":
            eligibility_reasons["label_unavailable"] += 1
            continue
        if (
            _finite_number(label.get("forward_return")) is None
            or not label.get("end_date")
        ):
            eligibility_reasons["invalid_active_label"] += 1
            continue
        if (
            _finite_number(row.get("production_score")) is None
            or _finite_number(row.get("hypothetical_score")) is None
        ):
            eligibility_reasons["unpaired_model_scores"] += 1
            continue
        try:
            event_date = _parse_date(row.get("trading_date"), field="trading_date")
            label_end_date = _parse_date(
                label.get("end_date"), field="label end_date"
            )
        except ValueError:
            eligibility_reasons["invalid_label_dates"] += 1
            continue
        if label_end_date <= event_date:
            eligibility_reasons["non_forward_label_end"] += 1
            continue
        copy = dict(row)
        copy["_event_date"] = event_date
        copy["_label_end_date"] = label_end_date
        eligible.append(copy)

    ordered_dates = sorted({row["_event_date"] for row in eligible})
    required_dates = train_dates + validation_dates + test_dates
    windows: list[dict[str, Any]] = []
    start = 0
    while start + required_dates <= len(ordered_dates):
        train_raw_dates = ordered_dates[start : start + train_dates]
        validation_raw_dates = ordered_dates[
            start + train_dates : start + train_dates + validation_dates
        ]
        test_raw_dates = ordered_dates[
            start + train_dates + validation_dates : start + required_dates
        ]
        validation_used_dates = validation_raw_dates[embargo_dates:]
        test_used_dates = test_raw_dates[embargo_dates:]
        validation_start = validation_used_dates[0]
        test_start = test_used_dates[0]

        train_raw = [row for row in eligible if row["_event_date"] in train_raw_dates]
        validation_raw = [
            row for row in eligible if row["_event_date"] in validation_raw_dates
        ]
        test_raw = [row for row in eligible if row["_event_date"] in test_raw_dates]
        train_used = [
            row for row in train_raw if row["_label_end_date"] < validation_start
        ]
        validation_after_embargo = [
            row for row in validation_raw if row["_event_date"] in validation_used_dates
        ]
        validation_used = [
            row
            for row in validation_after_embargo
            if row["_label_end_date"] < test_start
        ]
        test_used = [row for row in test_raw if row["_event_date"] in test_used_dates]

        train_metrics = _ablation_metrics(
            train_used, horizon=normalized_horizon, top_k=top_k
        )
        validation_metrics = _ablation_metrics(
            validation_used, horizon=normalized_horizon, top_k=top_k
        )
        test_metrics = _ablation_metrics(
            test_used, horizon=normalized_horizon, top_k=top_k
        )
        baseline_selection = _selection_metric(validation_metrics["baseline"])
        augmented_selection = _selection_metric(
            validation_metrics["same_family_replacement"]
        )
        selected_model = (
            "same_family_replacement"
            if baseline_selection is not None
            and augmented_selection is not None
            and augmented_selection > baseline_selection
            else "baseline"
            if baseline_selection is not None and augmented_selection is not None
            else None
        )
        split_counts_sufficient = all(
            len(rows) >= minimum_rows_per_split
            for rows in (train_used, validation_used, test_used)
        )
        leakage_free = (
            all(row["_label_end_date"] < validation_start for row in train_used)
            and all(row["_label_end_date"] < test_start for row in validation_used)
        )
        window_status = (
            "active"
            if split_counts_sufficient
            and leakage_free
            and train_metrics["status"] == "active"
            and validation_metrics["status"] == "active"
            and test_metrics["status"] == "active"
            and selected_model is not None
            else "unavailable"
        )
        windows.append(
            {
                "window_id": len(windows) + 1,
                "status": window_status,
                "horizon": normalized_horizon,
                "dates": {
                    "train": [
                        train_raw_dates[0].isoformat(),
                        train_raw_dates[-1].isoformat(),
                    ],
                    "validation_raw": [
                        validation_raw_dates[0].isoformat(),
                        validation_raw_dates[-1].isoformat(),
                    ],
                    "validation": [
                        validation_used_dates[0].isoformat(),
                        validation_used_dates[-1].isoformat(),
                    ],
                    "test_raw": [
                        test_raw_dates[0].isoformat(),
                        test_raw_dates[-1].isoformat(),
                    ],
                    "test": [
                        test_used_dates[0].isoformat(),
                        test_used_dates[-1].isoformat(),
                    ],
                },
                "audit": {
                    "raw_rows": {
                        "train": len(train_raw),
                        "validation": len(validation_raw),
                        "test": len(test_raw),
                    },
                    "used_rows": {
                        "train": len(train_used),
                        "validation": len(validation_used),
                        "test": len(test_used),
                    },
                    "purged_rows": {
                        "train": len(train_raw) - len(train_used),
                        "validation": len(validation_after_embargo)
                        - len(validation_used),
                    },
                    "embargoed_rows": {
                        "validation": len(validation_raw)
                        - len(validation_after_embargo),
                        "test": len(test_raw) - len(test_used),
                    },
                    "embargo_dates": embargo_dates,
                    "embargoed_validation_dates": [
                        value.isoformat()
                        for value in validation_raw_dates[:embargo_dates]
                    ],
                    "embargoed_test_dates": [
                        value.isoformat() for value in test_raw_dates[:embargo_dates]
                    ],
                    "validation_start": validation_start.isoformat(),
                    "test_start": test_start.isoformat(),
                    "maximum_train_label_end": (
                        max(row["_label_end_date"] for row in train_used).isoformat()
                        if train_used
                        else None
                    ),
                    "maximum_validation_label_end": (
                        max(
                            row["_label_end_date"] for row in validation_used
                        ).isoformat()
                        if validation_used
                        else None
                    ),
                    "leakage_check": "passed" if leakage_free else "failed",
                },
                "train": train_metrics,
                "validation": validation_metrics,
                "test": test_metrics,
                "selection": {
                    "status": "active" if selected_model else "unavailable",
                    "selected_on": "validation_only",
                    "selected_model": selected_model,
                    "test_metrics": (
                        test_metrics.get(selected_model) if selected_model else None
                    ),
                },
            }
        )
        start += step

    active_windows = [window for window in windows if window["status"] == "active"]
    test_ic_deltas = [
        _finite_number(
            window["test"]["same_family_replacement_minus_baseline"].get(
                "rank_ic_mean"
            )
        )
        for window in active_windows
    ]
    test_ic_deltas = [value for value in test_ic_deltas if value is not None]
    configuration = {
        "train_dates": train_dates,
        "validation_dates": validation_dates,
        "test_dates": test_dates,
        "step_dates": step,
        "embargo_dates": embargo_dates,
        "minimum_rows_per_split": minimum_rows_per_split,
        "top_k": top_k,
        "purge_rule": "label_end_date_strictly_before_next_used_split_start",
    }
    return {
        "status": "active" if active_windows else "unavailable",
        "schema_version": RESEARCH_VALIDATION_VERSION,
        "horizon": normalized_horizon,
        "configuration": configuration,
        "configuration_hash": _stable_hash(configuration),
        "coverage": {
            "source_observations": len(source_rows),
            "eligible_labeled_observations": len(eligible),
            "eligible_dates": len(ordered_dates),
            "first_eligible_date": (
                ordered_dates[0].isoformat() if ordered_dates else None
            ),
            "last_eligible_date": (
                ordered_dates[-1].isoformat() if ordered_dates else None
            ),
            "window_count": len(windows),
            "active_window_count": len(active_windows),
            "ineligible_reasons": dict(sorted(eligibility_reasons.items())),
        },
        "aggregate": {
            "test_rank_ic_delta_window_count": len(test_ic_deltas),
            "mean_test_rank_ic_delta": (
                round(fmean(test_ic_deltas), 10) if test_ic_deltas else None
            ),
            "positive_test_rank_ic_windows": sum(value > 0 for value in test_ic_deltas),
            "majority_positive": (
                sum(value > 0 for value in test_ic_deltas) > len(test_ic_deltas) / 2
                if test_ic_deltas
                else None
            ),
        },
        "windows": windows,
        "warnings": (
            ["insufficient_chronological_windows"] if not active_windows else []
        ),
    }


def run_range_persistence_validation(
    events: Iterable[Mapping[str, Any]],
    shadows: Iterable[Mapping[str, Any]],
    price_dataset: Mapping[str, Any],
    *,
    horizons: Sequence[int] = DEFAULT_FORWARD_HORIZONS,
    train_dates: int = 60,
    validation_dates: int = 20,
    test_dates: int = 20,
    step_dates: int | None = None,
    embargo_dates: int = 1,
    minimum_rows_per_split: int = 10,
    top_k: int = 5,
) -> dict[str, Any]:
    """Build labels and purged walk-forward ablations for completed snapshots."""

    normalized_horizons = _normalize_horizons(horizons)
    merged = merge_completed_research_observations(events, shadows)
    labeled = attach_forward_return_labels(
        merged["observations"], price_dataset, horizons=normalized_horizons
    )
    horizon_reports = {
        str(horizon): build_walk_forward_ablation(
            labeled["observations"],
            horizon=horizon,
            train_dates=train_dates,
            validation_dates=validation_dates,
            test_dates=test_dates,
            step_dates=step_dates,
            embargo_dates=embargo_dates,
            minimum_rows_per_split=minimum_rows_per_split,
            top_k=top_k,
        )
        for horizon in normalized_horizons
    }
    active_horizons = sum(
        report["status"] == "active" for report in horizon_reports.values()
    )
    configuration = {
        "horizons": list(normalized_horizons),
        "train_dates": train_dates,
        "validation_dates": validation_dates,
        "test_dates": test_dates,
        "step_dates": step_dates if step_dates is not None else test_dates,
        "embargo_dates": embargo_dates,
        "minimum_rows_per_split": minimum_rows_per_split,
        "top_k": top_k,
        "partition_unit": "trading_date",
        "random_split": False,
    }
    research_fingerprint = {
        **configuration,
        "validation_version": RESEARCH_VALIDATION_VERSION,
        "price_schema_version": labeled["price_data"]["schema_version"],
        "price_content_sha256": labeled["price_data"].get("content_sha256"),
        "baseline_model_id": "model_a_production_baseline",
        "replacement_model_id": "model_c_range_same_family_replacement",
    }
    warnings = [
        *merged.get("warnings", []),
        *labeled.get("warnings", []),
    ]
    if not active_horizons:
        warnings.append("no_evaluable_walk_forward_horizon")
    if labeled["price_data"].get("content_sha256") is None:
        warnings.append("price_dataset_content_hash_unavailable")
    warnings.append("production_mode_remains_shadow")
    baseline_available = any(
        _finite_number(row.get("production_score")) is not None
        for row in labeled["observations"]
    )
    replacement_available = any(
        _finite_number(row.get("hypothetical_score")) is not None
        for row in labeled["observations"]
    )
    trading_dates = sorted(
        str(row["trading_date"])
        for row in labeled["observations"]
        if row.get("trading_date")
    )
    published_times = sorted(
        str(row["published_at"])
        for row in labeled["observations"]
        if row.get("published_at")
    )
    return {
        "status": "active" if active_horizons else "unavailable",
        "analysis_type": "purged_walk_forward_range_persistence_ablation",
        "schema_version": RESEARCH_VALIDATION_VERSION,
        "decision_status": "insufficient_for_production_decision",
        "production_mode_recommendation": "shadow",
        "configuration": configuration,
        "research_config_hash": _stable_hash(research_fingerprint),
        "sample_window": {
            "first_trading_date": trading_dates[0] if trading_dates else None,
            "last_trading_date": trading_dates[-1] if trading_dates else None,
            "first_published_at": published_times[0] if published_times else None,
            "last_published_at": published_times[-1] if published_times else None,
            "price_as_of": labeled["price_data"]["as_of"],
        },
        "price_data": labeled["price_data"],
        "versions": {
            "validation": RESEARCH_VALIDATION_VERSION,
            "range_persistence": merged.get("versions", {}),
            "strength_score": merged.get("score_versions", {}),
            "canonical_universe": merged.get(
                "canonical_universe_versions", {}
            ),
            "price_data": labeled["price_data"]["schema_version"],
        },
        "models": {
            "baseline": {
                "status": "active" if baseline_available else "unavailable",
                "attachment_model": "A",
                "model_id": "model_a_production_baseline",
                "score_field": "production_score",
            },
            "additive": {
                "status": "unavailable",
                "attachment_model": "B",
                "model_id": "model_b_additive_range_persistence",
                "reason": "point_in_time_additive_score_not_stored",
            },
            "same_family_replacement": {
                "status": "active" if replacement_available else "unavailable",
                "attachment_model": "C",
                "model_id": "model_c_range_same_family_replacement",
                "score_field": "hypothetical_score",
            },
        },
        "coverage": {
            "merge": merged["coverage"],
            "labels": labeled["coverage"],
            "active_horizons": active_horizons,
            "requested_horizons": len(normalized_horizons),
        },
        "horizons": horizon_reports,
        "observations": labeled["observations"],
        "warnings": list(dict.fromkeys(warnings)),
        "limitations": list(_VALIDATION_LIMITATIONS),
    }
