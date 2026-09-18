"""Run the fixed 864-config coverage / signal diagnostic.

Does not overwrite B0 / Round 1 / Round 1b / Round 2 / freeze / readiness files.
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

from app.services.research_eod_v1.fixed_matrix import (  # noqa: E402
    PROTOCOL,
    run_fixed_matrix,
    write_return_pack,
)
from app.services.research_eod_v1.source_bind import research_code_hashes  # noqa: E402


def _head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def _audit(result: dict, head: str) -> str:
    calendar = result["calendar"]
    quality = result["quality"]
    progress = result["progress"]
    mid = result["mid_rescore"]
    gap = result["layer_gaps"]
    lines = [
        "# PR #174 fixed matrix and signal validation",
        "",
        f"Head `{head}`. Review anchor `c8cd25931f007ce826c7b1e3cd1a1726d48ffae0`. Protocol `{PROTOCOL}`.",
        "B0 / Round 1 / Round 1b / Round 2 / freeze / readiness files are not rewritten.",
        "Holdout 2024-07-01 stays sealed. No new weight search.",
        "",
        "## Calendar",
        "",
        f"- version {calendar.get('calendar_version')} official_n {calendar.get('official_n')} spy_n {calendar.get('spy_complete_n')}",
        f"- 2018-12-05 closed {calendar['exception_2018_12_05']['official_closed']}",
        f"- canonical T+20 from 2024-04-01 {calendar.get('canonical_t20_2024_04_01')}",
        "",
        "## Quality",
        "",
        f"- valid {quality.get('valid_n')} invalid {quality.get('invalid_n')} insufficient {quality.get('insufficient_n')} spy {quality.get('spy_status')}",
        f"- isolated outliers {sorted((quality.get('isolated_outliers') or {}).keys())}",
        f"- execution gates {quality.get('execution_gates')}",
        "",
        "## 864 plan",
        "",
        f"- plan_n {result.get('plan_n')} structured {progress.get('structured_n')} scored {progress.get('scored_n')} score_pending {progress.get('score_pending_n')}",
        f"- pending_reason {progress.get('pending_reason')}",
        f"- signature {progress.get('signature')}",
        "",
        "## Selection vs background",
        "",
        f"- selection {result['selection_sets'].get('status')} own-identical {result['selection_sets'].get('own_sets_identical_days')} differ {result['selection_sets'].get('own_sets_differ_days')}",
        f"- background {result['background_basket']['statistic']}",
        f"- NEXT_DAY_CONFIRM earliest {result['next_day_confirm']['actual_helper_earliest_entry']}",
        f"- p05 {result['background_basket']['left_tail_p05']['meaning']}",
        "",
        "## Gaps",
        "",
    ]
    for item in gap.get("split_tracks") or []:
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "No production champion. No unseal. No new weight grid.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    head = _head()
    result = run_fixed_matrix(root=ROOT, load_yahoo=True, rescore_b0=True)
    written = write_return_pack(result)
    pack = ROOT / "research" / "option_pro_us_eod_v1" / "return_pack"
    (pack / "algorithm_fixed_matrix_audit.md").write_text(_audit(result, head), encoding="utf-8")
    (pack / "algorithm_fixed_matrix_head.json").write_text(
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
                "plan_n": result["plan_n"],
                "official_n": result["calendar"]["official_n"],
                "completed_n": result["progress"]["completed_n"],
                "pending_n": result["progress"]["pending_n"],
                "written": sorted(path.name for path in written.values()),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
