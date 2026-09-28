"""Positive control on real data: drop one SPY bar in a copy of the cache and rerun the gate.

Runs on the Colab machine used for results/review2 (frozen cache and directory under
/content/data, the gate branch's backend under /content/gate2, the CI Python environment
under /content/venv); see ../full_market_v1_6/results/review2/RUN_SPEC.md for the layout.
"""
import gzip, json, shutil, sqlite3, sys
from datetime import date
from pathlib import Path
sys.path.insert(0, "/content/gate2/backend")
from app.services.eod_limited import market_data as md
from app.services.eod_limited.benchmark_window import benchmark_window, window_problems
from app.services.eod_limited.live_config import LIVE_CONFIG
from app.services.eod_limited.universe import select_all_market_universe
from app.services.research_eod_v1.constants import HORIZONS
from app.services.research_eod_v1.membership import has_complete_session_bar

TARGET = date(2026, 9, 18)
DIRECTORY = Path("/content/data/massive_directory_2026-09-27")
label = sorted(p.name[:-8] for p in DIRECTORY.glob("20*.json.gz") if p.name[:10] <= TARGET.isoformat())[-1]
rows = json.loads(gzip.open(DIRECTORY / f"{label}.json.gz").read())["results"]
members, coverage = select_all_market_universe(rows, tickers=["SPY"])
window = benchmark_window(TARGET, horizons=HORIZONS, policy=LIVE_CONFIG.tuning_policy())
print("window", window.describe())
shutil.copyfile("/content/data/replay.sqlite", "/content/mut.sqlite")
out = {}
for case, dropped in (("inside_window", "2026-03-02"), ("trailing_skipped_session", "2026-09-15"),
                      ("outside_window_inside_lookback", "2025-05-01"), ("none", None)):
    shutil.copyfile("/content/data/replay.sqlite", "/content/mut.sqlite")
    conn = sqlite3.connect("/content/mut.sqlite"); conn.row_factory = sqlite3.Row
    if dropped:
        n = conn.execute("DELETE FROM raw_daily_bars WHERE ticker='SPY' AND session_date=?", (dropped,)).rowcount
        conn.commit()
    else:
        n = 0
    sessions = md._required_sessions(TARGET)
    md._session_manifest(conn, sessions)
    panel = md._load_panel(conn, members, coverage, sessions)[0]
    spy = panel.get("SPY")
    if spy is None:
        verdict = {"status": "SPY dropped from panel by the loader"}
    elif not has_complete_session_bar(spy, TARGET):
        verdict = {"status": "refused_target_day"}
    else:
        problems = window_problems("SPY", spy, window)
        verdict = {"status": "refused_window" if problems else "ok", "problems": problems}
    out[case] = {"dropped": dropped, "rows_deleted": n, **verdict}
    print(case, json.dumps(out[case])[:400], flush=True)
    conn.close()
Path("/content/gate_mutation_realdata.json").write_text(json.dumps({"target": TARGET.isoformat(), "window": window.describe(), "cases": out}, indent=2) + "\n")
