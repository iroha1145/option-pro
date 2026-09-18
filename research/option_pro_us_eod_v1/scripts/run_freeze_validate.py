"""Freeze the unique Round 2 candidate and validate it.

Does not overwrite B0, Round 1, Round 1b, or Round 2 public files.
Does not open a new weight grid. Checklist is written before daily effects.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from app.services.research_eod_v1 import FEATURE_VERSION  # noqa: E402
from app.services.research_eod_v1.bootstrap import BOOTSTRAP_SEED  # noqa: E402
from app.services.research_eod_v1.config_load import load_registry  # noqa: E402
from app.services.research_eod_v1.freeze import (  # noqa: E402
    CHECKLIST,
    FROZEN_CANDIDATE_ID,
    FREEZE_PROTOCOL,
    checklist_manifest,
    decide_stop,
    etfs_parameter_correction,
    freeze_snapshot,
    load_frozen_candidate,
    next_stage_data_requirements,
    project_gaps,
    public_validation_table,
    theme_family_status,
    validate_frozen_candidate,
)
from app.services.research_eod_v1.round1b import MAIN_LABEL_HORIZON  # noqa: E402
from app.services.research_eod_v1.runs import run_dir_name, run_signature  # noqa: E402
from app.services.research_eod_v1.source_bind import (  # noqa: E402
    EXPECTED_B0_SHA256,
    bind_b0_tape,
    bind_limited_prefix,
    research_code_hashes,
    sha256_file,
)
from app.services.research_eod_v1.stats import STATISTICS_VERSION  # noqa: E402

PACK = ROOT / "research" / "option_pro_us_eod_v1" / "return_pack"
ROWS = PACK / "measurement_factor_rows.jsonl"
R2_DAILY = PACK / "round2_runs" / "run_e3fbb87eaf69ead5_full_tape" / "daily_paired_diffs.jsonl"
R2_CANDIDATES = PACK / "algorithm_round2_candidates.json"
R2_CARDS = PACK / "algorithm_round2_theme_cards.json"
RUNS = PACK / "round2_freeze_runs"
CACHE = ROOT / "research" / "option_pro_us_eod_v1" / "data" / "cache" / "offline_replay" / "daily_bars.parquet"
ALLOWED_END = "2024-06-28"
HOLDOUT_START = "2024-07-01"
REVIEW_ANCHOR = "6159b2388d5bf36eefa7f7aba6c12455c3817585"
FROZEN_FAMILY = "D_residual_momentum"
FROZEN_THEME = "automotive"


def _git_sha() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def _rel(path: Path) -> str:
    return str(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path)


def _cache_status() -> dict[str, object]:
    if not CACHE.exists():
        return {
            "path": _rel(CACHE),
            "status": "MISSING",
            "execution_prices": "MISSING",
            "nav_winrate_capacity": "NOT_INVENTED",
            "reason": "no local bar cache",
        }
    try:
        import pandas as pd

        frame = pd.read_parquet(CACHE)
        names = sorted({str(item) for item in frame["security_id"].tolist()})
        return {
            "path": _rel(CACHE),
            "rows": int(len(frame)),
            "securities": names,
            "status": "INSUFFICIENT",
            "execution_prices": "MISSING",
            "nav_winrate_capacity": "NOT_INVENTED",
            "reason": (
                f"cache has {len(frame)} rows for {names}; not the automotive eligible set, "
                "and vintage_status is download_time_not_pit. Descriptive forward is skipped."
            ),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "path": _rel(CACHE),
            "status": "MISSING",
            "execution_prices": "MISSING",
            "nav_winrate_capacity": "NOT_INVENTED",
            "reason": f"cache unreadable: {exc}",
        }


def write_prevalidate(head: str) -> dict[str, object]:
    """Write the checklist and inherited status before any daily effect is read."""

    registry = load_registry()
    frozen_row = load_frozen_candidate(R2_CANDIDATES)
    snapshot = freeze_snapshot(frozen_row)
    executed = json.loads(R2_CANDIDATES.read_text(encoding="utf-8"))
    etfs = next(item for item in executed if item["theme"] == "etfs")
    fintech = next(item for item in executed if item["theme"] == "fintech")
    cards = json.loads(R2_CARDS.read_text(encoding="utf-8"))
    checklist = checklist_manifest(head=head, daily_effects_read=False)
    payload = {
        "round": "algorithm_round2_freeze",
        "protocol": FREEZE_PROTOCOL,
        "reviewed_anchor": REVIEW_ANCHOR,
        "head": head,
        "written_at": datetime.now(timezone.utc).isoformat(),
        "holdout_start": HOLDOUT_START,
        "holdout_unsealed": False,
        "allowed_end": ALLOWED_END,
        "executed_backtests": 0,
        "daily_effects_read": False,
        "r2_files_not_overwritten": True,
        "frozen": snapshot,
        "etfs_parameter_correction": etfs_parameter_correction(registry, etfs),
        "fintech_keep_baseline": {
            "candidate_id": fintech["candidate_id"],
            "status": "keep_baseline",
            "eligible_signal_n": fintech.get("eligible_signal_n"),
            "keep_historical_result": True,
            "retune": False,
        },
        "theme_family_status": theme_family_status(cards),
        "project_gaps": project_gaps(),
        "next_stage_data_requirements": next_stage_data_requirements(),
        "local_execution_cache": _cache_status(),
        "checklist": checklist,
    }
    _write(PACK / "algorithm_round2_freeze_checklist.json", checklist)
    _write(PACK / "algorithm_round2_freeze_snapshot.json", snapshot)
    _write(PACK / "algorithm_round2_freeze_etfs_correction.json", payload["etfs_parameter_correction"])
    _write(PACK / "algorithm_round2_freeze_theme_status.json", payload["theme_family_status"])
    _write(PACK / "algorithm_round2_freeze_project_gaps.json", payload["project_gaps"])
    _write(PACK / "algorithm_round2_freeze_next_data.json", payload["next_stage_data_requirements"])
    _write(PACK / "algorithm_round2_freeze_prevalidate.json", payload)
    return payload


def _sessions_from_r2_header() -> list[str] | None:
    if not R2_DAILY.exists():
        return None
    with R2_DAILY.open("r", encoding="utf-8") as handle:
        header = json.loads(handle.readline())
    sessions = header.get("sessions")
    if not isinstance(sessions, list) or not sessions:
        return None
    return [str(item) for item in sessions]


def _iter_frozen_rows(path: Path, *, limit: int | None):
    count = 0
    with path.open("rb") as handle:
        for raw in handle:
            count += 1
            if count % 200_000 == 0:
                print(json.dumps({"rows_read": count}), flush=True)
            row = json.loads(raw)
            if (
                str(row.get("theme_id")) == FROZEN_THEME
                and str(row.get("algorithm")) == FROZEN_FAMILY
                and int(row.get("label_horizon") or 0) == MAIN_LABEL_HORIZON
            ):
                yield row
            if limit is not None and count >= limit:
                return


def _write_daily(out: Path, result: dict, *, public: bool) -> dict[str, object]:
    private = out / "daily_validation.jsonl"
    with private.open("w", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "candidate_id": FROZEN_CANDIDATE_ID,
                    "sessions": result["sessions"],
                    "protocol": FREEZE_PROTOCOL,
                },
                separators=(",", ":"),
            )
            + "\n"
        )
        for row in result["daily"]:
            handle.write(json.dumps(row, separators=(",", ":"), default=str) + "\n")
    digest = sha256_file(private)
    meta = {
        "path": _rel(private),
        "sha256": digest,
        "rows": len(result["daily"]) + 1,
        "defined_pair_days": result["valid_pair_days"],
        "public_git": False,
        "note": "Controlled frozen-candidate daily artifact. Raw B0 tape is not copied here.",
    }
    _write(out / "daily_validation.sha256.json", meta)
    if public:
        _write(PACK / "algorithm_round2_freeze_daily.sha256.json", meta)
    return meta


def _public_result(result: dict, daily_meta: dict, stop: dict, head: str, data_hash: str, signature: str) -> dict:
    payload = {
        key: value
        for key, value in result.items()
        if key not in {"daily", "sessions"}
    }
    payload.update(
        {
            "head": head,
            "source_row_sha256": data_hash,
            "run_signature": signature,
            "daily_validation": daily_meta,
            "stop": stop,
            "executed_backtests": 0,
            "holdout_unsealed": False,
            "r2_files_not_overwritten": True,
            "local_execution_cache": _cache_status(),
            "checklist_items": list(CHECKLIST),
        }
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checklist-only", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args()
    head = _git_sha()
    pre = write_prevalidate(head)
    if args.checklist_only:
        print(json.dumps({"mode": "checklist_only", "head": head, "daily_effects_read": False}, indent=2))
        return
    if not ROWS.exists():
        missing = {
            "status": "NOT_VERIFIABLE",
            "gaps": ["missing frozen B0 tape"],
            "stop": decide_stop({"valid_pair_days": 0}),
        }
        _write(PACK / "algorithm_round2_freeze_validation.json", missing)
        raise SystemExit("missing frozen B0 tape")
    if args.limit is None:
        data_hash = bind_b0_tape(ROWS)
        kind = "full_tape"
    else:
        data_hash = bind_limited_prefix(ROWS, limit=args.limit)
        kind = f"limit_{args.limit}"
    registry = load_registry()
    frozen_row = load_frozen_candidate(R2_CANDIDATES)
    snapshot = freeze_snapshot(frozen_row)
    code_hashes = research_code_hashes()
    signature = run_signature(
        registry=registry,
        profile="balanced",
        horizon="mid",
        label_horizons=(MAIN_LABEL_HORIZON,),
        feature_version=FEATURE_VERSION,
        statistics_version=STATISTICS_VERSION,
        data_hash=data_hash,
        universe_version="u_measurement_continuous",
        member_policy="known_theme_members",
        reference_policy="same_track_t_complete_plus_spy_qqq",
        start="2018-01-02",
        end=ALLOWED_END,
        available_factors=("T", "M", "S", "B", "P", "V", "R"),
        timing_policy="NEXT_DAY_CONFIRM",
        registry_version=registry.get("schema_version"),
        capability_mask={"actual_G_observed": False, "tracks": ["PRICE_ONLY_DIAGNOSTIC"]},
        variant_definitions=[{"variant_id": FROZEN_CANDIDATE_ID, "kind": "freeze_replay"}],
        event_rule="us-eod-event-groups-v1.1",
        pairing_rule="common_member_pair",
        statistics_rule="us-eod-research-stats-v1.1",
        block_lengths={"H": "label_horizon", "2H": "2*label_horizon", "trim": "original_timeline_n"},
        bootstrap_seed=BOOTSTRAP_SEED,
        code_hashes=code_hashes,
        require_content_hash=True,
    )
    out = args.out_dir if args.out_dir is not None else RUNS / f"{run_dir_name(signature)}_{kind}"
    out.mkdir(parents=True, exist_ok=True)
    public = args.limit is None
    sessions = _sessions_from_r2_header()
    result = validate_frozen_candidate(
        _iter_frozen_rows(ROWS, limit=args.limit),
        registry,
        frozen=frozen_row,
        sessions=sessions,
    )
    stop = decide_stop(result)
    daily_meta = _write_daily(out, result, public=public)
    table = public_validation_table(result, limit=500)
    public_payload = _public_result(result, daily_meta, stop, head, data_hash, signature)
    public_payload["frozen"] = snapshot
    public_payload["etfs_parameter_correction"] = pre["etfs_parameter_correction"]
    public_payload["theme_family_status_n"] = len(pre["theme_family_status"])
    public_payload["project_gaps"] = pre["project_gaps"]
    _write(out / "manifest.json", {"head": head, "run_signature": signature, "source_row_sha256": data_hash, "kind": kind})
    _write(out / "validation.json", public_payload)
    _write(out / "table.json", table)
    _write(out / "stop.json", stop)
    if public:
        _write(PACK / "algorithm_round2_freeze_validation.json", public_payload)
        _write(PACK / "algorithm_round2_freeze_table.json", table)
        _write(PACK / "algorithm_round2_freeze_stop.json", stop)
        yearly_lines = [
            f"- {year}: n={row['n']} mean_delta={row['mean_delta']} baseline_ic={row['mean_baseline_ic']} variant_ic={row['mean_variant_ic']} ({row['direction']})"
            for year, row in (result.get("yearly") or {}).items()
        ]
        leave_lines = [
            f"- {item['security_id']}: present={item['days_present']} lost_below_n={item['days_lost_below_n']} remain={item['remaining_defined_days']} thin={item['remaining_statistically_thin']}"
            for item in (result.get("leave_one_member") or [])
        ]
        audit = [
            "# PR #174 freeze and validate",
            "",
            f"Head `{head}`. Review anchor `{REVIEW_ANCHOR}`. Protocol `{FREEZE_PROTOCOL}`.",
            "B0 / Round 1 / Round 1b / Round 2 files are not rewritten. Holdout 2024-07-01 stays sealed. executed_backtests = 0.",
            "",
            "## Frozen object",
            "",
            f"- candidate `{FROZEN_CANDIDATE_ID}`",
            f"- V {snapshot['before_V']} -> {snapshot['after_V']} (requested -5pp, relative {snapshot['relative_reduction']})",
            "- control PRICE_ONLY_DIAGNOSTIC / alias D_MARKET_RESIDUAL_DIAGNOSTIC",
            "- ETF D/P historical HALVE_AT_BOUNDARY correction kept; future policy REJECT_INFEASIBLE",
            "- fintech B/S keep baseline",
            "",
            "## Validation",
            "",
            f"- timeline_n {result['timeline_n']}; valid_pair_days {result['valid_pair_days']}; start {result['valid_start']}; end {result['valid_end']}",
            f"- rejection_counts {result['rejection_counts']}",
            f"- mean_common_n {result['mean_common_n']}",
            f"- mean_delta {result['mean_delta']}",
            f"- mean_baseline_ic_common {result['mean_baseline_ic_common']}",
            f"- mean_variant_ic_common {result['mean_variant_ic_common']}",
            f"- eligible_signal_records {result['eligible_signal_records']}; matured_label {result['eligible_matured_label_records']}; overlap_groups {result['event_overlap_groups']}",
            f"- daily sha256 `{daily_meta['sha256']}`",
            f"- public table rows {len(table)} (limit 500)",
            f"- execution_prices {result['execution_prices']}; nav_winrate_capacity {result['nav_winrate_capacity']}",
            f"- stop `{stop['outcome']}`: {stop['reason']}",
            "",
            "## Yearly (all retained)",
            "",
            *(yearly_lines or ["- none"]),
            "",
            "## Leave-one member (N=10 to N=9 is insufficient)",
            "",
            *(leave_lines or ["- none"]),
            "",
            "## Project gaps",
            "",
            *[f"- {item['gap']}: {item['status']}" for item in project_gaps()],
            "",
            "No production champion. No unseal. No new weight grid.",
            "",
        ]
        text = "\n".join(audit) + "\n"
        (PACK / "algorithm_round2_freeze_audit.md").write_text(text, encoding="utf-8")
        (out / "audit.md").write_text(text, encoding="utf-8")
    print(
        json.dumps(
            {
                "head": head,
                "run_signature": signature,
                "data_hash": data_hash,
                "run_dir": str(out),
                "valid_pair_days": result["valid_pair_days"],
                "stop": stop["outcome"],
                "daily_sha256": daily_meta["sha256"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
