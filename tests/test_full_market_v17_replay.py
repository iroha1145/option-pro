"""The v1.7 replay mechanism: variant composition, shared inputs per input set, production switches."""
from __future__ import annotations

import gzip
import importlib.util
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from app.services.eod_limited import market_data as md
from app.services.eod_limited.industry import SicTable
from app.services.research_eod_v1.fixtures import structured_close, trading_days_ending

from eod_v17_fixtures import SIC_CODES, directory_rows, sic_table, stock_ids

SCRIPTS = Path(__file__).resolve().parents[1] / "research/option_pro_us_eod_v1/return_pack/full_market_v1_7/scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"v17_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


replay = _load("replay")
evaluate = _load("evaluate")
paired = _load("paired")

STOCKS = 36
FUNDS = ("SPY", "QQQ", "XLE", "TLT", "IWM", "GLD", "ZZZF")
END = date(2026, 9, 25)
SESSIONS = trading_days_ending(END, 600)  # 506 for the 12-1 residual, 63 more for labels
SIGNAL = SESSIONS[-64]


def _closes(index: int, kind: str) -> np.ndarray:
    n = len(SESSIONS)
    if kind == "fund":
        return structured_close(n, 80.0 + 10 * index, 0.03 + 0.01 * index, 20 + index)
    return structured_close(n, 15 + index, 0.04 + 0.012 * (index % 8), 16 + (index * 5) % 13)


@pytest.fixture(scope="module")
def replay_setup(tmp_path_factory):
    root = tmp_path_factory.mktemp("v17replay")
    db = root / "replay.sqlite"
    connection = md._connect(db)
    with connection:
        for day in SESSIONS:
            connection.execute("INSERT INTO market_sessions VALUES (?, 'x', 1, 'x', 'OK')", (day.isoformat(),))
        rows = []
        tickers = [(name, "fund", index) for index, name in enumerate(FUNDS)] + \
                  [(sid, "stock", index) for index, sid in enumerate(stock_ids(STOCKS))]
        for ticker, kind, index in tickers:
            closes = _closes(index, kind)
            volume = 400_000 if kind == "fund" else 3_000_000 + 40_000 * index
            for day, close in zip(SESSIONS, closes):
                open_ = close * 0.998
                rows.append((ticker, day.isoformat(), 1, open_, max(open_, close) * 1.01, min(open_, close) * 0.99,
                             close, volume, None, None))
        connection.executemany("INSERT INTO raw_daily_bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    connection.close()
    directory_dir = root / "directory"
    directory_dir.mkdir()
    directory = [row for row in directory_rows(STOCKS) if row["ticker"] in FUNDS or row["ticker"].startswith("NEW")]
    directory.append({"ticker": "ZZZF", "name": "Loan fund", "market": "stocks", "locale": "us", "type": "ETF",
                      "primary_exchange": "ARCX", "active": True, "cik": None})
    with gzip.open(directory_dir / f"{SESSIONS[-70].isoformat()}.json.gz", "wt") as handle:
        json.dump({"results": directory}, handle)
    table_path = root / "ticker_sic.json.gz"
    with gzip.open(table_path, "wt") as handle:
        json.dump(sic_table(STOCKS), handle)  # the frozen research table is a bare list
    variants = ["v16", "g3", "g3x2", "g4", "full3", "full4", "cons17", "nofund", "d12m1", "g3+cons17+d12m1",
                "cons17+nofund", "live"]
    replay._state.clear()
    replay.init_worker(str(db), str(directory_dir), "pit", str(table_path), variants)
    out = root / "out"
    out.mkdir()
    result = replay.replay_date((SIGNAL.isoformat(), str(out)))
    record = json.load(gzip.open(out / f"{SIGNAL.isoformat()}.json.gz"))
    return {"db": db, "out": out, "result": result, "record": record, "variants": variants,
            "directory": directory_dir, "table": table_path, "root": root}


def test_compose_merges_switches_and_unites_profiles():
    assert replay.compose("v16") == {"profiles": replay.ALL_PROFILES}
    assert replay.compose("cons17+nofund") == {"conservative": 2.0, "fund_scope": "benchmarks",
                                               "profiles": replay.ALL_PROFILES}
    combo = replay.compose("g3+cons17+d12m1")
    assert combo["industry"] == "g_only" and combo["sic_level"] == 3 and combo["conservative"] == 2.0
    assert combo["residual"] == (251, 21) and combo["profiles"] == replay.ALL_PROFILES
    assert replay.compose("cons17")["profiles"] == ("conservative",)
    with pytest.raises(ValueError, match="set twice"):
        replay.compose("g3+g4")
    with pytest.raises(ValueError, match="unknown variant"):
        replay.compose("g5")
    assert replay.input_key(replay.compose("g3")) == replay.input_key(replay.compose("v16")) == (None, None, "all")
    assert replay.input_key(replay.compose("full3")) == ("full", 3, "all")
    assert replay.input_key(replay.compose("full4")) != replay.input_key(replay.compose("full3"))
    assert replay.input_key(replay.compose("nofund")) == (None, None, "benchmarks")


def test_live_variant_reads_the_production_config_and_cannot_be_composed():
    from app.services.eod_limited.live_config import LIVE_CONFIG

    spec = replay.compose("live")
    assert spec["live"] is True and spec["profiles"] == replay.ALL_PROFILES
    assert spec.get("conservative") == LIVE_CONFIG.conservative_atr_multiplier
    assert spec.get("fund_scope") == LIVE_CONFIG.fund_scope == "benchmarks"
    assert "industry" not in spec
    # S1 as adopted: the live inputs are the cons17+nofund inputs.
    assert replay.input_key(spec) == replay.input_key(replay.compose("cons17+nofund"))
    assert replay.registry_for(spec) == replay.registry_for(replay.compose("cons17+nofund"))
    options = replay.options_for("live", spec, {3: {}, 4: {}})
    assert options.tuning.version == "full-market-v1.7-cons-atr2" and options.industry_mode == "off"
    assert options.label == LIVE_CONFIG.label()
    for name in ("live+g3", "cons17+live"):
        with pytest.raises(ValueError, match="cannot be composed"):
            replay.compose(name)


def test_registry_and_options_follow_the_variant_spec():
    from app.services.eod_limited.market_registry import load_market_registry

    base = load_market_registry()
    g3x2 = replay.registry_for(replay.compose("g3x2"))
    assert g3x2["profiles"]["balanced"]["factor_tilt"][7] == pytest.approx(base["profiles"]["balanced"]["factor_tilt"][7] * 2)
    assert g3x2["profiles"]["conservative"] == base["profiles"]["conservative"]
    cons = replay.registry_for(replay.compose("cons17"))
    assert cons["profiles"]["conservative"]["factor_tilt"][6] == pytest.approx(1.0)
    assert cons["profiles"]["balanced"] == base["profiles"]["balanced"]
    assert replay.registry_for(replay.compose("v16")) == base
    tags = {3: SicTable(sic_table(STOCKS)).classify(directory_rows(STOCKS), level=3),
            4: SicTable(sic_table(STOCKS)).classify(directory_rows(STOCKS), level=4)}
    assert replay.options_for("v16", replay.compose("v16"), tags) is None
    assert replay.options_for("nofund", replay.compose("nofund"), tags) is None
    g3 = replay.options_for("g3", replay.compose("g3"), tags)
    assert g3.industry_mode == "g_only" and g3.label == "g3" and g3.tuning.version == "full-market-v1.5"
    full4 = replay.options_for("full4", replay.compose("full4"), tags)
    assert full4.industry_mode == "full" and set(full4.industry) == set(tags[4])
    combo = replay.options_for("g3+cons17+d12m1", replay.compose("g3+cons17+d12m1"), tags)
    assert combo.industry_mode == "g_only" and combo.tuning.version == "full-market-v1.7-cons-atr2"


def test_replay_date_scores_every_variant_on_shared_inputs(replay_setup):
    record, result = replay_setup["record"], replay_setup["result"]
    assert result["status"] == record["status"] == "scored" and record["gate_ok"]
    assert record["eligible"] == record["complete"] == STOCKS + len(FUNDS)
    assert record["industry"]["sic3"]["groups"] == 4 and record["industry"]["sic4"]["groups"] == 5
    expected_views = {name: len(replay.compose(name)["profiles"]) * 3 for name in replay_setup["variants"]}
    listed = {}
    for key in record["lists"]:
        listed[key.split("/")[0]] = listed.get(key.split("/")[0], 0) + 1
    assert listed == expected_views
    assert record["unavailable_variants"] == {}
    assert set(record["residual_status"]) == {"d12m1", "g3+cons17+d12m1"}
    assert record["variant_options"]["v16"] is None and record["variant_options"]["nofund"] is None
    assert record["variant_options"]["g3"]["industry_mode"] == "g_only"
    assert record["variant_options"]["full3"]["industry_mode"] == "full"
    assert record["variant_options"]["cons17"]["tuning_version"] == "full-market-v1.7-cons-atr2"
    timing = record["timing_s"]
    assert {key for key in timing if key.startswith("precomputed")} == {
        "precomputed:base::all", "precomputed:full:3:all", "precomputed:full:4:all", "precomputed:base::benchmarks"}
    assert all(f"scored_{name}" in timing for name in replay_setup["variants"])
    from app.services.eod_limited.live_config import LIVE_CONFIG

    live, explicit = record["variant_options"]["live"], record["variant_options"]["cons17+nofund"]
    assert live["label"] == LIVE_CONFIG.label() and explicit["label"] == "cons17+nofund"
    assert {k: v for k, v in live.items() if k != "label"} == {k: v for k, v in explicit.items() if k != "label"}


def _rows(record, key):
    return [(row["ticker"], round(row["sort_score"], 9), row["stock_or_etf_track"]) for row in record["lists"][key]["rows"]]


def test_live_lists_equal_the_evaluated_s1_candidate_on_every_view(replay_setup):
    record = replay_setup["record"]
    for profile in replay.ALL_PROFILES:
        for view in ("short", "mid", "long"):
            assert _rows(record, f"live/{profile}/{view}") == _rows(record, f"cons17+nofund/{profile}/{view}")
            assert record["lists"][f"live/{profile}/{view}"]["n"] == record["lists"][f"cons17+nofund/{profile}/{view}"]["n"]
    # S1 keeps the balanced and aggressive stock rows of v1.6 and changes the conservative ones.
    for profile in ("balanced", "aggressive"):
        assert _stock_rows(record, f"live/{profile}/mid") == _stock_rows(record, f"v16/{profile}/mid")
    assert _stock_rows(record, "live/conservative/mid") == _stock_rows(record, "cons17/conservative/mid")
    assert _stock_rows(record, "live/conservative/mid") != _stock_rows(record, "v16/conservative/mid")


def _stock_rows(record, key):
    return [(row["ticker"], round(row["sort_score"], 9)) for row in record["lists"][key]["rows"]
            if row["stock_or_etf_track"] == "stock"]


def test_fund_scope_leaves_stock_rows_identical_and_drops_the_extra_fund(replay_setup):
    record = replay_setup["record"]
    for profile in replay.ALL_PROFILES:
        for view in ("short", "mid", "long"):
            assert _stock_rows(record, f"nofund/{profile}/{view}") == _stock_rows(record, f"v16/{profile}/{view}")
    funds_all = {row["ticker"] for key, block in record["lists"].items() if key.startswith("v16/")
                 for row in block["rows"] if row["stock_or_etf_track"] == "etf"}
    funds_scoped = {row["ticker"] for key, block in record["lists"].items() if key.startswith("nofund/")
                    for row in block["rows"] if row["stock_or_etf_track"] == "etf"}
    assert "ZZZF" not in funds_scoped
    assert funds_scoped <= funds_all


def test_industry_variants_change_lists_and_record_classification(replay_setup):
    record = replay_setup["record"]
    unclassified = {sid for index, sid in enumerate(stock_ids(STOCKS)) if index % 6 == 5}
    for key, block in record["lists"].items():
        name = key.split("/")[0]
        for row in block["rows"]:
            if row["stock_or_etf_track"] != "stock":
                assert row["industry_id"] is None
                continue
            assert (row["industry_id"] is None) == (row["ticker"] in unclassified), (key, row["ticker"])
            if row["industry_id"] is not None:
                assert row["industry_id"].startswith("sic3:")
            g = (row.get("factors") or {}).get("G")
            profile = key.split("/")[1]
            if name.startswith(("g3", "g4", "full")) and profile != "conservative":
                assert g is not None, (key, row["ticker"])
                if row["industry_id"] is None:
                    assert g == 50.0, (key, row["ticker"])  # neutral fill for unclassified names
            else:
                assert g is None, (key, row["ticker"])
    assert _stock_rows(record, "g3/balanced/mid") != _stock_rows(record, "v16/balanced/mid")
    assert _stock_rows(record, "full3/balanced/mid") != _stock_rows(record, "v16/balanced/mid")
    assert _stock_rows(record, "g3/balanced/mid") != _stock_rows(record, "g4/balanced/mid")
    assert _stock_rows(record, "cons17/conservative/mid") != _stock_rows(record, "v16/conservative/mid")
    assert _stock_rows(record, "full3/conservative/mid") != _stock_rows(record, "v16/conservative/mid")
    # The residual swap changes the D-family scores of listed rows (the A-led top rows may not move).
    def d_scores(name):
        return {(key, row["ticker"]): row["observation_family_scores"].get("D_residual_momentum")
                for key, block in record["lists"].items() if key.startswith(f"{name}/")
                for row in block["rows"] if row.get("observation_family_scores")}
    baseline_d, swapped_d = d_scores("v16"), d_scores("d12m1")
    common = [key for key in swapped_d if (key[0].replace("d12m1/", "v16/"), key[1]) in baseline_d]
    assert common and any(swapped_d[key] != baseline_d[(key[0].replace("d12m1/", "v16/"), key[1])] for key in common)
    assert set(record["residual_status"]["d12m1"]) <= {"OK", "SHORT_HISTORY", "UNALIGNED_BENCHMARK",
                                                        "INSUFFICIENT_MATCHED_BENCHMARK", "LONG_WINDOW_UNAVAILABLE",
                                                        "MISSING_DAY_RETURN"}
    assert record["residual_status"]["d12m1"].get("OK", 0) >= STOCKS


def test_evaluate_and_paired_produce_v17_tables(replay_setup, monkeypatch, tmp_path):
    out = tmp_path / "results"
    monkeypatch.setattr(sys, "argv", ["evaluate.py", "--db", str(replay_setup["db"]), "--replay",
                                      str(replay_setup["out"]), "--out", str(out)])
    evaluate.main()
    primary = list(__import__("csv").DictReader((out / "primary.csv").open()))
    assert {row["variant"] for row in primary} == set(replay_setup["variants"])
    assert {row["list_type"] for row in primary} == {"stock", "mixed"}
    conservative = [row for row in primary if row["profile"] == "conservative" and row["list_type"] == "stock"]
    assert {row["variant"] for row in conservative} >= {"v16", "cons17", "full3", "nofund"}
    assert all(row["unclassified_share"] != "" for row in primary if row["list_type"] == "stock" and row["variant"] == "v16")
    decision = json.loads((out / "decision.json").read_text())
    assert decision["baseline"] == "v16"
    assert "cons17/conservative/stock" in decision["variant_vs_baseline"]
    assert "g3/balanced/stock" in decision["variant_vs_baseline"] and "g3/conservative/stock" not in decision["variant_vs_baseline"]
    assert set(decision["pairs"]) >= {"g3|g4/balanced", "full3|full4/balanced", "full3|full4/conservative"}
    identity = json.loads((out / "identity.json").read_text())
    assert set(identity) == {"nofund"} and len(identity["nofund"]) == 9
    assert all(item["identical"] == item["dates"] == 1 for item in identity["nofund"].values())
    assert all({"compared_rows", "extra_variant_rows", "n_differing_dates"} <= set(item) for item in identity["nofund"].values())
    monkeypatch.setattr(sys, "argv", ["paired.py", "--db", str(replay_setup["db"]), "--replay",
                                      str(replay_setup["out"]), "--out", str(out)])
    paired.main()
    rows = list(__import__("csv").DictReader((out / "paired.csv").open()))
    # One date only: fewer than three points, so no paired row can be formed - the file still has its header.
    assert rows == []
    assert (out / "paired.csv").read_text().startswith("comparison,variant,profile")


def test_replay_directories_merge_by_date_and_shared_views_must_agree(replay_setup, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    record = json.loads(json.dumps(replay_setup["record"]))
    record["lists"] = {key: block for key, block in record["lists"].items() if key.startswith(("v16/", "g3/"))}
    with gzip.open(other / f"{SIGNAL.isoformat()}.json.gz", "wt") as handle:
        json.dump(record, handle)
    merged, skipped = evaluate.load_records([replay_setup["out"], other])
    assert len(merged) == 1 and skipped == {} and set(merged[0]["lists"]) == set(replay_setup["record"]["lists"])
    record["lists"]["v16/balanced/mid"]["rows"][0]["sort_score"] += 1.0
    with gzip.open(other / f"{SIGNAL.isoformat()}.json.gz", "wt") as handle:
        json.dump(record, handle)
    with pytest.raises(SystemExit, match="disagree"):
        evaluate.load_records([replay_setup["out"], other])


def test_compare_records_maps_variant_names_and_flags_differences(replay_setup, tmp_path, monkeypatch, capsys):
    compare = _load("compare_records")
    other = tmp_path / "renamed"
    other.mkdir()
    record = json.loads(json.dumps(replay_setup["record"]))
    record["lists"] = {key.replace("v16/", "v15/"): block for key, block in record["lists"].items() if key.startswith("v16/")}
    with gzip.open(other / f"{SIGNAL.isoformat()}.json.gz", "wt") as handle:
        json.dump(record, handle)
    monkeypatch.setattr(sys, "argv", ["compare_records.py", "--left", str(other), "--right", str(replay_setup["out"]),
                                      "--map", "v15=v16"])
    compare.main()
    assert "views compared 9, identical 9" in capsys.readouterr().out
    record["lists"]["v15/balanced/mid"]["rows"][0]["sort_score"] += 0.5
    with gzip.open(other / f"{SIGNAL.isoformat()}.json.gz", "wt") as handle:
        json.dump(record, handle)
    with pytest.raises(SystemExit):
        compare.main()
    assert "differs:" in capsys.readouterr().out


def test_replay_main_runs_a_spawned_pool_and_resumes(replay_setup):
    """The Colab entry point: argument parsing, run.json, spawn workers, cached dates."""
    import subprocess

    out = replay_setup["root"] / "pool_out"
    command = [sys.executable, str(SCRIPTS / "replay.py"), "--db", str(replay_setup["db"]),
               "--directory", str(replay_setup["directory"]), "--industry-table", str(replay_setup["table"]),
               "--out", str(out), "--dates", SIGNAL.isoformat(), "--variants", "v16,g3", "--workers", "2"]
    env = {**__import__("os").environ, "PYTHONDONTWRITEBYTECODE": "1"}
    first = subprocess.run(command, capture_output=True, text=True, timeout=600, check=True, env=env)
    assert "DONE replay" in first.stdout and '"status": "scored"' in first.stdout
    run = json.loads((out / "run.json").read_text())
    assert set(run["variants"]) == {"v16", "g3"} and run["variants"]["g3"]["industry"] == "g_only"
    record = json.load(gzip.open(out / f"{SIGNAL.isoformat()}.json.gz"))
    assert {key.split("/")[0] for key in record["lists"]} == {"v16", "g3"}
    assert _stock_rows(record, "v16/balanced/mid") == _stock_rows(replay_setup["record"], "v16/balanced/mid")
    second = subprocess.run(command, capture_output=True, text=True, timeout=600, check=True, env=env)
    assert '"status": "cached"' in second.stdout
    with pytest.raises(subprocess.CalledProcessError):
        subprocess.run(command[:-4] + ["--variants", "g3", "--workers", "1"], capture_output=True, check=True,
                       env=env)


def test_stock_identity_compares_the_common_prefix_and_reports_fund_counts_apart():
    def row(ticker, score, track="stock"):
        return {"ticker": ticker, "sort_score": score, "stock_or_etf_track": track}

    base = [row("AAA", 90.0), row("FUND", 85.0, "etf"), row("BBB", 80.0), row("CCC", 70.0)]
    # The variant scores no funds, keeps one more stock row and reports a smaller n.
    more = [row("AAA", 90.0), row("BBB", 80.0), row("CCC", 70.0), row("DDD", 60.0)]
    records = [{"session": "2026-09-14", "lists": {"v16/balanced/mid": {"n": 40, "rows": base},
                                                    "nofund/balanced/mid": {"n": 31, "rows": more}}},
               {"session": "2026-09-21", "lists": {"v16/balanced/mid": {"n": 40, "rows": base},
                                                    "nofund/balanced/mid": {"n": 40, "rows": [row("AAA", 90.0), row("BBB", 79.0)]}}},
               {"session": "2026-09-28", "lists": {"v16/balanced/mid": {"n": 40, "rows": base},
                                                    "nofund/balanced/mid": {"n": 40, "rows": [row("AAA", 90.0), row("BBB", 80.0)]}}}]
    out = evaluate.stock_identity(records, "v16", "nofund")
    item = out["balanced/mid"]
    assert item["dates"] == 3 and item["identical"] == 1
    assert item["differing_dates"] == ["2026-09-21", "2026-09-28"]  # a changed score; fewer stock rows
    assert item["extra_variant_rows"] == 1 and item["compared_rows"] == 3 + 2 + 2
    assert item["n_differing_dates"] == 1
    assert set(item) == {"dates", "identical", "differing_dates", "compared_rows", "extra_variant_rows", "n_differing_dates"}

