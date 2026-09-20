"""Run LIMITED_CURRENT_UNIVERSE_V1 through existing research functions.

    PYTHONPATH=backend python research/option_pro_us_eod_v1/scripts/run_limited_current_universe_v1.py \\
      --dataset auto --session 2024-06-28 --replay-days 20 --profile balanced --horizon mid \\
      --out-dir research/option_pro_us_eod_v1/return_pack/limited_current_universe_v1_1
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.research_eod_v1.eod_shadow import read_snapshot  # noqa: E402
from app.services.research_eod_v1.limited_v1 import (  # noqa: E402
    ALLOWED_END,
    MODE,
    PACK_RELATIVE,
    read_preview_state,
    run_limited_v1,
)


def _parse() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=("auto", "yahoo_cache", "synthetic"), default="auto")
    parser.add_argument(
        "--session",
        default=None,
        help=f"development-zone trading day; omit to default to {ALLOWED_END.isoformat()}",
    )
    parser.add_argument("--replay-days", type=int, default=20)
    parser.add_argument("--profile", default="balanced")
    parser.add_argument("--horizon", default="mid")
    parser.add_argument("--out-dir", type=Path, default=ROOT / PACK_RELATIVE)
    parser.add_argument("--allow-network", action="store_true", help="fill missing current-list names via locked Yahoo params")
    parser.add_argument("--no-network", action="store_true")
    parser.add_argument("--smoke-864", action="store_true")
    parser.add_argument("--preview-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse()
    if args.preview_only:
        preview = args.out_dir / "preview.html"
        snap = args.out_dir / "research-eod-v1-snapshot.json"
        state = read_preview_state(args.out_dir)
        published = read_snapshot(snap) or {}
        print(json.dumps({
            "mode": MODE,
            "preview": str(preview),
            "snapshot": str(snap),
            "exists": preview.is_file(),
            "attempted_session": state.get("attempted_session"),
            "served_session": state.get("served_session") or published.get("session_date"),
            "integrity": state.get("integrity") or published.get("integrity"),
            "stale": bool(state.get("stale")),
            "synthetic": bool(state.get("synthetic") or published.get("synthetic")),
        }, indent=2))
        return 0 if preview.is_file() else 2
    allow_network = bool(args.allow_network) and not args.no_network
    if args.dataset == "yahoo_cache":
        allow_network = False
    if args.dataset == "synthetic":
        allow_network = False
    report = run_limited_v1(
        root=ROOT,
        out_dir=args.out_dir,
        session=date.fromisoformat(args.session) if args.session else None,
        replay_days=max(1, int(args.replay_days)),
        profile=args.profile,
        horizon=args.horizon,
        allow_network=allow_network,
        smoke=args.smoke_864,
        synthetic=args.dataset == "synthetic",
    )
    print(json.dumps({k: v for k, v in report.items() if k not in {"coverage", "load"}}, indent=2, default=str))
    return 0 if report.get("status") == "RAN" else 2


if __name__ == "__main__":
    raise SystemExit(main())
