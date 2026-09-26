"""Immutable, indexed EOD diagnostics published by one ranking batch."""

from __future__ import annotations

from functools import lru_cache
from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Mapping, Sequence
from urllib.parse import quote
from uuid import uuid4
import zlib

from app.failure_diagnostics import record_fallback_failure

from .diagnostics import DATA_REASONS
from .store import json_default, snapshot_dir, variant_key

SCHEMA_VERSION = 2
_NAME = re.compile(r"^diagnostics-[0-9a-f]{32}\.sqlite$")
# Leftovers of a run killed mid-write: the diagnostics temp database, its
# rollback journal, and batch.json temp files from store._atomic_write.
_ORPHAN_NAME = re.compile(r"^(?:diagnostics-[0-9a-f]{32}\.sqlite\.tmp(?:-journal)?|batch\.json[^/]*\.tmp)$")
GENERATION_MIN_AGE_SECONDS = 60 * 60
ORPHAN_MIN_AGE_SECONDS = 60 * 60
_ROW_FIELDS = frozenset({
    "security_id", "ticker_at_signal", "session_date", "feature_version",
    "algorithm_id", "profile", "horizon", "track", "stock_or_etf_track",
    "sector_context", "theme_ids", "display_sector_id", "name", "price", "close",
    "raw_close", "score", "status", "qualification", "rejection_reasons",
    "gate_results", "gate_results_source", "common_gate_checks", "score_gate_checks", "setup_state",
    "factors", "score_components", "configured_weights", "effective_weights",
    "track_weights", "weight_provenance_id", "observed_feature_coverage",
    "adv20", "atr", "atr_pct", "sector_median_atr_pct", "atr_reference_n",
    "atr_reference_source", "atr_reference_policy", "atr_threshold_pct",
    "extension_atr", "extension_limit_atr", "ma_distance_atr",
    "platform_distance_atr", "invalidation_distance_atr", "known_support",
    "known_resistance", "planned_invalidation", "event_data_status",
    "capacity_status", "residual_status", "residual_raw", "price_adjustment",
    "volume_adjustment", "volume_scope", "vintage_status", "tri_verified",
    "reconstruction_mode", "identity_confidence", "industry_source", "halted",
    "currently_tradable", "zero_volume", "momentum_basis", "return_basis",
    "capability_flags",
})


def _encoded(value: Any) -> bytes:
    return zlib.compress(json.dumps(value, ensure_ascii=False, default=json_default, separators=(",", ":"), allow_nan=False).encode("utf-8"), 6)


def _decoded(value: bytes) -> Any:
    return json.loads(zlib.decompress(value))


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@lru_cache(maxsize=16)
def _verified_digest(path: str, size: int, mtime_ns: int) -> str:
    del size, mtime_ns
    return _hash_file(Path(path))


class DiagnosticWriter:
    """Write every scoring path incrementally; publish() provides the commit marker."""

    def __init__(
        self, *, root: Path | None, served_session: str,
        compute_version: str, feature_version: str, source_hash: str | None,
        dollar_volume_basis: str | None,
    ) -> None:
        directory = snapshot_dir(root)
        directory.mkdir(parents=True, exist_ok=True)
        name = f"diagnostics-{uuid4().hex}.sqlite"
        self.final_path = directory / name
        self.temp_path = directory / f"{name}.tmp"
        self.connection = sqlite3.connect(self.temp_path)
        self.connection.execute("PRAGMA journal_mode=DELETE")
        self.connection.execute("PRAGMA synchronous=FULL")
        # Full-market compressed paths are 1.3–1.9 KB each. A WITHOUT ROWID
        # index spills each payload to an overflow page at the default 4 KB
        # page size; ordinary table leaves pack these records together.
        self.connection.executescript("""
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE coverage (
                ticker TEXT COLLATE BINARY PRIMARY KEY, lookup_ticker TEXT NOT NULL, payload BLOB NOT NULL
            );
            CREATE INDEX coverage_lookup ON coverage (lookup_ticker);
            CREATE TABLE paths (
                variant TEXT NOT NULL, ticker TEXT COLLATE BINARY NOT NULL, lookup_ticker TEXT NOT NULL,
                theme_id TEXT NOT NULL,
                algorithm_id TEXT NOT NULL, payload BLOB NOT NULL,
                PRIMARY KEY (variant, ticker, theme_id, algorithm_id)
            );
            CREATE INDEX paths_lookup ON paths (variant, lookup_ticker);
            CREATE TABLE weight_sources (id TEXT PRIMARY KEY, payload BLOB NOT NULL);
        """)
        self.variant_keys: set[str] = set()
        self.source_ids: set[str] = set()
        self.dollar_volume_basis = dollar_volume_basis
        self._closed = False
        metadata = {
            "schema_version": SCHEMA_VERSION, "served_session": served_session,
            "compute_version": compute_version, "feature_version": feature_version,
            "source_hash": source_hash,
        }
        self.connection.executemany(
            "INSERT INTO metadata (key, value) VALUES (?, ?)",
            ((key, json.dumps(value, separators=(",", ":"))) for key, value in metadata.items()),
        )

    def write_coverage(self, rows: Sequence[Mapping[str, Any]]) -> None:
        self.connection.executemany(
            "INSERT INTO coverage (ticker, lookup_ticker, payload) VALUES (?, ?, ?)",
            ((str(row["ticker"]), str(row["ticker"]).upper(), _encoded(dict(row))) for row in rows if row.get("ticker")),
        )

    def write_block(
        self, variant: str, theme_id: str, family: str,
        rows: Sequence[Mapping[str, Any]], provenance: Mapping[str, Any] | None = None,
    ) -> None:
        self.variant_keys.add(variant)
        source = dict(provenance or {})
        if source:
            ids = {str(row.get("weight_provenance_id")) for row in rows if row.get("weight_provenance_id")}
            if not ids and source.get("id"):
                ids = {str(source["id"])}
            for source_id in ids - self.source_ids:
                self.connection.execute(
                    "INSERT INTO weight_sources (id, payload) VALUES (?, ?)",
                    (source_id, _encoded(source)),
                )
                self.source_ids.add(source_id)
        self.connection.executemany(
            "INSERT INTO paths (variant, ticker, lookup_ticker, theme_id, algorithm_id, payload) VALUES (?, ?, ?, ?, ?, ?)",
            (
                (
                    variant,
                    str(row["security_id"]),
                    str(row["security_id"]).upper(),
                    theme_id,
                    family,
                    _encoded({
                        **{key: value for key, value in row.items() if key in _ROW_FIELDS},
                        "adv20_proxy_basis": self.dollar_volume_basis if row.get("adv20") is not None else None,
                    }),
                )
                for row in rows if row.get("security_id")
            ),
        )

    def finish(self) -> dict[str, Any]:
        self.connection.commit()
        result = self.connection.execute("PRAGMA quick_check").fetchone()
        if result is None or result[0] != "ok":
            raise RuntimeError("diagnostic_store_integrity_check_failed")
        self.connection.close()
        self._closed = True
        os.replace(self.temp_path, self.final_path)
        # The batch.json replacement is the publication marker. This file is
        # complete and durable before that marker may point at it.
        with self.final_path.open("rb") as handle:
            os.fsync(handle.fileno())
        dir_fd = os.open(self.final_path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        return {
            "schema_version": SCHEMA_VERSION,
            "path": self.final_path.name,
            "size": self.final_path.stat().st_size,
            "sha256": _hash_file(self.final_path),
            "variant_keys": sorted(self.variant_keys),
            "served_session": self._metadata_value("served_session"),
            "compute_version": self._metadata_value("compute_version"),
            "feature_version": self._metadata_value("feature_version"),
            "source_hash": self._metadata_value("source_hash"),
        }

    def _metadata_value(self, key: str) -> Any:
        # Metadata was written before closing; reread the immutable result.
        with closing(sqlite3.connect(f"file:{quote(str(self.final_path))}?mode=ro&immutable=1", uri=True)) as connection:
            row = connection.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def discard(self) -> None:
        if not self._closed:
            self.connection.close()
            self._closed = True
        self.temp_path.unlink(missing_ok=True)
        self.final_path.unlink(missing_ok=True)


def _safe_path(manifest: Mapping[str, Any], root: Path | None) -> Path | None:
    name = str(manifest.get("path") or "")
    if not _NAME.fullmatch(name):
        return None
    path = snapshot_dir(root) / name
    if path.is_symlink() or not path.is_file():
        return None
    return path


def _validated_path(batch: Mapping[str, Any], root: Path | None) -> Path | None:
    manifest = batch.get("diagnostics")
    if not isinstance(manifest, Mapping) or manifest.get("schema_version") != SCHEMA_VERSION:
        return None
    for key in ("served_session", "compute_version", "feature_version"):
        if manifest.get(key) != batch.get(key):
            return None
    if manifest.get("source_hash") != (batch.get("coverage") or {}).get("source_hash"):
        return None
    path = _safe_path(manifest, root)
    if path is None:
        return None
    stat = path.stat()
    if stat.st_size != manifest.get("size") or _verified_digest(str(path), stat.st_size, stat.st_mtime_ns) != manifest.get("sha256"):
        return None
    return path


def _display_metadata(variant: Mapping[str, Any], ticker: str, paths: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    observation: dict[str, Mapping[str, Any]] = {}
    for block in variant.get("family_results") or ():
        for row in block.get("rows") or ():
            if row.get("status") == "eligible" and isinstance(row.get("score"), (int, float)):
                sid = str(row.get("security_id") or "")
                if sid not in observation or float(row["score"]) > float(observation[sid]["score"]):
                    observation[sid] = row
    for row in variant.get("watch_list") or ():
        if isinstance(row.get("score"), (int, float)):
            sid = str(row.get("security_id") or "")
            if sid not in observation or float(row["score"]) > float(observation[sid]["score"]):
                observation[sid] = row
    track = str(paths[0].get("stock_or_etf_track") or "stock")
    ordered = sorted(
        observation,
        key=lambda sid: (-float(observation[sid]["score"]), sid),
    )
    track_ordered = [sid for sid in ordered if str(observation[sid].get("stock_or_etf_track") or "stock") == track]
    composite_rows = list(variant.get("composite_results") or ())
    composite_rows.sort(key=lambda row: (-float(row.get("consensus_z") or row.get("score") or -1), str(row.get("security_id") or "")))
    composite = {str(row.get("security_id") or ""): index + 1
                 for index, row in enumerate(composite_rows)}
    family_scores: dict[str, dict[str, Any]] = {}
    for row in paths:
        family = str(row.get("algorithm_id") or "")
        score = row.get("score")
        current = family_scores.get(family)
        if current is None or (isinstance(score, (int, float)) and (current["score"] is None or float(score) > float(current["score"]))):
            family_scores[family] = {
                "score": score, "status": row.get("status"),
                "theme_id": row.get("sector_context"),
            }
    for family, item in family_scores.items():
        family_paths = [row for row in paths if row.get("algorithm_id") == family]
        item["passed_strict"] = any(row.get("status") == "eligible" for row in family_paths)
        item["passed_observation"] = any(row.get("status") in {"eligible", "watch"} for row in family_paths)
    best = observation.get(ticker)
    if best is None:
        best = max((row for row in paths if isinstance(row.get("score"), (int, float))),
                   key=lambda row: float(row["score"]), default=None)
    best_path = None if best is None else {
        "theme_id": best.get("sector_context"), "algorithm_id": best.get("algorithm_id"),
        "score": best.get("score"), "status": best.get("status"),
        "rejection_reasons": best.get("rejection_reasons") or [],
    }
    return {
        "in_observation": ticker in observation,
        "in_composite": ticker in composite,
        "observation_rank": ordered.index(ticker) + 1 if ticker in observation else None,
        "composite_rank": composite.get(ticker),
        "rank_scope": "all_assets_before_display_filters",
        "track_observation_rank": track_ordered.index(ticker) + 1 if ticker in track_ordered else None,
        "best_path": best_path,
        "family_scores": family_scores,
        "display_reason": (
            "composite" if ticker in composite else
            "observation_only" if ticker in observation else
            "scored_but_rejected" if best_path is not None else "data_insufficient"
        ),
    }


def read_security_diagnostics(
    batch: Mapping[str, Any], ticker: str, profile: str, horizon: str,
    root: Path | None = None,
) -> dict[str, Any] | None:
    """Read one symbol from exactly the generation referenced by `batch`."""
    requested_ticker = ticker.strip()
    lookup_ticker = requested_ticker.upper()
    key = variant_key(profile, horizon)
    base = {
        "ticker": None, "requested_ticker": requested_ticker,
        "profile": profile, "horizon": horizon,
        "served_session": batch.get("served_session"),
        "compute_version": batch.get("compute_version"),
        "feature_version": batch.get("feature_version"),
        "source_hash": (batch.get("coverage") or {}).get("source_hash"),
        "paths": [], "coverage": None,
    }
    manifest = batch.get("diagnostics") or {}
    if key not in manifest.get("variant_keys", ()):
        return {**base, "data_status": "variant_diagnostics_unavailable", "display": None}
    path = _validated_path(batch, root)
    if path is None:
        return {**base, "data_status": "diagnostics_unavailable", "display": None}
    variant = (batch.get("variants") or {}).get(key)
    if not isinstance(variant, Mapping):
        return {**base, "data_status": "variant_diagnostics_unavailable", "display": None}
    with closing(sqlite3.connect(f"file:{quote(str(path))}?mode=ro&immutable=1", uri=True)) as connection:
        metadata = {item[0]: json.loads(item[1]) for item in connection.execute("SELECT key, value FROM metadata")}
        expected_metadata = {
            "schema_version": SCHEMA_VERSION,
            "served_session": batch.get("served_session"),
            "compute_version": batch.get("compute_version"),
            "feature_version": batch.get("feature_version"),
            "source_hash": (batch.get("coverage") or {}).get("source_hash"),
        }
        if metadata != expected_metadata:
            return {**base, "data_status": "diagnostics_unavailable", "display": None}
        exact_coverage = connection.execute("SELECT payload FROM coverage WHERE ticker=?", (requested_ticker,)).fetchone()
        exact_path = connection.execute(
            "SELECT 1 FROM paths WHERE variant=? AND ticker=? LIMIT 1", (key, requested_ticker),
        ).fetchone()
        if exact_coverage is not None or exact_path is not None:
            provider_ticker = requested_ticker
            identity_resolution = "exact"
        else:
            candidates = {
                str(row[0]) for row in connection.execute(
                    "SELECT ticker FROM coverage WHERE lookup_ticker=?", (lookup_ticker,),
                )
            }
            candidates.update(str(row[0]) for row in connection.execute(
                "SELECT DISTINCT ticker FROM paths WHERE variant=? AND lookup_ticker=?", (key, lookup_ticker),
            ))
            if not candidates:
                return None
            if len(candidates) > 1:
                return {
                    **base, "data_status": "ambiguous_symbol", "provider_tickers": sorted(candidates),
                    "identity_resolution": "ambiguous_case_insensitive", "display": None,
                }
            provider_ticker = next(iter(candidates))
            identity_resolution = "unique_case_insensitive"
        coverage_row = exact_coverage if provider_ticker == requested_ticker else connection.execute(
            "SELECT payload FROM coverage WHERE ticker=?", (provider_ticker,),
        ).fetchone()
        coverage = _decoded(coverage_row[0]) if coverage_row else None
        paths = [_decoded(row[0]) for row in connection.execute(
            "SELECT payload FROM paths WHERE variant=? AND ticker=? ORDER BY theme_id, algorithm_id",
            (key, provider_ticker),
        )]
        source_ids = {str(row.get("weight_provenance_id")) for row in paths if row.get("weight_provenance_id")}
        sources = {}
        for source_id in source_ids:
            row = connection.execute("SELECT payload FROM weight_sources WHERE id=?", (source_id,)).fetchone()
            if row:
                sources[source_id] = _decoded(row[0])
    if coverage is None and not paths:
        return None
    if coverage is not None and str(coverage.get("status") or "") != "ok":
        status = "out_of_scope" if str(coverage.get("status") or "").startswith("excluded:") else "data_insufficient"
    elif not paths:
        status = "not_evaluated_in_variant"
    else:
        status = "scored" if any(
            isinstance(row.get("score"), (int, float))
            and not isinstance(row.get("score"), bool)
            and math.isfinite(float(row["score"]))
            and not DATA_REASONS.intersection(str(reason) for reason in row.get("rejection_reasons") or ())
            for row in paths
        ) else "data_insufficient"
    display = _display_metadata(variant, provider_ticker, paths) if paths else None
    return {
        **base, "ticker": provider_ticker,
        "provider_ticker": provider_ticker, "identity_resolution": identity_resolution,
        "data_status": status, "coverage": coverage, "paths": paths,
        "capability_flags": variant.get("capability_flags") or {},
        "volume_scope": variant.get("volume_scope"),
        "dollar_volume_basis": (batch.get("coverage") or {}).get("dollar_volume_basis"),
        "weight_provenance_sources": sources,
        "display": display,
    }


def _remove_if_older(path: Path, *, now: float, min_age_seconds: float) -> None:
    try:
        if now - path.stat().st_mtime > min_age_seconds:
            path.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        # Housekeeping only: record it and keep sweeping the other files.
        record_fallback_failure("eod_diagnostics_prune", exc)


def prune_old_generations(
    *,
    root: Path | None,
    active_name: str,
    keep: int = 3,
    min_age_seconds: float = GENERATION_MIN_AGE_SECONDS,
    orphan_min_age_seconds: float = ORPHAN_MIN_AGE_SECONDS,
) -> None:
    """Keep the newest ``keep`` generations and the active one; sweep old temp files.

    Age is only a floor: it lets a reader that still holds the previous batch
    finish. A temp file younger than ``orphan_min_age_seconds`` may belong to
    a run in progress and is left alone.
    """
    directory = snapshot_dir(root)
    now = time.time()
    generations: list[tuple[float, Path]] = []
    for path in directory.glob("diagnostics-*.sqlite"):
        if not _NAME.fullmatch(path.name) or path.is_symlink():
            continue
        try:
            generations.append((path.stat().st_mtime, path))
        except FileNotFoundError:
            continue
    generations.sort(key=lambda item: item[0], reverse=True)
    for _mtime, path in generations[keep:]:
        if path.name != active_name:
            _remove_if_older(path, now=now, min_age_seconds=min_age_seconds)
    for path in directory.iterdir():
        if not _ORPHAN_NAME.fullmatch(path.name) or path.is_symlink():
            continue
        if path.name.endswith("-journal"):
            # SQLite writes the rollback journal once per transaction, so its
            # mtime stops moving while the temp database keeps growing. Judge
            # the journal by its owner; a live writer must keep its journal.
            owner = path.with_name(path.name[: -len("-journal")])
            try:
                if now - owner.stat().st_mtime <= orphan_min_age_seconds:
                    continue
            except FileNotFoundError:
                pass
        _remove_if_older(path, now=now, min_age_seconds=orphan_min_age_seconds)
