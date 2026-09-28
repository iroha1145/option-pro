"""Run the new SPY window gate on the frozen Massive data for every trading session.

Uses production code from the gate branch (claude/eod-v1.7-spy-window): the same
directory selection, cache loader and window check the all-market worker runs.
Only SPY is loaded, since the check reads only SPY.

Runs on the Colab machine used for results/review2 (frozen cache and directory under
/content/data, the gate branch's backend under /content/gate2, the CI Python environment
under /content/venv); see ../full_market_v1_6/results/review2/RUN_SPEC.md for the layout.
"""
import gzip
import json
import sqlite3
import sys
import time
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, "/content/gate2/backend")

from app.services.eod_limited import market_data as md  # noqa: E402
from app.services.eod_limited.benchmark_window import benchmark_window, window_problems  # noqa: E402
from app.services.eod_limited.live_config import LIVE_CONFIG  # noqa: E402
from app.services.eod_limited.universe import select_all_market_universe  # noqa: E402
from app.services.market_calendar import is_trading_day  # noqa: E402
from app.services.research_eod_v1.constants import HORIZONS  # noqa: E402
from app.services.research_eod_v1.membership import has_complete_session_bar  # noqa: E402

FREEZE_START = date(2021, 9, 28)
DB = "/content/data/replay.sqlite"
DIRECTORY = Path("/content/data/massive_directory_2026-09-27")
FIRST, LAST = date(2023, 3, 17), date(2026, 9, 25)

connection = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
connection.row_factory = sqlite3.Row
labels = sorted(path.name[:-8] for path in DIRECTORY.glob("20*.json.gz"))
directories: dict[str, list] = {}
policy = LIVE_CONFIG.tuning_policy()

started = time.time()
results = []
day = FIRST
while day <= LAST:
    if is_trading_day(day):
        label = [item for item in labels if item <= day.isoformat()][-1]
        if label not in directories:
            directories[label] = json.loads(gzip.open(DIRECTORY / f"{label}.json.gz").read())["results"]
        members, coverage = select_all_market_universe(directories[label], tickers=["SPY"])
        sessions = md._required_sessions(day)
        row = {"session": day.isoformat(), "directory": label}
        if sessions[0] < FREEZE_START:
            row["status"] = "window_before_freeze"
        else:
            md._session_manifest(connection, sessions)
            panel = md._load_panel(connection, members, coverage, sessions)[0]
            spy = panel.get("SPY")
            if spy is None or not has_complete_session_bar(spy, day):
                row["status"] = "refused_target_day"
            else:
                window = benchmark_window(day, horizons=HORIZONS, policy=policy)
                problems = window_problems("SPY", spy, window)
                row["status"] = "refused_window" if problems else "ok"
                row["window"] = window.describe()
                if problems:
                    row["problems"] = problems
        results.append(row)
    day += timedelta(days=1)

counts = Counter(row["status"] for row in results)
summary = {
    "code": "claude/eod-v1.7-spy-window 42e0f827",
    "data": "frozen Massive grouped daily cache (replay.sqlite), point-in-time directory",
    "sessions_checked": len(results),
    "first": results[0]["session"], "last": results[-1]["session"],
    "status_counts": dict(counts),
    "window_sizes": dict(Counter(row["window"]["session_count"] for row in results if "window" in row)),
    "refusals": [row for row in results if row["status"].startswith("refused")],
    "elapsed_s": round(time.time() - started, 1),
}
Path("/content/gate_realdata.json").write_text(json.dumps(summary, indent=2) + "\n")
print(json.dumps({key: value for key, value in summary.items() if key != "refusals"}, indent=2))
for row in summary["refusals"][:20]:
    print(json.dumps(row)[:600])
