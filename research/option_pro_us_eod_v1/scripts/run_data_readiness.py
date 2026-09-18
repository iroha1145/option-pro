"""Inventory recovered caches and write the data-readiness pack.

Does not overwrite B0 / Round 1 / Round 1b / Round 2 / freeze public files.
Does not retune the frozen vector or open a new weight grid.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from app.services.research_eod_v1.data_readiness import (  # noqa: E402
    PROTOCOL,
    RECOVERY_DATASET_VERSION,
    run_readiness,
    write_return_pack,
)
from app.services.research_eod_v1.source_bind import research_code_hashes  # noqa: E402


def _head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def _audit(result: dict, head: str) -> str:
    summary = result.get("yahoo_summary") or {}
    spy = summary.get("spy") or {}
    budget = result["decade_budget"]
    gap = result["next_unique_gap"]
    stop = result["stop"]
    auto = result["automotive_descriptive"]
    lines = [
        "# PR #174 data readiness",
        "",
        f"Head `{head}`. Review anchor `35d8a08769174f4a56a9eae5af793852f3e1189d`. Protocol `{PROTOCOL}`.",
        f"Recovery dataset `{RECOVERY_DATASET_VERSION}` is not spliced onto B0.",
        "B0 / Round 1 / Round 1b / Round 2 / freeze files are not rewritten. Holdout 2024-07-01 stays sealed.",
        "",
        "## Inventory",
        "",
    ]
    for row in result["inventory"]:
        lines.append(
            f"- `{row.get('path')}` role={row.get('role')} status={row.get('status')} "
            f"sha256={row.get('sha256') or 'n/a'} securities_n={row.get('securities_n')} "
            f"first={row.get('first_session')} last={row.get('last_session')} "
            f"last_allowed={row.get('last_allowed_session')} "
            f"actions={row.get('corporate_actions')} vintage={row.get('vintage_status')}"
        )
    lines.extend(
        [
            "",
            "## Recovered Yahoo cache",
            "",
            f"- symbols {summary.get('symbols')} bars {summary.get('bar_rows')} ohlc_violations {summary.get('ohlc_violations')}",
            f"- SPY allowed complete T-days {spy.get('allowed_n')} first {spy.get('first')} last_allowed {spy.get('last_allowed')}",
            f"- raw_ne_close_bars {summary.get('raw_ne_close_bars')} (C stays blocked)",
            "",
            "## Split-window samples",
            "",
        ]
    )
    for command in result.get("validation") or []:
        samples = command.get("split_window_samples") or []
        for sample in samples:
            lines.append(f"- TSLA {sample.get('event')}: {sample.get('invariant')}")
    lines.extend(
        [
            "",
            "## Decade budget",
            "",
            f"- raw_start {budget.get('raw_start')} allowed_end {budget.get('allowed_end')} span_years {budget.get('raw_span_years')}",
            f"- ten_year_evaluable {budget.get('ten_year_evaluable')}",
            f"- D first_scoreable {budget['families']['D_residual_momentum']['first_scoreable_day']}",
            f"- D label 20 mature {budget['families']['D_residual_momentum']['labels']['20']['mature_label_day']}",
            "",
            "## Automotive descriptive",
            "",
            f"- status {auto.get('status')} close_to_close_n {auto.get('close_to_close_n')} next_day_confirm_n {auto.get('next_day_confirm_n')}",
            f"- mean equal-weight T→T+20 {auto.get('mean_equal_weight_close_to_close')}",
            f"- mean excess vs SPY {auto.get('mean_excess_vs_spy')}",
            f"- NAV {auto.get('nav_winrate_capacity')}",
            "",
            "## Stage labels",
            "",
            f"- composite: {result['stage_status']['composite_layer']['status']}",
            f"- three profiles / horizons: {result['stage_status']['three_profiles_three_horizons']['status']}",
            "",
            "## Stop",
            "",
            f"- {stop['outcome']}: {stop['reason']}",
            f"- unique gap: {gap.get('field')}",
            "",
            "No production champion. No unseal. No new weight grid.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    head = _head()
    result = run_readiness(root=ROOT, load_yahoo=True)
    written = write_return_pack(result)
    pack = ROOT / "research" / "option_pro_us_eod_v1" / "return_pack"
    audit = _audit(result, head)
    (pack / "algorithm_data_readiness_audit.md").write_text(audit, encoding="utf-8")
    (pack / "algorithm_data_readiness_head.json").write_text(
        json.dumps(
            {
                "head": head,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "code_hashes": research_code_hashes(),
                "protocol": PROTOCOL,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "stop": result["stop"]["outcome"],
                "symbols": (result.get("yahoo_summary") or {}).get("symbols"),
                "gap": result["next_unique_gap"]["field"],
                "written": sorted(path.name for path in written.values()),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
