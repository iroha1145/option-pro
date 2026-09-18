"""After-close last-bar recapture. New version only; old retrieved_at stays frozen.

Large bars stay in the gitignored cache. This script writes metadata and hashes.
Yahoo finalization is not assumed just because the NYSE clock is past 16:00.
"""

from __future__ import annotations

import hashlib
import json
import pickle
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.research_eod_v1.calendar_asof import (  # noqa: E402
    LIVE_CAPTURE,
    VENDOR_WITHOUT_FINALIZED_FIELD_POLICY,
    capture_as_of,
    disclosed_source_finalized_through,
    last_complete_eod_session,
    last_completed_session,
)
from app.services.research_eod_v1.data.capture_store import ImmutableCaptureStore  # noqa: E402
from app.services.research_eod_v1.data.yahoo import YahooDiagnosticProvider  # noqa: E402
from app.services.sectors import SECTORS  # noqa: E402

INVALID_RETRIEVED = datetime.fromisoformat("2026-09-16T17:24:41.368841+00:00")
KNOWN_FINALIZED = date(2026, 9, 15)
START = date(2026, 8, 20)
END = date(2026, 9, 17)
CACHE = ROOT / "research" / "option_pro_us_eod_v1" / "data" / "cache" / "round3_after_close"
OUT = ROOT / "research" / "option_pro_us_eod_v1" / "return_pack" / "round3_after_close_capture.json"
MANIFEST = ROOT / "research" / "option_pro_us_eod_v1" / "return_pack" / "yahoo_current_universe" / "manifest.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    prior = json.loads(MANIFEST.read_text(encoding="utf-8"))
    prior_retrieved = prior["retrieved_at"]
    clock = capture_as_of(datetime.now(timezone.utc))
    calendar_session = last_completed_session(clock)
    eod_session = disclosed_source_finalized_through(clock)
    tickers: list[str] = []
    for sector in SECTORS.values():
        for ticker in sector["tickers"]:
            if ticker not in tickers:
                tickers.append(ticker)
    for extra in ("SPY", "QQQ"):
        if extra not in tickers:
            tickers.append(extra)

    CACHE.mkdir(parents=True, exist_ok=True)
    provider = YahooDiagnosticProvider(allow_network=True, clock=clock)
    batched: dict[str, list] = {}
    for offset in range(0, len(tickers), 40):
        batched.update(provider.fetch_daily_bars_batch(tickers[offset : offset + 40], START, END))

    cache_path = CACHE / "last_bars.pkl"
    cache_path.write_bytes(pickle.dumps(batched))
    all_bars = [bar for bars in batched.values() for bar in bars]
    last_session_bars = [bar for bar in all_bars if bar.session_date == calendar_session]
    isolated_partial = [
        bar
        for bar in all_bars
        if bar.partial or bar.vintage_status == "PARTIAL" or bar.session_date > eod_session
    ]

    store = ImmutableCaptureStore()
    predecessor = store.record_capture(
        clock=INVALID_RETRIEVED,
        bars=(),
        claimed_session=date(2026, 9, 16),
        notes=("archived_invalid_eod_manifest", "bars_not_in_public_git"),
        stamp=False,
        capture_mode=LIVE_CAPTURE,
    )
    recapture = store.recapture_last_bar(
        predecessor_id=predecessor.capture_id,
        clock=clock,
        bars=all_bars,
        claimed_session=eod_session,
        notes=("yahoo_last_bar_recapture", "vendor_finalization_unproven", VENDOR_WITHOUT_FINALIZED_FIELD_POLICY),
        capture_mode=LIVE_CAPTURE,
    )
    stored_pred = store.get(predecessor.capture_id)
    after_manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if after_manifest["retrieved_at"] != prior_retrieved:
        raise RuntimeError("old manifest retrieved_at was mutated")
    if stored_pred.retrieved_at != INVALID_RETRIEVED:
        raise RuntimeError("predecessor retrieved_at was mutated")

    report = {
        "head_note": "after-close recapture is a new version; old 13:24 retrieved_at stays frozen",
        "capture_clock": clock.isoformat(),
        "calendar_last_completed_session": calendar_session.isoformat(),
        "last_complete_eod_session": recapture.last_complete_eod_session.isoformat(),
        "last_proven_finalized": KNOWN_FINALIZED.isoformat(),
        "vendor_finalization_policy": VENDOR_WITHOUT_FINALIZED_FIELD_POLICY,
        "disclosed_source_finalized_through": eod_session.isoformat(),
        "capture_mode": recapture.capture_mode,
        "eod_status": recapture.eod_status,
        "capture_id": recapture.capture_id,
        "predecessor_id": recapture.predecessor_id,
        "predecessor_retrieved_at": stored_pred.retrieved_at.isoformat(),
        "recapture_retrieved_at": recapture.retrieved_at.isoformat(),
        "old_manifest_retrieved_at": prior_retrieved,
        "old_manifest_retrieved_at_unchanged": after_manifest["retrieved_at"] == prior_retrieved,
        "requested_tickers": len(tickers),
        "downloaded_tickers": sum(1 for bars in batched.values() if bars),
        "bar_count": len(all_bars),
        "calendar_session_bar_count": len(last_session_bars),
        "isolated_unfinalized_or_partial_bars": len(isolated_partial),
        "content_sha256": recapture.content_sha256,
        "cache_sha256": _sha256(cache_path),
        "cache_path": "research/option_pro_us_eod_v1/data/cache/round3_after_close/last_bars.pkl",
        "provider_failures": provider.failures,
        "calendar_only_session_without_finalization": last_complete_eod_session(clock).isoformat(),
        "executed_snapshots": 0,
        "executed_backtests": 0,
        "winners": [],
        "notes": [
            "Yahoo last-root recapture after the regular 16:00 ET close",
            "vendor finalized_at is unavailable; 2026-09-16 is not promoted to COMPLETE_EOD",
            "old INVALID_EOD_CAPTURE retrieved_at was not rewritten",
            "tape is gitignored; only hashes and metadata are committed",
        ],
        "secret_present": False,
    }
    OUT.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "capture_clock": report["capture_clock"],
                "last_complete_eod_session": report["last_complete_eod_session"],
                "eod_status": report["eod_status"],
                "downloaded_tickers": report["downloaded_tickers"],
                "isolated_unfinalized_or_partial_bars": report["isolated_unfinalized_or_partial_bars"],
                "old_manifest_retrieved_at_unchanged": report["old_manifest_retrieved_at_unchanged"],
                "out": str(OUT),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
