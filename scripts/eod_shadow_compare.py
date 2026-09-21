#!/usr/bin/env python3
"""Compare a frozen, read-only all-market cache; never fetch or publish data."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.eod_limited.market_data import _load_panel, _required_sessions, _session_manifest
from app.services.eod_limited.market_registry import load_market_registry
from app.services.eod_limited.shadow import _digest, run_shadow_comparison, write_shadow_report
from app.services.eod_limited.universe import select_all_market_universe


DIRECTORY_FETCH_METHOD = "app.services.eod_limited.market_data._fetch_directory"


def _directory_capture(directory_bytes: bytes, directory: list, manifest_path: Path | None, *, allow_subset: bool):
    if manifest_path is None:
        if not allow_subset:
            raise ValueError("full-market directory requires --directory-manifest; use --allow-subset for an explicitly partial scope")
        return {"scope": "subset", "sha256": hashlib.sha256(directory_bytes).hexdigest(), "row_count": len(directory), "completeness_verified": False}
    capture = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(capture, dict):
        raise ValueError("directory manifest must be an object")
    if capture.get("sha256") != hashlib.sha256(directory_bytes).hexdigest():
        raise ValueError("directory manifest SHA256 does not match the frozen file")
    if type(capture.get("row_count")) is not int or capture["row_count"] != len(directory):
        raise ValueError("directory manifest row_count does not match the frozen file")
    try:
        captured_at = datetime.fromisoformat(str(capture["captured_at"]).replace("Z", "+00:00"))
    except (KeyError, ValueError) as exc:
        raise ValueError("directory manifest requires captured_at in UTC") from exc
    if captured_at.tzinfo is None or captured_at.utcoffset() != timedelta(0):
        raise ValueError("directory manifest requires captured_at in UTC")
    if not all(isinstance(capture.get(key), str) and capture[key].strip() for key in ("source", "source_client", "source_client_version")):
        raise ValueError("directory manifest requires source, source_client and source_client_version")
    complete = (capture.get("fetch_method") == DIRECTORY_FETCH_METHOD and capture.get("pagination_complete") is True
                and "terminal_next_url" in capture and capture["terminal_next_url"] is None)
    if not complete and not allow_subset:
        raise ValueError("directory manifest lacks a supported completed pagination declaration")
    return {**capture, "scope": "subset" if allow_subset else "all_market", "completeness_verified": complete}


def load_frozen_cache(cache_db: Path, directory_json: Path, session: date, *, directory_manifest: Path | None = None, allow_subset: bool = False):
    directory_bytes = directory_json.read_bytes()
    directory = json.loads(directory_bytes)
    if isinstance(directory, dict):
        if directory.get("next_url") and not allow_subset:
            raise ValueError("directory is a partial page; provide the complete frozen directory")
        directory = directory.get("results")
    if not isinstance(directory, list) or not directory or not all(isinstance(row, dict) for row in directory):
        raise ValueError("directory-json requires a full provider directory array or completed results envelope")
    capture = _directory_capture(directory_bytes, directory, directory_manifest, allow_subset=allow_subset)
    members, coverage = select_all_market_universe(directory)
    if not members:
        raise ValueError("empty frozen eligible universe")
    sessions = _required_sessions(session)
    # mode=ro also fails for a missing database; no schema or cache writes occur.
    uri = cache_db.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        session_meta = _session_manifest(connection, sessions)
        # A frozen archive must remain reproducible after the live cache TTL.
        # Reuse the stored capture and the production split/bar conversion.
        split = connection.execute(
            "SELECT * FROM split_captures WHERE window_start = ? AND window_end = ?",
            (sessions[0].isoformat(), sessions[-1].isoformat()),
        ).fetchone()
        if split is None:
            raise ValueError("frozen cache lacks complete split coverage")
        panel, coverage, missing, short, residual_short = _load_panel(connection, members, coverage, sessions)
    manifest = {"session": session.isoformat(), "directory_hash": _digest(directory),
                "directory_scope": capture["scope"], "directory_capture": capture,
                "session_manifest_hash": _digest(session_meta), "split_hash": str(split["content_sha256"]),
                "universe_member_hash": _digest({sid: member.__dict__ for sid, member in sorted(members.items())}),
                "eligible_count": len(members), "panel_count": len(panel), "missing_target_count": missing,
                "short_history_count": short, "residual_short_history_count": residual_short,
                "coverage_status_counts": {status: sum(r.get("status") == status for r in coverage) for status in sorted({str(r.get("status")) for r in coverage})}}
    manifest["source_hash"] = _digest(manifest)
    return panel, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-db", type=Path, required=True)
    parser.add_argument("--directory-json", type=Path, required=True)
    parser.add_argument("--directory-manifest", type=Path, help="Complete capture declaration bound to directory file SHA256 and row_count")
    parser.add_argument("--allow-subset", action="store_true", help="Explicitly label the input subset; do not claim full-market coverage")
    parser.add_argument("--session", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profiles", nargs="+", choices=("conservative", "balanced", "aggressive"), default=["conservative", "balanced", "aggressive"])
    parser.add_argument("--horizons", nargs="+", choices=("short", "mid", "long"), default=["short", "mid", "long"])
    parser.add_argument("--geometry-workers", type=int, default=1)
    args = parser.parse_args()
    panel, manifest = load_frozen_cache(args.cache_db, args.directory_json, args.session,
                                      directory_manifest=args.directory_manifest, allow_subset=args.allow_subset)
    report = run_shadow_comparison(panel, args.session, registry=load_market_registry(), profiles=args.profiles,
                                   horizons=args.horizons, source_manifest=manifest, geometry_workers=args.geometry_workers)
    write_shadow_report(report, args.output)
    print(json.dumps({"output": str(args.output.resolve()), "input_hash": report["frozen_inputs"]["input_hash"],
                      "stock_isolation": report["stock_isolation"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
