#!/usr/bin/env python3
"""Generate the first-round return pack. Does not invent market returns."""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[3] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.research_eod_v1.reporting import build_return_pack  # noqa: E402


def main() -> None:
    log = Path(sys.argv[1]).read_text(encoding="utf-8") if len(sys.argv) > 1 else ""
    summary = build_return_pack(test_log=log)
    print(summary)


if __name__ == "__main__":
    main()
