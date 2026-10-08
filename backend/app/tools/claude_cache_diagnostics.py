"""Read local cache diagnostics without creating a client or making API calls."""
from __future__ import annotations

import argparse
from collections import Counter
import json

from app.services.claude_cache_diagnostics import DiagnosticsStore, verdict


def main() -> None:
    parser = argparse.ArgumentParser(description="Read Claude cache diagnostics (no model calls)")
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--task", default=None)
    args = parser.parse_args()
    snapshot = DiagnosticsStore(args.data_dir).read()
    rows = [row | {"verdict": verdict(row)} for row in snapshot["records"] if args.task is None or row["task"] == args.task]
    print(json.dumps({"available": snapshot["available"], "summary": dict(Counter(row["verdict"] for row in rows)),
                      "records": rows[-max(1, min(200, args.limit)):]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
