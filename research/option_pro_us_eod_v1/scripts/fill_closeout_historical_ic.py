"""Recompute signal IC from executed eligible snapshots. Cache replay only."""

from __future__ import annotations

import json
import pickle
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.services.research_eod_v1.calendar_asof import eod_evaluation_as_of, shift_sessions  # noqa: E402
from app.services.research_eod_v1.config_load import load_registry  # noqa: E402
from app.services.research_eod_v1.data.to_series import bars_to_series  # noqa: E402
from app.services.research_eod_v1.snapshot import compute_snapshot  # noqa: E402
from app.services.research_eod_v1.universe_audit import ETF_SUBASSET_HINTS  # noqa: E402
from app.services.sectors import SECTORS  # noqa: E402

from run_closeout_historical_abcd import (  # noqa: E402
    ALLOWED_END,
    CACHE,
    HOLDOUT_START,
    LABELS,
    LOG,
    OUT,
    _forward_return,
    _spearman,
    _tail_history,
    _venue,
)


def main() -> int:
    report = json.loads(OUT.read_text(encoding="utf-8"))
    batched = pickle.loads(CACHE.read_bytes())
    appearances: dict[str, list[str]] = {}
    for theme_id, sector in SECTORS.items():
        for ticker in sector["tickers"]:
            appearances.setdefault(ticker, []).append(theme_id)
    panel = {}
    for ticker, themes in appearances.items():
        track = "etf" if ticker in ETF_SUBASSET_HINTS or themes == ["etfs"] else "stock"
        bars = [bar for bar in (batched.get(ticker) or []) if bar.session_date <= ALLOWED_END]
        if not bars:
            continue
        series = bars_to_series(
            bars,
            security_id=ticker,
            asset_track=track,
            theme_ids=tuple(themes),
            industry_id=themes[0],
            parent_industry_id=themes[0],
            venue_metadata=_venue(track),
        )
        if series is not None:
            panel[ticker] = series
    for extra in ("SPY", "QQQ"):
        if extra in panel or extra not in batched:
            continue
        bars = [bar for bar in batched[extra] if bar.session_date <= ALLOWED_END]
        series = bars_to_series(
            bars,
            security_id=extra,
            asset_track="etf",
            theme_ids=("etfs",),
            venue_metadata=_venue("etf"),
        )
        if series is not None:
            panel[extra] = series

    needed = []
    for line in LOG.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("eligible", 0) <= 0:
            continue
        session = date.fromisoformat(row["session"])
        if session >= HOLDOUT_START:
            raise SystemExit(f"holdout leaked: {session}")
        needed.append(row)
    label_cut = {horizon: shift_sessions(ALLOWED_END, -horizon) for horizon in LABELS}
    pairs: dict[str, dict[int, list[tuple[float, float]]]] = defaultdict(lambda: {5: [], 20: [], 63: []})
    registry = load_registry()
    rerun = 0
    for row in needed:
        session = date.fromisoformat(row["session"])
        theme_id = row["theme_id"]
        members = set(SECTORS[theme_id]["tickers"]) | {"SPY", "QQQ"}
        session_panel = {}
        theme_panel = {}
        for sid in members:
            series = panel.get(sid)
            if series is None:
                continue
            theme_panel[sid] = series
            window = _tail_history(series, session)
            if window is not None:
                session_panel[sid] = window
        payload = compute_snapshot(
            eod_evaluation_as_of(session),
            session_panel,
            "u_closeout_historical",
            registry,
            sector_id=theme_id,
            algorithm=row["algorithm"],
            profile="balanced",
            horizon="mid",
            source_finalized_through=session,
        )
        rerun += 1
        for item in payload["rows"]:
            if item.get("status") != "eligible" or item.get("score") is None:
                continue
            series = theme_panel.get(item["security_id"])
            if series is None:
                continue
            for horizon in LABELS:
                if session > label_cut[horizon]:
                    continue
                fwd = _forward_return(series, session, horizon, ALLOWED_END)
                if fwd is None:
                    continue
                pairs[theme_id][horizon].append((float(item["score"]), float(fwd)))
        print(
            json.dumps({"rerun": rerun, "of": len(needed), "theme": theme_id, "session": row["session"]}),
            flush=True,
        )

    for theme_id, stats in report["themes"].items():
        stats["ic_forward_labels"] = {
            str(horizon): _spearman(
                [a for a, _ in pairs[theme_id][horizon]],
                [b for _, b in pairs[theme_id][horizon]],
            )
            for horizon in LABELS
        }
        stats["ic_pair_counts"] = {str(horizon): len(pairs[theme_id][horizon]) for horizon in LABELS}
        stats["ic_note"] = "Spearman of eligible score vs TRI forward label; signal diagnostic only"
    report["ic_eligible_snapshots_rerun"] = rerun
    report["label_maturity_cuts"] = {str(k): v.isoformat() for k, v in label_cut.items()}
    OUT.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"rerun": rerun, "out": str(OUT)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
