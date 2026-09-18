"""Probe local / Yahoo / optional Massive env. Never print secrets."""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.research_eod_v1.data.probe import run_provider_probe  # noqa: E402


def main() -> int:
    allow = os.environ.get("RESEARCH_EOD_ALLOW_NETWORK", "1").strip() not in {"0", "false", "no"}
    report = run_provider_probe(
        allow_network=allow,
        start=date(2020, 1, 2),
        end=date(2020, 3, 2),
    )
    text = json.dumps(report, indent=2, default=str)
    lowered = text.lower()
    for forbidden in ("bearer ", "apikey=", "api_key="):
        if forbidden in lowered:
            raise SystemExit("refusing to write a probe report that looks like it contains a secret")
    out = Path(__file__).resolve().parents[1] / "return_pack" / "provider_probe.json"
    out.write_text(text + "\n", encoding="utf-8")
    print(json.dumps({"selected_provider": report.get("selected_provider"), "sample_n": len(report.get("sample") or [])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
