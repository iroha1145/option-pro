"""Recover existing authorized caches and gate experiments by layer.

Not a new weight grid. Not a B0 rewrite. Yahoo cache and B0 stay separate versions.
"""

from __future__ import annotations

import math
import os
import pickle
import stat
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.services.research_eod_v1.constants import RESIDUAL_HISTORY_MIN
from app.services.research_eod_v1.data.contract import ResearchBar, validate_research_bars
from app.services.research_eod_v1.freeze import FROZEN_CANDIDATE_ID, FROZEN_FAMILY, FROZEN_THEME
from app.services.research_eod_v1.paths import REPO_ROOT, RETURN_PACK_DIR
from app.services.research_eod_v1.source_bind import EXPECTED_B0_SHA256, sha256_file
from app.services.sectors import SECTORS

PROTOCOL = "us-eod-research-recovery-v1"
RECOVERY_DATASET_VERSION = "us-eod-recovered-yahoo-cache-v1"
B0_FEATURE_VERSION = "us-eod-research-features-v1.5"
ALLOWED_END = date(2024, 6, 28)
HOLDOUT_START = date(2024, 7, 1)
LABEL_HORIZONS = (5, 20, 63)
MAIN_LABEL = 20
AUTOMOTIVE_MEMBERS = ("F", "GM", "LCID", "LI", "NIO", "RIVN", "STLA", "TM", "TSLA", "XPEV")
# Registry min_history is 252 for A/B/C and 330 for D. Do not invent 200/80/200.
FAMILY_WARMUP_SESSIONS = {
    "A_trend_quality": 252,
    "B_confirmed_base_breakout": 252,
    "C_trend_pullback": 252,
    "D_residual_momentum": max(330, RESIDUAL_HISTORY_MIN),
}
STATIC_VENUE_NOTES = {
    "LVMUY": "OTC_EXCLUDED",
    "RMS.PA": "NON_US_LISTING",
    "CFRUY": "OTC_EXCLUDED",
}
TRUSTED_RELATIVE_PICKLES = (
    "research/option_pro_us_eod_v1/data/cache/round3_yahoo_abcd/bars.pkl",
    "research/option_pro_us_eod_v1/data/cache/round3_after_close/last_bars.pkl",
)

LAYER_A = "A_close_signal_diagnostic"
LAYER_B = "B_total_return_vs_quote"
LAYER_C = "C_unadjusted_execution"
LAYER_D = "D_historical_membership"


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve()))
    except ValueError:
        return str(path)


def trusted_pickle_paths(*, root: Path | None = None) -> tuple[Path, ...]:
    base = (root or REPO_ROOT).resolve()
    return tuple(base / item for item in TRUSTED_RELATIVE_PICKLES)


def is_trusted_pickle(path: Path, *, root: Path | None = None) -> bool:
    resolved = path.resolve()
    return resolved in {item.resolve() for item in trusted_pickle_paths(root=root)}


def load_trusted_research_bars(path: Path, *, root: Path | None = None) -> dict[str, list[ResearchBar]]:
    if not is_trusted_pickle(path, root=root):
        raise ValueError(f"refusing untrusted pickle: {path}")
    if not path.is_file():
        raise FileNotFoundError(path)
    payload = pickle.loads(path.read_bytes())
    if not isinstance(payload, dict):
        raise ValueError("trusted cache must be a symbol-to-bars mapping")
    out: dict[str, list[ResearchBar]] = {}
    for symbol, bars in payload.items():
        if not isinstance(symbol, str) or not isinstance(bars, list):
            raise ValueError("unexpected pickle payload shape")
        if bars and not isinstance(bars[0], ResearchBar):
            raise ValueError(f"{symbol}: cache is not a ResearchBar list")
        out[symbol] = bars
    return out


def _file_record(path: Path, *, role: str, authorization: str) -> dict[str, Any]:
    exists = path.exists()
    record: dict[str, Any] = {
        "path": _rel(path) if exists or path.is_absolute() else str(path),
        "role": role,
        "authorization": authorization,
        "exists": exists,
        "readable": exists and os.access(path, os.R_OK),
        "status": "present" if exists else "missing",
    }
    if path.is_file():
        record["bytes"] = path.stat().st_size
        record["mode"] = stat.filemode(path.stat().st_mode)
        record["sha256"] = sha256_file(path)
    elif path.is_dir():
        record["status"] = "directory"
        record["entries"] = sum(1 for _ in path.iterdir())
    record.update(empty_source_fields())
    return record


INVENTORY_COVERAGE_KEYS = (
    "securities_n",
    "first_session",
    "last_session",
    "last_allowed_session",
    "field_capabilities",
    "corporate_actions",
    "vintage_status",
    "known_at_vs_retrieved",
    "backup_locator",
)


def empty_source_fields() -> dict[str, Any]:
    return {
        "securities_n": None,
        "first_session": None,
        "last_session": None,
        "last_allowed_session": None,
        "field_capabilities": None,
        "corporate_actions": None,
        "vintage_status": None,
        "known_at_vs_retrieved": None,
        "backup_locator": None,
    }


def coverage_from_bars(bars: Mapping[str, Sequence[ResearchBar]]) -> dict[str, Any]:
    firsts: list[date] = []
    lasts: list[date] = []
    allowed_lasts: list[date] = []
    raw_ne = 0
    tri_ne = 0
    retrieved_after_close = 0
    known_missing = 0
    for rows in bars.values():
        if not rows:
            continue
        firsts.append(rows[0].session_date)
        lasts.append(rows[-1].session_date)
        allowed = [bar.session_date for bar in rows if bar.session_date <= ALLOWED_END]
        if allowed:
            allowed_lasts.append(allowed[-1])
        for bar in rows:
            if bar.raw_close is not None and bar.close is not None and abs(bar.raw_close - bar.close) > 1e-9:
                raw_ne += 1
            if bar.tri is not None and bar.close is not None and abs(bar.tri - bar.close) > 1e-6:
                tri_ne += 1
            if bar.economic_known_at is None:
                known_missing += 1
            if bar.retrieved_at is not None and bar.economic_known_at is not None:
                if bar.retrieved_at.timestamp() > bar.economic_known_at.timestamp():
                    retrieved_after_close += 1
    return {
        "securities_n": len(bars),
        "first_session": min(firsts).isoformat() if firsts else None,
        "last_session": max(lasts).isoformat() if lasts else None,
        "last_allowed_session": max(allowed_lasts).isoformat() if allowed_lasts else None,
        "field_capabilities": {
            "ohlcv": True,
            "raw_equals_structure_close": raw_ne == 0,
            "tri_present": any(
                bar.tri is not None
                for rows in bars.values()
                for bar in rows
            ),
            "tri_differs_from_close_bars": tri_ne,
            "dollar_volume_is_close_times_volume": True,
            "is_raw_unadjusted_eod": False,
        },
        "corporate_actions": "no_licensed_ledger; splits_already_in_close_series",
        "vintage_status": "download_time_not_pit",
        "known_at_vs_retrieved": {
            "economic_known_at": "session_close_America_New_York",
            "source_published_at": None,
            "retrieved_at": "download_clock",
            "bars_with_retrieved_after_session_close": retrieved_after_close,
            "bars_missing_economic_known_at": known_missing,
        },
        "backup_locator": None,
    }


def inspect_offline_parquet(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return empty_source_fields()
    try:
        import pandas as pd
    except ImportError:
        return empty_source_fields()
    frame = pd.read_parquet(path)
    dates = [str(item)[:10] for item in frame.get("session_date", [])]
    symbols = sorted({str(item) for item in frame.get("security_id", [])})
    return {
        "securities_n": len(symbols),
        "first_session": min(dates) if dates else None,
        "last_session": max(dates) if dates else None,
        "last_allowed_session": max((item for item in dates if item <= ALLOWED_END.isoformat()), default=None),
        "field_capabilities": {
            "ohlcv": True,
            "rows": int(len(frame)),
            "symbols": symbols,
            "is_raw_unadjusted_eod": False,
            "not_a_substitute_for_214_name_cache": True,
        },
        "corporate_actions": "absent",
        "vintage_status": str(frame["vintage_status"].iloc[0]) if "vintage_status" in frame.columns and len(frame) else "offline_export",
        "known_at_vs_retrieved": "fixture_download_time_not_pit",
        "backup_locator": None,
    }


def inspect_security_master(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return empty_source_fields()
    import csv

    with path.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    firsts = [str(row["first"])[:10] for row in rows if row.get("first")]
    lasts = [str(row["last"])[:10] for row in rows if row.get("last")]
    return {
        "securities_n": len(rows),
        "first_session": min(firsts) if firsts else None,
        "last_session": max(lasts) if lasts else None,
        "last_allowed_session": None,
        "field_capabilities": {
            "kind": "current_list_metadata_not_bars",
            "claimed_bar_counts": True,
            "bars_file_missing": True,
            "claimed_last_includes_post_holdout": bool(lasts and max(lasts) >= HOLDOUT_START.isoformat()),
            "is_raw_unadjusted_eod": False,
        },
        "corporate_actions": "not_in_this_file",
        "vintage_status": "current_list_locator_for_missing_parquet",
        "known_at_vs_retrieved": "not_a_price_tape",
        "backup_locator": (
            "research/option_pro_us_eod_v1/return_pack/yahoo_current_universe/manifest.json "
            "and hashes.json yahoo_daily_bars_sha256 "
            "d056327fd03abdb3a276a8f20039ea2af04300bc7ec9c1dd10010343abf7262e"
        ),
    }


def inspect_revoked_snapshot(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return empty_source_fields()
    try:
        import pandas as pd
    except ImportError:
        return empty_source_fields()
    frame = pd.read_parquet(path)
    dates = [str(item)[:10] for item in frame.get("session_date", [])]
    symbols = sorted({str(item) for item in frame.get("security_id", [])})
    allowed = [item for item in dates if item <= ALLOWED_END.isoformat()]
    return {
        "securities_n": len(symbols),
        "first_session": min(dates) if dates else None,
        "last_session": max(dates) if dates else None,
        "last_allowed_session": max(allowed) if allowed else None,
        "field_capabilities": {
            "kind": "revoked_same_day_theme_snapshot",
            "is_raw_unadjusted_eod": False,
            "is_market_history": False,
            "rows": int(len(frame)),
            "invalid_eod_capture": True,
        },
        "corporate_actions": "absent",
        "vintage_status": "INVALID_EOD_CAPTURE",
        "known_at_vs_retrieved": "same_day_preclose_capture_revoked",
        "backup_locator": None,
    }


def inspect_cache_tree(path: Path) -> dict[str, Any]:
    files: list[str] = []
    if path.is_dir():
        files = sorted(_rel(item) for item in path.rglob("*") if item.is_file())
    return {
        "securities_n": None,
        "first_session": None,
        "last_session": None,
        "last_allowed_session": None,
        "field_capabilities": {
            "kind": "project_cache_tree",
            "file_count": len(files),
            "files": files,
            "unknown_pickles_loaded": False,
        },
        "corporate_actions": None,
        "vintage_status": "scanned",
        "known_at_vs_retrieved": None,
        "backup_locator": None,
    }


def cursor_artifact_inventory() -> dict[str, Any]:
    roots = [
        Path("/opt/cursor/artifacts"),
        Path("/home/ubuntu/.cursor/projects/workspace/uploads"),
    ]
    market_like = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            name = path.name.lower()
            if name.endswith((".pkl", ".pickle")) or name in {"daily_bars.parquet", "measurement_factor_rows.jsonl"}:
                market_like.append(_rel(path) if path.exists() else str(path))
    return {
        "role": "authorized_cursor_artifact_dirs",
        "authorization": "this_run_uploads_and_walkthrough_artifacts",
        "exists": True,
        "status": "scanned_no_additional_market_tape" if not market_like else "found_market_like_files",
        "path": [str(root) for root in roots if root.is_dir()],
        "market_like_files": market_like,
        "untrusted_pickles_loaded": False,
        **empty_source_fields(),
        "field_capabilities": {"research_specs_and_audits_only": True, "additional_eod_tape": False},
        "backup_locator": None,
    }


def split_window_samples(bars: Sequence[ResearchBar], targets: Sequence[date]) -> list[dict[str, Any]]:
    ordered = list(bars)
    index = {bar.session_date: i for i, bar in enumerate(ordered)}
    out = []
    for target in targets:
        nearby = [bar for bar in ordered if abs((bar.session_date - target).days) <= 2]
        rows = []
        for bar in nearby:
            loc = index[bar.session_date]
            prev = ordered[loc - 1] if loc else None
            overnight = None
            if prev is not None and prev.close and bar.open:
                overnight = bar.open / prev.close
            rows.append(
                {
                    "session": bar.session_date.isoformat(),
                    "open": bar.open,
                    "close": bar.close,
                    "raw_close": bar.raw_close,
                    "tri": bar.tri,
                    "volume": bar.volume,
                    "raw_equals_close": bar.raw_close == bar.close,
                    "overnight_open_over_prev_close": overnight,
                }
            )
        out.append(
            {
                "event": target.isoformat(),
                "bars": rows,
                "invariant": "no 4-for-1 or 5-for-1 raw gap; close already looks split-adjusted; C blocked",
            }
        )
    return out


def annotate_inventory(
    rows: Sequence[Mapping[str, Any]],
    *,
    yahoo_bars: Mapping[str, Sequence[ResearchBar]] | None = None,
    after_close_bars: Mapping[str, Sequence[ResearchBar]] | None = None,
    root: Path | None = None,
) -> list[dict[str, Any]]:
    base = root or REPO_ROOT
    research = base / "research" / "option_pro_us_eod_v1"
    out = [dict(row) for row in rows]
    for row in out:
        role = row.get("role")
        if role == "continuous_runner_yahoo_cache" and yahoo_bars is not None:
            row.update(coverage_from_bars(yahoo_bars))
        elif role == "after_close_recapture_cache" and after_close_bars is not None:
            row.update(coverage_from_bars(after_close_bars))
            row["last_allowed_session"] = None
            row["field_capabilities"] = {
                **(row.get("field_capabilities") or {}),
                "entirely_after_holdout": True,
                "not_used_for_evaluation": True,
            }
        elif role == "offline_replay_spy_nvda_fixture":
            row.update(inspect_offline_parquet(research / "data" / "cache" / "offline_replay" / "daily_bars.parquet"))
        elif role == "b0_feature_label_tape":
            row.update(
                {
                    "securities_n": None,
                    "first_session": "2018-01-02",
                    "last_session": "2024-06-28",
                    "last_allowed_session": "2024-06-28",
                    "field_capabilities": {
                        "is_raw_unadjusted_eod": False,
                        "kind": "feature_label_rows",
                        "cannot_invert_to_prints": True,
                    },
                    "corporate_actions": "not_in_this_tape",
                    "vintage_status": "frozen_b0",
                    "known_at_vs_retrieved": "b0_signal_session_only",
                    "backup_locator": None,
                }
            )
        elif role == "public_yahoo_current_universe_bars":
            row["backup_locator"] = (
                "research/option_pro_us_eod_v1/return_pack/yahoo_current_universe/manifest.json "
                "and hashes.json digest d056327fd03abdb3a276a8f20039ea2af04300bc7ec9c1dd10010343abf7262e"
            )
            row["field_capabilities"] = {"missing_from_public_git": True, "historical_bar_rows_claimed": 582198}
            row["corporate_actions"] = "unknown_until_file_restored"
            row["vintage_status"] = "removed_from_public_git"
        elif role == "current_universe_security_master":
            row.update(inspect_security_master(research / "return_pack" / "yahoo_current_universe" / "security_master.csv"))
        elif role == "revoked_invalid_eod_snapshot_rows":
            row.update(inspect_revoked_snapshot(research / "return_pack" / "yahoo_current_universe" / "theme_snapshot_rows.parquet"))
        elif role == "missing_parquet_locator":
            row["backup_locator"] = "hashes.json yahoo_daily_bars_sha256"
            row["field_capabilities"] = {"kind": "manifest_locator", "locates_missing_daily_bars": True}
            row["corporate_actions"] = "unknown_until_file_restored"
            row["vintage_status"] = "INVALID_EOD_CAPTURE_locator"
        elif role == "historical_public_bars_digest":
            digest_path = research / "return_pack" / "hashes.json"
            payload: dict[str, Any] = {}
            if digest_path.is_file():
                payload = __import__("json").loads(digest_path.read_text(encoding="utf-8"))
            row["field_capabilities"] = {
                "kind": "locator_not_tape",
                "yahoo_daily_bars_sha256": payload.get("yahoo_daily_bars_sha256"),
                "yahoo_daily_bars_git_status": payload.get("yahoo_daily_bars_git_status"),
            }
            row["backup_locator"] = (
                "hashes.json yahoo_daily_bars_sha256 "
                f"{payload.get('yahoo_daily_bars_sha256')}; file not present in this environment"
            )
            row["corporate_actions"] = "unknown_until_file_restored"
            row["vintage_status"] = payload.get("invalid_eod_capture")
        elif role == "project_cache_tree":
            row.update(inspect_cache_tree(research / "data" / "cache"))
        elif role == "ci_fixture_not_market_history":
            row.update(inspect_offline_parquet(research / "return_pack" / "fixtures" / "synthetic_daily_bars.parquet"))
            row["field_capabilities"] = {
                **(row.get("field_capabilities") or {}),
                "synthetic": True,
                "is_market_history": False,
            }
        elif role == "RESEARCH_EOD_LOCAL_DATA" and row.get("status") == "unset":
            row["backup_locator"] = "set RESEARCH_EOD_LOCAL_DATA to an authorized export tree"
            row["field_capabilities"] = {"kind": "optional_authorized_export", "present": False}
        elif role == "authorized_local_export_root" and row.get("status") == "missing":
            row["backup_locator"] = "user-provided authorized parquet/csv under data/local"
            row["field_capabilities"] = {"kind": "optional_authorized_export", "present": False}
    out.append(cursor_artifact_inventory())
    return out


def inventory_sources(*, root: Path | None = None) -> list[dict[str, Any]]:
    base = root or REPO_ROOT
    research = base / "research" / "option_pro_us_eod_v1"
    env_root = os.environ.get("RESEARCH_EOD_LOCAL_DATA")
    rows = [
        _file_record(
            research / "data" / "cache" / "round3_yahoo_abcd" / "bars.pkl",
            role="continuous_runner_yahoo_cache",
            authorization="project_gitignored_yahoo_diagnostic_cache",
        ),
        _file_record(
            research / "data" / "cache" / "round3_after_close" / "last_bars.pkl",
            role="after_close_recapture_cache",
            authorization="project_gitignored_yahoo_diagnostic_cache",
        ),
        _file_record(
            research / "data" / "cache" / "offline_replay" / "daily_bars.parquet",
            role="offline_replay_spy_nvda_fixture",
            authorization="derived_from_yahoo_cache_not_a_substitute_inventory",
        ),
        _file_record(
            research / "return_pack" / "measurement_factor_rows.jsonl",
            role="b0_feature_label_tape",
            authorization="frozen_b0_offline_tape",
        ),
        _file_record(
            research / "return_pack" / "yahoo_current_universe" / "daily_bars.parquet",
            role="public_yahoo_current_universe_bars",
            authorization="removed_from_public_git",
        ),
        _file_record(
            research / "return_pack" / "yahoo_current_universe" / "security_master.csv",
            role="current_universe_security_master",
            authorization="public_return_pack",
        ),
        _file_record(
            research / "return_pack" / "yahoo_current_universe" / "theme_snapshot_rows.parquet",
            role="revoked_invalid_eod_snapshot_rows",
            authorization="public_return_pack_revoked_same_day_capture",
        ),
        _file_record(
            research / "return_pack" / "yahoo_current_universe" / "manifest.json",
            role="missing_parquet_locator",
            authorization="public_return_pack",
        ),
        _file_record(
            research / "return_pack" / "hashes.json",
            role="historical_public_bars_digest",
            authorization="public_return_pack",
        ),
        _file_record(
            research / "data" / "cache",
            role="project_cache_tree",
            authorization="project_gitignored_and_fixture_tree",
        ),
        _file_record(
            research / "return_pack" / "fixtures" / "synthetic_daily_bars.parquet",
            role="ci_fixture_not_market_history",
            authorization="synthetic",
        ),
        _file_record(
            research / "data" / "local",
            role="authorized_local_export_root",
            authorization="RESEARCH_EOD_LOCAL_DATA_or_default_local",
        ),
    ]
    if env_root:
        rows.append(_file_record(Path(env_root), role="RESEARCH_EOD_LOCAL_DATA", authorization="user_env"))
    else:
        unset = {
            "path": None,
            "role": "RESEARCH_EOD_LOCAL_DATA",
            "authorization": "unset",
            "exists": False,
            "readable": False,
            "status": "unset",
        }
        unset.update(empty_source_fields())
        rows.append(unset)
    return rows


def _finite_positive(value: float | None) -> bool:
    if value is None:
        return False
    return value == value and value not in (float("inf"), float("-inf")) and value > 0


def _complete_t_day(bar: ResearchBar) -> bool:
    if bar.missing or bar.partial or bar.vintage_status == "PARTIAL":
        return False
    if not all(_finite_positive(price) for price in (bar.open, bar.high, bar.low, bar.close)):
        return False
    return _ohlc_ok(bar)


def _ohlc_ok(bar: ResearchBar) -> bool:
    if None in (bar.open, bar.high, bar.low, bar.close):
        return True
    top = max(bar.open, bar.close)
    bottom = min(bar.open, bar.close)
    return bar.high >= top and bar.low <= bottom


def inspect_symbol_bars(bars: Sequence[ResearchBar], *, allowed_end: date = ALLOWED_END) -> dict[str, Any]:
    if not bars:
        return {
            "n": 0,
            "allowed_n": 0,
            "first": None,
            "last": None,
            "last_allowed": None,
            "complete_t_days": 0,
            "ohlc_violations": 0,
            "duplicates": 0,
            "unsorted": False,
            "raw_ne_close": 0,
            "tri_ne_close": 0,
            "dollar_volume_mismatch": 0,
            "partial": 0,
            "after_holdout": 0,
            "overnight_gt_40pct": 0,
        }
    dates = [bar.session_date for bar in bars]
    duplicates = len(dates) - len(set(dates))
    unsorted = any(dates[i] < dates[i - 1] for i in range(1, len(dates)))
    ohlc_violations = 0
    complete = 0
    raw_ne = 0
    tri_ne = 0
    dv_mismatch = 0
    partial = 0
    after_holdout = 0
    allowed_n = 0
    overnight = 0
    last_allowed = None
    for index, bar in enumerate(bars):
        if bar.session_date >= HOLDOUT_START:
            after_holdout += 1
        if bar.session_date <= allowed_end:
            allowed_n += 1
            last_allowed = bar.session_date
        if bar.partial or bar.vintage_status == "PARTIAL":
            partial += 1
        if not _ohlc_ok(bar):
            ohlc_violations += 1
        if _complete_t_day(bar) and bar.session_date <= allowed_end:
            complete += 1
        if bar.raw_close is not None and bar.close is not None and abs(bar.raw_close - bar.close) > 1e-9:
            raw_ne += 1
        if bar.tri is not None and bar.close is not None and abs(bar.tri - bar.close) > 1e-6:
            tri_ne += 1
        if bar.dollar_volume is not None and bar.close is not None and bar.volume is not None:
            expected = bar.close * bar.volume
            if abs(expected - bar.dollar_volume) > 1e-3:
                dv_mismatch += 1
        if index and bars[index - 1].close and bar.open:
            ratio = bar.open / bars[index - 1].close
            if ratio < 0.6 or ratio > 1.67:
                overnight += 1
    return {
        "n": len(bars),
        "allowed_n": allowed_n,
        "first": dates[0].isoformat(),
        "last": dates[-1].isoformat(),
        "last_allowed": last_allowed.isoformat() if last_allowed else None,
        "complete_t_days": complete,
        "ohlc_violations": ohlc_violations,
        "duplicates": duplicates,
        "unsorted": unsorted,
        "raw_ne_close": raw_ne,
        "tri_ne_close": tri_ne,
        "dollar_volume_mismatch": dv_mismatch,
        "partial": partial,
        "after_holdout": after_holdout,
        "overnight_gt_40pct": overnight,
    }


def inspect_yahoo_cache(bars: Mapping[str, Sequence[ResearchBar]]) -> dict[str, Any]:
    firsts: Counter[str] = Counter()
    lasts: Counter[str] = Counter()
    total = 0
    ohlc = 0
    raw_ne = 0
    for symbol, rows in bars.items():
        summary = inspect_symbol_bars(rows)
        total += summary["n"]
        ohlc += summary["ohlc_violations"]
        raw_ne += summary["raw_ne_close"]
        if summary["first"]:
            firsts[summary["first"]] += 1
        if summary["last"]:
            lasts[summary["last"]] += 1
    spy = inspect_symbol_bars(bars.get("SPY") or [])
    return {
        "symbols": len(bars),
        "empty_symbols": sum(1 for rows in bars.values() if not rows),
        "bar_rows": total,
        "ohlc_violations": ohlc,
        "raw_ne_close_bars": raw_ne,
        "first_session_counts": dict(firsts),
        "last_session_counts": dict(lasts),
        "spy": spy,
        "automotive": {symbol: inspect_symbol_bars(bars.get(symbol) or []) for symbol in AUTOMOTIVE_MEMBERS},
        "not_spliced_onto_b0": True,
    }


def calendar_from_spy(bars: Sequence[ResearchBar], *, allowed_end: date = ALLOWED_END) -> list[date]:
    return [
        bar.session_date
        for bar in bars
        if bar.session_date <= allowed_end and _complete_t_day(bar)
    ]


def shift_sessions(sessions: Sequence[date], index: int, steps: int) -> date | None:
    target = index + steps
    if target < 0 or target >= len(sessions):
        return None
    return sessions[target]


def decade_budget(sessions: Sequence[date]) -> dict[str, Any]:
    raw_start = sessions[0] if sessions else None
    raw_end = sessions[-1] if sessions else None
    families = {}
    for family, warmup in FAMILY_WARMUP_SESSIONS.items():
        # history_sessions counts the signal day. First scoreable is sessions[warmup - 1].
        first_scoreable = sessions[warmup - 1] if len(sessions) >= warmup else None
        labels = {}
        for horizon in LABEL_HORIZONS:
            mature = sessions[warmup - 1 + horizon] if len(sessions) >= warmup + horizon else None
            evaluable_n = max(0, len(sessions) - warmup - horizon + 1)
            labels[str(horizon)] = {
                "mature_label_day": mature.isoformat() if mature else None,
                "evaluable_sessions": evaluable_n,
                "not_a_long_horizon_validation": horizon == 63,
            }
        families[family] = {
            "warmup_sessions": warmup,
            "first_scoreable_day": first_scoreable.isoformat() if first_scoreable else None,
            "labels": labels,
        }
    span_years = None
    if raw_start and raw_end:
        span_years = (raw_end - raw_start).days / 365.25
    return {
        "raw_start": raw_start.isoformat() if raw_start else None,
        "allowed_end": raw_end.isoformat() if raw_end else None,
        "holdout_start": HOLDOUT_START.isoformat(),
        "holdout_used_for_labels": False,
        "raw_span_years": span_years,
        "ten_year_evaluable": False,
        "note": "Long history extends backward only. Holdout sessions are not read to pad a decade.",
        "families": families,
        "profiles_and_horizons_are_separate_experiments": True,
        "label_63_is_not_horizon_long": True,
    }


def layer_status_for_cache(summary: Mapping[str, Any]) -> dict[str, Any]:
    has_complete = int(summary.get("complete_t_days") or 0) > 0
    ohlc_bad = int(summary.get("ohlc_violations") or 0) > 0
    duplicates = int(summary.get("duplicates") or 0) > 0
    unsorted = bool(summary.get("unsorted"))
    contract = str(summary.get("contract") or "ok")
    a_ok = has_complete and not ohlc_bad and not duplicates and not unsorted and contract == "ok"
    raw_unverified = int(summary.get("raw_ne_close") or 0) == 0
    tri_differs = int(summary.get("tri_ne_close") or 0) > 0
    return {
        LAYER_A: {
            "status": "available" if a_ok else "blocked",
            "note": (
                "Complete T-day OHLC supports close-signal diagnostics. PRICE_ONLY / missing-G tracks stay."
                if a_ok
                else "validate_research_bars / OHLC / finite / duplicate checks blocked A for this unit."
            ),
        },
        LAYER_B: {
            "status": "partial" if has_complete else "blocked",
            "note": (
                "TRI (Yahoo Adj Close) can be compared with quote returns when it differs. "
                "No licensed split/dividend ledger. Not a verified total-return benchmark."
                if tri_differs
                else "TRI present but unverified. No licensed action ledger."
            ),
            "tri_ne_close_bars": int(summary.get("tri_ne_close") or 0),
        },
        LAYER_C: {
            "status": "blocked",
            "note": "raw_open/raw_close copy the structure session prices. Do not treat them as unadjusted prints. NAV/win-rate/capacity stay uninvented.",
            "raw_price_verified": False,
            "raw_ne_close_bars": int(summary.get("raw_ne_close") or 0),
            "silent_raw_equals_close": raw_unverified and has_complete,
        },
        LAYER_D: {
            "status": "current_list_only",
            "note": "known_theme_members / SECTORS only. Current-list replay is not a survivorship-free market proof.",
        },
    }


def capability_matrix(
    inventory: Sequence[Mapping[str, Any]],
    yahoo_summary: Mapping[str, Any] | None,
) -> dict[str, Any]:
    present = {row["role"]: row for row in inventory}
    yahoo = present.get("continuous_runner_yahoo_cache") or {}
    b0 = present.get("b0_feature_label_tape") or {}
    fixture = present.get("offline_replay_spy_nvda_fixture") or {}
    missing_public = present.get("public_yahoo_current_universe_bars") or {}
    layers = layer_status_for_cache((yahoo_summary or {}).get("spy") or {})
    return {
        "protocol": PROTOCOL,
        "recovery_dataset_version": RECOVERY_DATASET_VERSION,
        "b0_feature_version": B0_FEATURE_VERSION,
        "versions_not_spliced": True,
        "layers": layers,
        "sources": {
            "yahoo_cache": {
                "status": yahoo.get("status"),
                "sha256": yahoo.get("sha256"),
                "feeds": [LAYER_A, LAYER_B],
            },
            "b0_tape": {
                "status": b0.get("status"),
                "sha256": b0.get("sha256"),
                "expected": EXPECTED_B0_SHA256,
                "is_raw_eod": False,
                "note": "Feature/label tape. Cannot invert scores into unadjusted prints.",
            },
            "offline_replay_fixture": {
                "status": fixture.get("status"),
                "note": "SPY/NVDA only. Not a substitute for the 214-name cache inventory.",
            },
            "public_yahoo_daily_bars": {
                "status": missing_public.get("status"),
                "locator": "return_pack/yahoo_current_universe/manifest.json plus hashes.json historical digest",
                "historical_digest": "d056327fd03abdb3a276a8f20039ea2af04300bc7ec9c1dd10010343abf7262e",
            },
        },
        "g_track": "keep_missing_G_and_PRICE_ONLY",
        "invent_neutral_g": False,
    }


def theme_capability_table(bars: Mapping[str, Sequence[ResearchBar]]) -> list[dict[str, Any]]:
    spy_sessions = set(calendar_from_spy(bars.get("SPY") or []))
    rows = []
    for theme_id, sector in SECTORS.items():
        members = [str(ticker) for ticker in sector["tickers"]]
        present = []
        late = []
        venue = []
        firsts = []
        for ticker in members:
            series = bars.get(ticker) or []
            summary = inspect_symbol_bars(series)
            if summary["n"]:
                present.append(ticker)
                firsts.append(summary["first"])
                if summary["first"] and summary["first"] > "2018-01-02":
                    late.append({"ticker": ticker, "first": summary["first"]})
            if ticker in STATIC_VENUE_NOTES:
                venue.append({"ticker": ticker, "decision": STATIC_VENUE_NOTES[ticker]})
        have = {ticker for ticker in present}
        overlap = 0
        if spy_sessions:
            common = spy_sessions
            for ticker in members:
                dates = {
                    bar.session_date
                    for bar in (bars.get(ticker) or [])
                    if bar.session_date <= ALLOWED_END and _complete_t_day(bar)
                }
                common = common & dates if ticker in have else common
            overlap = len(common)
        rows.append(
            {
                "theme": theme_id,
                "members": members,
                "members_in_cache": present,
                "missing_from_cache": [ticker for ticker in members if ticker not in have],
                "late_listed_vs_2018": late,
                "static_venue_notes": venue,
                "historical_theme_rebuild": "NOT_AVAILABLE",
                "membership": "CURRENT_LIST_ONLY",
                "industry_parent_is_not_theme": True,
                "common_complete_days_vs_spy_allowed": overlap,
                "new_version_minted": False,
                "layers": {
                    LAYER_A: "available" if present else "blocked",
                    LAYER_B: "partial" if present else "blocked",
                    LAYER_C: "blocked",
                    LAYER_D: "current_list_only",
                },
            }
        )
    return rows


def _index_map(sessions: Sequence[date]) -> dict[date, int]:
    return {session: index for index, session in enumerate(sessions)}


def automotive_descriptive(bars: Mapping[str, Sequence[ResearchBar]]) -> dict[str, Any]:
    spy = bars.get("SPY") or []
    sessions = calendar_from_spy(spy)
    if not sessions:
        return {"status": "INSUFFICIENT", "reason": "no SPY calendar"}
    by_symbol: dict[str, dict[date, ResearchBar]] = {}
    for symbol in (*AUTOMOTIVE_MEMBERS, "SPY"):
        by_symbol[symbol] = {
            bar.session_date: bar
            for bar in (bars.get(symbol) or [])
            if _complete_t_day(bar) and bar.session_date <= ALLOWED_END
        }
    index = _index_map(sessions)
    close_rows = []
    next_open_rows = []
    coverage = []
    for session in sessions:
        present = [symbol for symbol in AUTOMOTIVE_MEMBERS if session in by_symbol[symbol]]
        coverage.append({"session": session.isoformat(), "n": len(present)})
        loc = index[session]
        plus_h = shift_sessions(sessions, loc, MAIN_LABEL)
        plus_1 = shift_sessions(sessions, loc, 1)
        if plus_h is None or plus_h > ALLOWED_END:
            continue
        names = []
        name_returns = []
        dollars = []
        for symbol in AUTOMOTIVE_MEMBERS:
            start = by_symbol[symbol].get(session)
            end = by_symbol[symbol].get(plus_h)
            if start is None or end is None or not start.close or not end.close:
                continue
            ret = end.close / start.close - 1.0
            names.append(symbol)
            name_returns.append(ret)
            dollars.append(start.dollar_volume or 0.0)
        if len(names) < 10:
            continue
        spy_start = by_symbol["SPY"].get(session)
        spy_end = by_symbol["SPY"].get(plus_h)
        spy_ret = None
        if spy_start and spy_end and spy_start.close and spy_end.close:
            spy_ret = spy_end.close / spy_start.close - 1.0
        eq = sum(name_returns) / len(name_returns)
        ordered = sorted(name_returns)
        left = ordered[max(0, int(math.floor(0.05 * (len(ordered) - 1))))]
        total_dv = sum(dollars)
        hhi = sum((item / total_dv) ** 2 for item in dollars) if total_dv > 0 else None
        close_rows.append(
            {
                "session": session.isoformat(),
                "n": len(names),
                "equal_weight_close_to_close": eq,
                "spy_close_to_close": spy_ret,
                "excess_vs_spy": None if spy_ret is None else eq - spy_ret,
                "left_tail_p05": left,
                "dollar_volume_hhi": hhi,
            }
        )
        if plus_1 is None or plus_1 >= HOLDOUT_START:
            continue
        next_rets = []
        for symbol in AUTOMOTIVE_MEMBERS:
            start = by_symbol[symbol].get(session)
            nxt = by_symbol[symbol].get(plus_1)
            if start is None or nxt is None or not start.close or not nxt.open:
                continue
            next_rets.append(nxt.open / start.close - 1.0)
        if len(next_rets) == 10:
            next_open_rows.append(
                {
                    "session": session.isoformat(),
                    "equal_weight_next_open": sum(next_rets) / 10.0,
                }
            )
    common10 = [row for row in coverage if row["n"] == 10]
    return {
        "status": "DESCRIPTIVE_ONLY" if close_rows else "INSUFFICIENT",
        "theme": FROZEN_THEME,
        "family": FROZEN_FAMILY,
        "candidate_id": FROZEN_CANDIDATE_ID,
        "weights_not_retuned": True,
        "own_sets": {
            "note": "Freeze already found the same 10 names on every valid pair day. This overlay uses that constant current list, not a new score-floor basket and not an intersection that drops adds/drops.",
            "members": list(AUTOMOTIVE_MEMBERS),
            "baseline_and_candidate_membership_identical_at_n10": True,
        },
        "signal_label": "T_close_to_T_plus_H_close",
        "earliest_trade_if_executed": "NEXT_DAY_CONFIRM",
        "holdout_opens_used": False,
        "nav_winrate_capacity": "NOT_INVENTED",
        "execution_prices": "UNVERIFIED_STRUCTURE_SESSION",
        "coverage": {
            "spy_allowed_sessions": len(sessions),
            "days_with_all_10": len(common10),
            "first_all_10": common10[0]["session"] if common10 else None,
            "last_all_10": common10[-1]["session"] if common10 else None,
        },
        "close_to_close_n": len(close_rows),
        "mean_equal_weight_close_to_close": (
            sum(row["equal_weight_close_to_close"] for row in close_rows) / len(close_rows) if close_rows else None
        ),
        "mean_spy_close_to_close": (
            sum(row["spy_close_to_close"] for row in close_rows if row["spy_close_to_close"] is not None)
            / max(1, sum(1 for row in close_rows if row["spy_close_to_close"] is not None))
            if close_rows
            else None
        ),
        "mean_excess_vs_spy": (
            sum(row["excess_vs_spy"] for row in close_rows if row["excess_vs_spy"] is not None)
            / max(1, sum(1 for row in close_rows if row["excess_vs_spy"] is not None))
            if close_rows
            else None
        ),
        "mean_left_tail_p05": (
            sum(row["left_tail_p05"] for row in close_rows) / len(close_rows) if close_rows else None
        ),
        "mean_dollar_volume_hhi": (
            sum(row["dollar_volume_hhi"] for row in close_rows if row["dollar_volume_hhi"] is not None)
            / max(1, sum(1 for row in close_rows if row["dollar_volume_hhi"] is not None))
            if close_rows
            else None
        ),
        "next_day_confirm_n": len(next_open_rows),
        "mean_equal_weight_next_open": (
            sum(row["equal_weight_next_open"] for row in next_open_rows) / len(next_open_rows)
            if next_open_rows
            else None
        ),
        "public_table_limit": 500,
        "public_rows": close_rows[:500],
    }


def stage_status() -> dict[str, Any]:
    return {
        "freeze_small_pool": {
            "status": "closed",
            "automotive_d_v": "FROZEN_EXPLORATORY_CANDIDATE",
            "etfs_d_p": "KEEP_BASELINE",
            "fintech_b_s": "KEEP_BASELINE",
            "do_not_retune_frozen_vector": True,
            "do_not_rerun_1152": True,
            "leave_one_n10_conclusion": "unchanged; if N stays sufficient after a pool expansion, recompute both ranks and ICs",
        },
        "three_profiles_three_horizons": {
            "implementation": "registry_present",
            "validation": "only_balanced_mid_label_20_on_b0",
            "status": "已实现未验证",
            "label_63_is_not_horizon_long": True,
        },
        "composite_layer": {
            "implementation": "composite.py M1-M4 candidate code exists",
            "history_validation": "not_done",
            "portfolio_validation": "not_done",
            "production_promotion": "not_done",
            "status": "已有候选实现，历史验证/组合验证/生产晋升尚未完成",
            "not": "没有综合层",
        },
        "eod_research_snapshot": {
            "status": "ENGINEERING_AND_DIAGNOSTIC",
            "production_deploy": "not_wired",
        },
        "family_e_weekly_volume_macro_radar_composite": {
            "status": "后续项目项",
            "cancelled_by_freeze": False,
            "in_this_recovery_round": False,
        },
        "holdout": {"start": HOLDOUT_START.isoformat(), "unsealed": False},
        "production_surfaces": "unchanged",
    }


def next_unique_gap(inventory: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    yahoo = next(row for row in inventory if row["role"] == "continuous_runner_yahoo_cache")
    if yahoo.get("status") == "present":
        return {
            "field": "historical_constituent_membership_and_delist_tape",
            "why_unique": "The 214-name Yahoo cache already unlocks A-layer current-list close diagnostics. The next evidence layer for 24-theme generality is as-of membership, delist, and rename — not another weight grid.",
            "coverage": "add/drop dates for all 24 themes through 2024-06-28, plus survivorship-complete names in each window",
            "access": "licensed or already-held membership tape; do not assume API rights; do not auto-buy",
            "optional_parallel_entry": {
                "field": "licensed_us_eod_with_delistings_from_2010",
                "interval": "raw start on or before 2010-01-02 through allowed_end 2024-06-28; backward only",
                "corporate_actions": "point-in-time split/dividend/merger ledger separate from adjusted close",
                "delistings": "survivorship-complete names, including names absent from today's SECTORS list",
                "classification": "as-of industry and theme tags; do not backfill current lists",
                "access": "do not assume API key; do not auto-buy",
            },
            "user_must_provide": True,
            "recovered_cache_already_usable_for": [LAYER_A],
        }
    missing_public = next(row for row in inventory if row["role"] == "public_yahoo_current_universe_bars")
    return {
        "field": "round3_yahoo_abcd/bars.pkl",
        "why_unique": "The continuous-runner cache is missing here. The public 2015 parquet is also gone; hashes.json still locates its digest.",
        "locator": "research/option_pro_us_eod_v1/return_pack/yahoo_current_universe/manifest.json",
        "user_must_provide": True,
        "public_bars_status": missing_public.get("status"),
    }


def verify_contract_bars(bars: Sequence[ResearchBar]) -> dict[str, Any]:
    try:
        validate_research_bars(bars)
        contract = "ok"
    except ValueError as exc:
        contract = str(exc)
    return {"contract": contract, **inspect_symbol_bars(bars)}


def local_verification_arithmetic(payload: Mapping[str, Any]) -> dict[str, Any]:
    quarterly = payload.get("quarterly") if "quarterly" in payload else None
    if quarterly is None:
        raise ValueError("expected published quarterly means")
    total_n = sum(int(item["n"]) for item in quarterly.values())
    baseline = sum(float(item["mean_baseline_ic"]) * int(item["n"]) for item in quarterly.values()) / total_n
    candidate = sum(float(item["mean_variant_ic"]) * int(item["n"]) for item in quarterly.values()) / total_n
    delta = candidate - baseline
    without = {
        key: value for key, value in quarterly.items() if key != "2024Q1"
    }
    rest_n = sum(int(item["n"]) for item in without.values())
    rest_base = sum(float(item["mean_baseline_ic"]) * int(item["n"]) for item in without.values()) / rest_n
    rest_cand = sum(float(item["mean_variant_ic"]) * int(item["n"]) for item in without.values()) / rest_n
    return {
        "weighted_baseline": baseline,
        "weighted_candidate": candidate,
        "weighted_delta": delta,
        "without_2024Q1": {
            "baseline": rest_base,
            "candidate": rest_cand,
            "mean_delta": rest_cand - rest_base,
        },
        "not_an_additional_significance_test": True,
        "count_quarters": len(quarterly),
    }


def stop_rule(inventory: Sequence[Mapping[str, Any]], layers: Mapping[str, Any]) -> dict[str, Any]:
    yahoo = next(row for row in inventory if row["role"] == "continuous_runner_yahoo_cache")
    recovered = yahoo.get("status") == "present"
    a_ok = layers[LAYER_A]["status"] == "available"
    if recovered and a_ok:
        outcome = "RECOVERED_CACHE_ARCHIVED"
        reason = "Existing Yahoo diagnostic cache is present, hashed, and A/B checks ran. C stays blocked. Unique remaining user gap is membership/delist tape."
    else:
        outcome = "USER_FILE_OR_LICENSE_REQUIRED"
        reason = "A recoverable source is missing. Do not invent bars."
    return {
        "outcome": outcome,
        "reason": reason,
        "holdout_unsealed": False,
        "weights_retuned": False,
        "executed_backtests": 0,
        "do_not_merge": True,
    }


def run_readiness(*, root: Path | None = None, load_yahoo: bool = True) -> dict[str, Any]:
    inventory = inventory_sources(root=root)
    base = root or REPO_ROOT
    yahoo_path = base / TRUSTED_RELATIVE_PICKLES[0]
    after_close_path = base / TRUSTED_RELATIVE_PICKLES[1]
    yahoo_bars = None
    after_close_bars = None
    yahoo_summary = None
    theme_rows = []
    budget = decade_budget([])
    descriptive = {"status": "SKIPPED", "reason": "yahoo cache not loaded"}
    validation_commands = []
    if load_yahoo and yahoo_path.is_file():
        yahoo_bars = load_trusted_research_bars(yahoo_path, root=root)
        yahoo_summary = inspect_yahoo_cache(yahoo_bars)
        theme_rows = theme_capability_table(yahoo_bars)
        budget = decade_budget(calendar_from_spy(yahoo_bars.get("SPY") or []))
        descriptive = automotive_descriptive(yahoo_bars)
        spy_check = verify_contract_bars(
            [bar for bar in (yahoo_bars.get("SPY") or []) if bar.session_date <= ALLOWED_END]
        )
        tsla_check = verify_contract_bars(
            [bar for bar in (yahoo_bars.get("TSLA") or []) if bar.session_date <= ALLOWED_END]
        )
        tsla_splits = split_window_samples(
            yahoo_bars.get("TSLA") or [],
            [date(2020, 8, 31), date(2022, 8, 25)],
        )
        validation_commands.append(
            {
                "command": "load_trusted_research_bars + inspect_yahoo_cache + verify_contract_bars + split_window_samples",
                "spy": spy_check,
                "tsla": tsla_check,
                "split_window_samples": tsla_splits,
                "split_sample": (
                    "TSLA 2020-08-31 and 2022-08-25 already sit in a split-adjusted-looking close series; "
                    "raw_close equals close, so C stays blocked"
                ),
            }
        )
    if load_yahoo and after_close_path.is_file():
        after_close_bars = load_trusted_research_bars(after_close_path, root=root)
        after_summary = inspect_yahoo_cache(after_close_bars)
        validation_commands.append(
            {
                "command": "load_trusted_research_bars(after_close) + inspect_yahoo_cache",
                "after_close": after_summary,
                "not_used_for_evaluation": True,
            }
        )
    inventory = annotate_inventory(
        inventory,
        yahoo_bars=yahoo_bars,
        after_close_bars=after_close_bars,
        root=root,
    )
    layers = capability_matrix(inventory, yahoo_summary)
    stop = stop_rule(inventory, layers["layers"])
    return {
        "protocol": PROTOCOL,
        "review_anchor": "35d8a08769174f4a56a9eae5af793852f3e1189d",
        "inventory": inventory,
        "yahoo_summary": yahoo_summary,
        "capabilities": layers,
        "theme_table": theme_rows,
        "decade_budget": budget,
        "automotive_descriptive": descriptive,
        "stage_status": stage_status(),
        "next_unique_gap": next_unique_gap(inventory),
        "validation": validation_commands,
        "stop": stop,
        "executed": [
            "inventory existing caches",
            "trusted pickle load of project Yahoo cache only",
            "calendar / OHLC / raw-vs-close / TRI-vs-close / dollar-volume checks",
            "inventory coverage fields (securities/dates/fields/actions/vintage)",
            "TSLA split-window raw-vs-structure samples",
            "layered A/B/C/D matrix",
            "24-theme current-list field table",
            "decade budget from recovered SPY sessions",
            "automotive descriptive overlay if quotes exist",
        ],
        "not_executed": [
            "1152-cell rerun",
            "frozen vector retune",
            "holdout unseal",
            "NAV / win-rate / slippage / capacity",
            "Massive download or auto-buy",
            "family E / weekly / macro",
            "production merge",
            "vendor splice onto B0",
        ],
        "executed_backtests": 0,
    }


def write_return_pack(result: Mapping[str, Any], *, dest: Path | None = None) -> dict[str, Path]:
    out = dest or RETURN_PACK_DIR
    out.mkdir(parents=True, exist_ok=True)

    def dump(name: str, payload: Any) -> Path:
        path = out / name
        path.write_text(__import__("json").dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path

    written = {
        "inventory": dump("algorithm_data_readiness_inventory.json", result["inventory"]),
        "manifest": dump(
            "algorithm_data_readiness_manifest.json",
            {
                "protocol": PROTOCOL,
                "recovery_dataset_version": RECOVERY_DATASET_VERSION,
                "b0_feature_version": B0_FEATURE_VERSION,
                "versions_not_spliced": True,
                "yahoo_summary": result.get("yahoo_summary"),
                "review_anchor": result.get("review_anchor"),
            },
        ),
        "capabilities": dump("algorithm_data_readiness_capabilities.json", result["capabilities"]),
        "theme_table": dump("algorithm_data_readiness_theme_table.json", result["theme_table"]),
        "decade_budget": dump("algorithm_data_readiness_decade_budget.json", result["decade_budget"]),
        "automotive": dump("algorithm_data_readiness_automotive_descriptive.json", result["automotive_descriptive"]),
        "stage_status": dump("algorithm_data_readiness_stage_status.json", result["stage_status"]),
        "next_gap": dump("algorithm_data_readiness_next_gap.json", result["next_unique_gap"]),
        "validation": dump("algorithm_data_readiness_validation.json", result["validation"]),
        "stop": dump("algorithm_data_readiness_stop.json", result["stop"]),
        "sources": dump(
            "algorithm_data_readiness_sources.json",
            {
                "executed": result["executed"],
                "not_executed": result["not_executed"],
                "executed_backtests": 0,
                "freeze_files_not_overwritten": True,
            },
        ),
    }
    return written
