"""Read-only exports and descriptive shadow statistics for Breakout Radar.

The research utility deliberately reads only scan rows whose publication state
is ``completed``.  It opens SQLite through :class:`BreakoutRepository` in
read-only/query-only mode and never initializes or migrates the production
database.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import tempfile
from collections import Counter
from itertools import combinations_with_replacement
from pathlib import Path
from statistics import fmean, median
from typing import Any, Iterable, Mapping, Sequence

from .repository import BreakoutRepository


EVENT_FIELDS = (
    "scan_run_id",
    "published_at",
    "scan_session",
    "provider",
    "rank",
    "event_id",
    "ticker",
    "session",
    "setup_type",
    "lifecycle_state",
    "event_at",
    "alert_priority_score",
    "event_snapshot",
)

SHADOW_FIELDS = (
    "scan_run_id",
    "published_at",
    "event_id",
    "ticker",
    "production_score",
    "hypothetical_score",
    "production_rank",
    "hypothetical_rank",
    "rank_delta",
    "version",
    "shadow",
)

CORRELATION_FIELDS = (
    "production_score",
    "hypothetical_score",
    "score_delta",
    "production_rank",
    "hypothetical_rank",
    "rank_delta",
    "range_position",
    "range_persistence_fast",
    "range_persistence_slow",
    "range_persistence",
    "range_persistence_slope_5d",
    "range_persistence_ratio_10d",
    "range_persistence_self_percentile",
    "range_persistence_global_percentile",
    "range_persistence_sector_percentile",
    "legacy_control",
)

CORRELATION_EXPORT_FIELDS = (
    "left_field",
    "right_field",
    "paired_count",
    "pearson",
)

_LIMITATIONS = (
    "Shadow observations are descriptive and do not establish causality or future returns.",
    (
        "The same event may appear in more than one completed scan, so observations "
        "are not independent."
    ),
    "No outcome labels, transaction costs, slippage, or survivorship controls are included here.",
    (
        "A production decision still requires chronological walk-forward validation "
        "with purge and embargo."
    ),
)


def _decode_json(value: str | None) -> Any:
    return json.loads(value) if value is not None else None


def load_completed_events(database_path: str | Path) -> list[dict[str, Any]]:
    """Return event snapshots attached to completed, atomically published scans."""
    repository = BreakoutRepository(database_path, read_only=True)
    connection = repository.open_read_connection()
    try:
        rows = connection.execute(
            """
            SELECT
                runs.scan_run_id,
                runs.published_at,
                runs.session AS scan_session,
                runs.provider,
                events.rank,
                events.event_id,
                events.ticker,
                events.session,
                events.setup_type,
                events.lifecycle_state,
                events.event_at,
                events.alert_priority_score,
                events.event_snapshot_json
            FROM breakout_scan_events AS events
            INNER JOIN breakout_scan_runs AS runs
                ON runs.scan_run_id = events.scan_run_id
               AND runs.status = 'completed'
               AND runs.published_at IS NOT NULL
            ORDER BY runs.published_at, runs.scan_run_id, events.rank, events.event_id
            """
        ).fetchall()
        return [
            {
                "scan_run_id": row["scan_run_id"],
                "published_at": row["published_at"],
                "scan_session": row["scan_session"],
                "provider": row["provider"],
                "rank": row["rank"],
                "event_id": row["event_id"],
                "ticker": row["ticker"],
                "session": row["session"],
                "setup_type": row["setup_type"],
                "lifecycle_state": row["lifecycle_state"],
                "event_at": row["event_at"],
                "alert_priority_score": row["alert_priority_score"],
                "event_snapshot": _decode_json(row["event_snapshot_json"]),
            }
            for row in rows
        ]
    finally:
        connection.close()


def load_completed_shadows(database_path: str | Path) -> list[dict[str, Any]]:
    """Return Range Persistence shadow rows from completed scans only."""
    repository = BreakoutRepository(database_path, read_only=True)
    connection = repository.open_read_connection()
    try:
        rows = connection.execute(
            """
            SELECT
                runs.scan_run_id,
                runs.published_at,
                shadows.event_id,
                shadows.ticker,
                shadows.production_score,
                shadows.hypothetical_score,
                shadows.production_rank,
                shadows.hypothetical_rank,
                shadows.rank_delta,
                shadows.version,
                shadows.shadow_json
            FROM range_persistence_shadow AS shadows
            INNER JOIN breakout_scan_runs AS runs
                ON runs.scan_run_id = shadows.scan_run_id
               AND runs.status = 'completed'
               AND runs.published_at IS NOT NULL
            ORDER BY runs.published_at, runs.scan_run_id, shadows.event_id
            """
        ).fetchall()
        return [
            {
                "scan_run_id": row["scan_run_id"],
                "published_at": row["published_at"],
                "event_id": row["event_id"],
                "ticker": row["ticker"],
                "production_score": row["production_score"],
                "hypothetical_score": row["hypothetical_score"],
                "production_rank": row["production_rank"],
                "hypothetical_rank": row["hypothetical_rank"],
                "rank_delta": row["rank_delta"],
                "version": row["version"],
                "shadow": _decode_json(row["shadow_json"]),
            }
            for row in rows
        ]
    finally:
        connection.close()


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _pearson(pairs: Sequence[tuple[float, float]]) -> float | None:
    if len(pairs) < 2:
        return None
    left = [item[0] for item in pairs]
    right = [item[1] for item in pairs]
    left_mean = fmean(left)
    right_mean = fmean(right)
    numerator = sum(
        (left_value - left_mean) * (right_value - right_mean)
        for left_value, right_value in pairs
    )
    left_scale = math.sqrt(sum((value - left_mean) ** 2 for value in left))
    right_scale = math.sqrt(sum((value - right_mean) ** 2 for value in right))
    denominator = left_scale * right_scale
    if denominator == 0:
        return None
    return round(numerator / denominator, 6)


def summarize_shadows(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Build a descriptive summary without making performance claims."""
    rows = [dict(record) for record in records]
    score_pairs: list[tuple[float, float]] = []
    score_deltas: list[float] = []
    rank_deltas: list[int] = []
    for row in rows:
        production_score = _finite_number(row.get("production_score"))
        hypothetical_score = _finite_number(row.get("hypothetical_score"))
        if production_score is not None and hypothetical_score is not None:
            score_pairs.append((production_score, hypothetical_score))
            score_deltas.append(hypothetical_score - production_score)
        rank_delta = _finite_number(row.get("rank_delta"))
        if rank_delta is not None and rank_delta.is_integer():
            rank_deltas.append(int(rank_delta))

    published = sorted(
        str(row["published_at"])
        for row in rows
        if row.get("published_at") is not None
    )
    versions = Counter(str(row.get("version") or "unknown") for row in rows)
    changed_ranks = sum(delta != 0 for delta in rank_deltas)

    return {
        "analysis_type": "descriptive_shadow_only",
        "decision_status": "insufficient_for_production_decision",
        "observation_count": len(rows),
        "completed_scan_count": len(
            {str(row["scan_run_id"]) for row in rows if row.get("scan_run_id")}
        ),
        "unique_event_count": len(
            {str(row["event_id"]) for row in rows if row.get("event_id")}
        ),
        "unique_ticker_count": len(
            {str(row["ticker"]) for row in rows if row.get("ticker")}
        ),
        "period": {
            "first_published_at": published[0] if published else None,
            "last_published_at": published[-1] if published else None,
        },
        "versions": dict(sorted(versions.items())),
        "score_comparison": {
            "paired_count": len(score_pairs),
            "mean_hypothetical_minus_production": (
                round(fmean(score_deltas), 6) if score_deltas else None
            ),
            "median_hypothetical_minus_production": (
                round(float(median(score_deltas)), 6) if score_deltas else None
            ),
            "production_hypothetical_pearson": _pearson(score_pairs),
        },
        "rank_comparison": {
            "paired_count": len(rank_deltas),
            "changed_count": changed_ranks,
            "changed_rate": (
                round(changed_ranks / len(rank_deltas), 6) if rank_deltas else None
            ),
            "mean_absolute_delta": (
                round(fmean(abs(delta) for delta in rank_deltas), 6)
                if rank_deltas
                else None
            ),
            "median_absolute_delta": (
                round(float(median(abs(delta) for delta in rank_deltas)), 6)
                if rank_deltas
                else None
            ),
        },
        "limitations": list(_LIMITATIONS),
    }


def _correlation_value(row: Mapping[str, Any], field: str) -> float | None:
    if field == "score_delta":
        explicit = _finite_number(row.get(field))
        if explicit is not None:
            return explicit
        production = _finite_number(row.get("production_score"))
        hypothetical = _finite_number(row.get("hypothetical_score"))
        if production is not None and hypothetical is not None:
            return hypothetical - production

    value = _finite_number(row.get(field))
    if value is not None:
        return value
    shadow = row.get("shadow")
    if not isinstance(shadow, Mapping):
        return None
    value = _finite_number(shadow.get(field))
    if value is not None:
        return value
    feature = shadow.get("feature")
    if isinstance(feature, Mapping):
        return _finite_number(feature.get(field))
    return None


def build_shadow_correlations(
    records: Iterable[Mapping[str, Any]],
    *,
    fields: Sequence[str] = CORRELATION_FIELDS,
) -> dict[str, Any]:
    """Return pairwise available-case correlations for shadow diagnostics.

    Missing values are not imputed.  Each pair carries its own sample count so
    sparse comparisons cannot look more certain than the underlying data.
    """
    rows = [dict(record) for record in records]
    unique_fields = tuple(dict.fromkeys(str(field) for field in fields))
    pairs: list[dict[str, Any]] = []
    for left_field, right_field in combinations_with_replacement(unique_fields, 2):
        observations: list[tuple[float, float]] = []
        for row in rows:
            left = _correlation_value(row, left_field)
            right = _correlation_value(row, right_field)
            if left is not None and right is not None:
                observations.append((left, right))
        pairs.append(
            {
                "left_field": left_field,
                "right_field": right_field,
                "paired_count": len(observations),
                "pearson": _pearson(observations),
            }
        )
    return {
        "analysis_type": "descriptive_pairwise_pearson",
        "decision_status": "insufficient_for_production_decision",
        "observation_count": len(rows),
        "fields": list(unique_fields),
        "pairs": pairs,
        "limitations": list(_LIMITATIONS),
    }


def _serialize_cell(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    return value


def _render_records(
    records: Sequence[Mapping[str, Any]],
    *,
    output_format: str,
    fields: Sequence[str],
) -> str:
    if output_format == "json":
        return json.dumps(
            list(records),
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(fields), extrasaction="ignore")
    writer.writeheader()
    for record in records:
        writer.writerow({field: _serialize_cell(record.get(field)) for field in fields})
    return stream.getvalue()


def _output_format(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return "json"
    if suffix == ".csv":
        return "csv"
    raise ValueError("export path must end in .csv or .json")


def _safe_output_path(database_path: str | Path, output_path: str | Path) -> Path:
    database = Path(database_path).expanduser().resolve()
    supplied_output = Path(output_path).expanduser()
    if supplied_output.exists() and supplied_output.is_symlink():
        raise ValueError("export path must not be a symbolic link")
    if not supplied_output.is_absolute():
        output = (Path.cwd() / supplied_output).resolve()
    else:
        output = supplied_output.resolve()
    if output == database:
        raise ValueError("export path must not be the production database")
    if not output.parent.exists() or not output.parent.is_dir():
        raise ValueError("export parent directory must already exist")
    return output


def _atomic_write(path: Path, content: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def export_events(database_path: str | Path, output_path: str | Path) -> int:
    """Export completed event snapshots as CSV or JSON, inferred by suffix."""
    path = _safe_output_path(database_path, output_path)
    records = load_completed_events(database_path)
    _atomic_write(
        path,
        _render_records(records, output_format=_output_format(path), fields=EVENT_FIELDS),
    )
    return len(records)


def export_shadows(database_path: str | Path, output_path: str | Path) -> int:
    """Export completed Range Persistence shadow observations as CSV or JSON."""
    path = _safe_output_path(database_path, output_path)
    records = load_completed_shadows(database_path)
    _atomic_write(
        path,
        _render_records(records, output_format=_output_format(path), fields=SHADOW_FIELDS),
    )
    return len(records)


def export_shadow_summary(database_path: str | Path, output_path: str | Path) -> dict[str, Any]:
    """Write a JSON-only descriptive summary and return the same payload."""
    path = _safe_output_path(database_path, output_path)
    if _output_format(path) != "json":
        raise ValueError("shadow summary path must end in .json")
    summary = summarize_shadows(load_completed_shadows(database_path))
    _atomic_write(
        path,
        json.dumps(summary, allow_nan=False, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
    )
    return summary


def export_shadow_correlations(
    database_path: str | Path, output_path: str | Path
) -> dict[str, Any]:
    """Export pairwise shadow correlations with per-pair observation counts."""
    path = _safe_output_path(database_path, output_path)
    correlations = build_shadow_correlations(load_completed_shadows(database_path))
    output_format = _output_format(path)
    if output_format == "json":
        content = json.dumps(
            correlations,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"
    else:
        content = _render_records(
            correlations["pairs"],
            output_format="csv",
            fields=CORRELATION_EXPORT_FIELDS,
        )
    _atomic_write(path, content)
    return correlations


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export completed Breakout Radar research data without writing SQLite."
    )
    parser.add_argument(
        "--db", required=True, help="Absolute path to the Breakout Radar SQLite file"
    )
    parser.add_argument("--events-out", help="Completed event export ending in .csv or .json")
    parser.add_argument(
        "--shadows-out", help="Range Persistence shadow export ending in .csv or .json"
    )
    parser.add_argument(
        "--correlations-out", help="Pairwise shadow correlations ending in .csv or .json"
    )
    parser.add_argument("--summary-out", help="Descriptive shadow summary ending in .json")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    database_path = Path(args.db).expanduser()
    if not database_path.is_absolute():
        raise ValueError("--db must be an absolute path")

    result: dict[str, Any] = {}
    if args.events_out:
        result["event_rows"] = export_events(database_path, args.events_out)
    if args.shadows_out:
        result["shadow_rows"] = export_shadows(database_path, args.shadows_out)
    if args.correlations_out:
        correlations = export_shadow_correlations(database_path, args.correlations_out)
        result["correlation_pairs"] = len(correlations["pairs"])
    if args.summary_out:
        result["summary"] = export_shadow_summary(database_path, args.summary_out)
    if not result:
        result["summary"] = summarize_shadows(load_completed_shadows(database_path))
    print(json.dumps(result, allow_nan=False, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main
    raise SystemExit(main())
