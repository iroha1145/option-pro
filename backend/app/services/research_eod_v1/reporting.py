"""Build the first-round return pack. Market PnL stays null without a licensed run."""

from __future__ import annotations

import csv
import json
import subprocess
from pathlib import Path
from typing import Any

from app.services.research_eod_v1.config_load import (
    config_hash,
    file_sha256,
    load_composite_manifest,
    load_etf_subasset_manifest,
    load_experiment_manifest,
    load_registry,
)
from app.services.research_eod_v1.paths import (
    COMPOSITE_MANIFEST_PATH,
    ETF_SUBASSET_MANIFEST_PATH,
    EXPERIMENT_MANIFEST_PATH,
    REFERENCE_REGISTRY_PATH,
    REGISTRY_PATH,
    REPORTS_DIR,
    RESEARCH_ROOT,
    RETURN_PACK_DIR,
    RETURN_SUMMARY_TEMPLATE_PATH,
)
from app.services.research_eod_v1.universe_audit import current_universe_audit

NULL_METRICS = {
    "net_cagr": None,
    "max_drawdown": None,
    "sharpe": None,
    "sortino": None,
    "calmar": None,
    "es95_loss": None,
    "net_excess_return_mean": None,
    "net_excess_return_block_ci": None,
    "win_rate": None,
    "payoff_ratio": None,
    "median_return": None,
    "worst_decile": None,
    "rank_ic": None,
    "benchmark_beat_rate": None,
    "mean_exposure": None,
    "peak_exposure": None,
    "turnover": None,
    "empty_selection_day_fraction": None,
    "cost_2x_return": None,
    "cost_4x_return": None,
    "independent_events": None,
    "raw_signals": 0,
    "deduped_events": 0,
    "data_missing_rejections": None,
    "pit_qualify_rate": None,
    "delisted_coverage": None,
    "history_coverage": None,
}


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=RESEARCH_ROOT.parents[1], text=True).strip()
    except Exception:
        return "unknown"


def experiment_row(spec: dict[str, Any], *, status: str, reason: str) -> dict[str, Any]:
    row = {
        "experiment_id": spec["experiment_id"],
        "sector_id": spec.get("sector_id") or spec.get("layer"),
        "subasset_id": spec.get("subasset_id"),
        "algorithm": spec.get("algorithm") or spec.get("method"),
        "profile": spec.get("profile"),
        "horizon": spec.get("horizon"),
        "holding_sessions": spec.get("holding_sessions") or spec.get("primary_holding_sessions"),
        "stock_or_etf_track": spec.get("stock_or_etf_track"),
        "status": status,
        "failure_reason": reason,
        "market_backtest_run": False,
        "holdout_contaminated": None,
        "provisional_finalist": False,
        "winner": False,
    }
    row.update(NULL_METRICS)
    return row


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_parquet(path: Path, rows: list[dict[str, Any]], schema: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    import pandas as pd

    frame = pd.DataFrame(rows, columns=list(schema))
    try:
        frame.to_parquet(path, index=False)
    except (ImportError, ValueError, OSError):
        fallback = path.with_suffix(".csv")
        if rows:
            write_csv(fallback, rows)
        else:
            fallback.write_text(",".join(schema) + "\n", encoding="utf-8")
    (path.parent / f"{path.stem}.schema.json").write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")


def build_return_pack(*, test_log: str = "") -> dict[str, Any]:
    registry = load_registry()
    primary = load_experiment_manifest()
    etf = load_etf_subasset_manifest()
    composite = load_composite_manifest()
    source_sha = _git_sha()
    digest = config_hash()
    reason = (
        "No licensed point-in-time US daily tape, delist file, corporate-action "
        "journal, or historical venue/industry vintages were verified in this environment."
    )
    RETURN_PACK_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    primary_rows = [experiment_row(row, status="DATA_INSUFFICIENT", reason=reason) for row in primary["experiments"]]
    etf_rows = [experiment_row(row, status="DATA_INSUFFICIENT", reason=reason) for row in etf["experiments"]]
    for row in etf_rows:
        if row["sector_id"] == "etfs":
            row["status"] = "DATA_INSUFFICIENT"
    composite_rows = [experiment_row(row, status="DATA_INSUFFICIENT", reason=reason) for row in composite["experiments"]]

    write_csv(RETURN_PACK_DIR / "all_experiments.csv", primary_rows)
    write_csv(RETURN_PACK_DIR / "etf_subasset_experiments.csv", etf_rows)
    write_csv(RETURN_PACK_DIR / "composite_experiments.csv", composite_rows)

    annual = []
    regime = []
    subgroup = []
    capital = []
    for row in primary_rows:
        for year in range(2010, 2027):
            annual.append({**row, "year": year, "status": "DATA_INSUFFICIENT"})
        for name in ("bull", "bear", "range"):
            regime.append({**row, "regime": name, "status": "DATA_INSUFFICIENT"})
        for name in registry["sectors"][row["sector_id"]]["pit_subgroups"]:
            subgroup.append({**row, "subgroup": name, "status": "DATA_INSUFFICIENT"})
        for dollars in registry["research"]["capital_scenarios_usd"]:
            for multiple in (1, 2, 4):
                capital.append({**row, "capital_usd": dollars, "cost_multiple": multiple, "status": "DATA_INSUFFICIENT"})
    write_csv(RETURN_PACK_DIR / "annual_oos.csv", annual)
    write_csv(RETURN_PACK_DIR / "regime_oos.csv", regime)
    write_csv(RETURN_PACK_DIR / "subgroup_oos.csv", subgroup)
    write_csv(RETURN_PACK_DIR / "capital_cost_scenarios.csv", capital)

    signal_schema = {
        "security_id": "string",
        "session_date": "date",
        "algorithm_id": "string",
        "status": "string",
        "note": "empty until a licensed market run exists",
    }
    write_parquet(RETURN_PACK_DIR / "signals.parquet", [], signal_schema)
    write_parquet(RETURN_PACK_DIR / "trades.parquet", [], {**signal_schema, "entry_session": "date", "exit_session": "date"})
    write_parquet(RETURN_PACK_DIR / "daily_equity.parquet", [], {"session": "date", "equity": "float", "cash": "float"})
    write_parquet(RETURN_PACK_DIR / "rejections.parquet", [], {"security_id": "string", "reason": "string", "session_date": "date"})
    for leftover in ("signals.csv", "trades.csv", "daily_equity.csv", "rejections.csv"):
        path = RETURN_PACK_DIR / leftover
        if path.is_file() and path.with_suffix(".parquet").is_file():
            path.unlink()
    # Synthetic engineering fixture — not a market trial.
    from datetime import date as _date
    from app.services.research_eod_v1.backtest import plan_trade
    from app.services.research_eod_v1.fixtures import make_series, trading_days, trending_close

    days = trading_days(_date(2021, 1, 4), 40)
    series = make_series("ENG", days, trending_close(40, 50, 0.2))
    planned = plan_trade(series, days[5], 5, adv20=80_000_000)
    write_parquet(
        RETURN_PACK_DIR / "engineering_fixture_trades.parquet",
        [
            {
                "security_id": planned.security_id,
                "session_date": planned.signal_session.isoformat(),
                "algorithm_id": "ENGINEERING_ONLY",
                "status": planned.label_status,
                "note": "synthetic fixture; not a market backtest",
                "entry_session": planned.entry_session.isoformat(),
                "exit_session": planned.exit_session.isoformat(),
                "net_return": planned.net_return,
            }
        ],
        {**signal_schema, "entry_session": "date", "exit_session": "date", "net_return": "float"},
    )
    (RETURN_PACK_DIR / "parquet_index.json").write_text(
        json.dumps(
            {
                "signals": "signals.parquet",
                "trades": "trades.parquet",
                "daily_equity": "daily_equity.parquet",
                "rejections": "rejections.parquet",
                "engineering_fixture_trades": "engineering_fixture_trades.parquet",
                "rows": 0,
                "note": "Market bodies are empty because no licensed trial was executed. engineering_fixture_trades.parquet is synthetic only.",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    sector_dir = RETURN_PACK_DIR / "sector_reports"
    sector_dir.mkdir(exist_ok=True)
    sector_results = []
    for sector_id, sector in registry["sectors"].items():
        pit = "INSUFFICIENT_PIT_HISTORY" if sector.get("historical_membership_required") else "DATA_INSUFFICIENT"
        lines = [
            f"# {sector['name']} (`{sector_id}`)",
            "",
            "状态：`DATA_INSUFFICIENT` / `INSUFFICIENT_PIT_HISTORY`。",
            "",
            "本轮没有可核验的十年 PIT 日线、退市样本或历史行业标签，因此 **没有收益数字**，也 **没有赢家**。",
            "",
            f"- 资产轨道：`{sector['asset_track']}`",
            f"- 基准提示：`{sector['benchmark_hint']}`",
            f"- 事件规则：`{sector['gates']['event_policy']}`（无 PIT 事件日历时该变体不可测）",
            f"- 子组（仅诊断，未调参）：{', '.join(sector['pit_subgroups'])}",
            "",
            "## 36 个主评估",
            "",
            "|算法|风险档|期限|状态|暂留/淘汰|",
            "|---|---|---|---|---|",
        ]
        for algo in sector["candidates"]:
            for profile in registry["profiles"]:
                for horizon in registry["horizons"]:
                    lines.append(
                        f"|{algo}|{profile}|{horizon}|DATA_INSUFFICIENT|无赢家（允许空结果）|"
                    )
        if sector_id == "etfs":
            lines.extend([
                "",
                "主表 36 行状态为 `SEE_SUBASSETS` 语义：金/债不得与股票超额收益混排。180 个子资产切片见 `etf_subasset_experiments.csv`。",
            ])
        (sector_dir / f"{sector_id}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        sector_results.append({
            "sector_id": sector_id,
            "expected_main_rows": 36,
            "status": pit,
            "pit_years": None,
            "independent_event_count": None,
            "provisional_finalists": [],
            "no_winner_allowed": True,
            "data_limitations": [reason],
            "report_file": f"sector_reports/{sector_id}.md",
        })

    universe = current_universe_audit()
    data_audit = {
        "providers": [],
        "license_verified": False,
        "delisted_covered": False,
        "pit_classification_covered": False,
        "historical_venue_covered": False,
        "adjustment_policy": "not_verified_split_vs_dividend",
        "volume_session_scope": "unknown_regular_vs_full_day",
        "historical_revisions_limitations": [
            "No vendor vintage/revision journal was available.",
        ],
        "corporate_action_gaps": [
            "No point-in-time split/dividend/merger tape was licensed in this run.",
        ],
        "current_universe_audit": universe,
        "massive_mcp": "unavailable_or_unauthenticated",
        "yahoo_ sufficed": False,
        "blocking_items": [
            "Licensed US daily OHLCV with delistings from 2010 (or ten complete calendar years) plus 330-session warmup.",
            "Point-in-time exchange/MIC/security-type membership.",
            "Point-in-time economic industry and theme membership with valid-from/valid-to.",
            "Split-adjusted OHLC, total-return, and raw unadjusted prices as three separate tracks.",
            "Regular-session vs full-day volume vintage.",
            "Point-in-time earnings/binary/financing/ADR/fund calendars.",
            "Historical market-cap buckets as of each session.",
        ],
    }
    (RETURN_PACK_DIR / "data_audit.json").write_text(json.dumps(data_audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    hashes = {
        "source_sha": source_sha,
        "config_sha256": digest,
        "data_snapshot_sha256": None,
        "registry_sha256": file_sha256(REGISTRY_PATH),
        "experiment_manifest_sha256": file_sha256(EXPERIMENT_MANIFEST_PATH),
        "etf_subasset_manifest_sha256": file_sha256(ETF_SUBASSET_MANIFEST_PATH),
        "composite_manifest_sha256": file_sha256(COMPOSITE_MANIFEST_PATH),
        "reference_registry_sha256": file_sha256(REFERENCE_REGISTRY_PATH),
    }
    (RETURN_PACK_DIR / "hashes.json").write_text(json.dumps(hashes, indent=2) + "\n", encoding="utf-8")

    ledger = []
    for collection, label in (
        (primary["experiments"], "primary"),
        (etf["experiments"], "etf_subasset"),
        (composite["experiments"], "composite"),
    ):
        for spec in collection:
            ledger.append({
                "trial_id": spec["experiment_id"],
                "layer": label,
                "registered_at": "2026-09-16",
                "status": "DATA_INSUFFICIENT",
                "attempts": 1,
                "market_backtest_run": False,
                "parameter_changes": [],
                "notes": reason,
            })
    event_reason = (
        "technical_plus_event_guard is an independent overlay. No point-in-time "
        "earnings/binary/financing/ADR/fund calendar was licensed, so the overlay "
        "cannot default to pass."
    )
    event_entries = []
    for spec in primary["experiments"]:
        event_entries.append({
            "trial_id": f"{spec['experiment_id']}::technical_plus_event_guard",
            "layer": "event_overlay",
            "registered_at": "2026-09-16",
            "status": "DATA_INSUFFICIENT",
            "attempts": 1,
            "market_backtest_run": False,
            "parameter_changes": [],
            "notes": event_reason,
        })
    ledger.extend(event_entries)
    (RETURN_PACK_DIR / "trial_ledger.json").write_text(
        json.dumps(
            {
                "count": len(ledger),
                "technical_only": len(ledger) - len(event_entries),
                "event_overlay": len(event_entries),
                "entries": ledger,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    summary = json.loads(RETURN_SUMMARY_TEMPLATE_PATH.read_text(encoding="utf-8"))
    summary.update({
        "status": "DATA_INSUFFICIENT",
        "source_sha": source_sha,
        "config_sha256": digest,
        "data_snapshot_sha256": None,
        "history_start": None,
        "history_end": None,
        "market_backtests_run": False,
        "holdout_contaminated": None,
        "universe_tracks": {
            "U_current": {
                "status": "DATA_INSUFFICIENT",
                "limitations": [
                    "Current theme list is a 2026 survivor watchlist.",
                    "primary_sector_id is first-listing-wins, not an economic industry.",
                    "No licensed replay of the current list was executed.",
                ],
            },
            "U_PIT": {
                "status": "INSUFFICIENT_PIT_HISTORY",
                "limitations": data_audit["blocking_items"],
            },
        },
        "data_audit": {k: v for k, v in data_audit.items() if k != "current_universe_audit"},
        "primary_results_file": "all_experiments.csv",
        "etf_subasset_results_file": "etf_subasset_experiments.csv",
        "composite_results_file": "composite_experiments.csv",
        "annual_oos_file": "annual_oos.csv",
        "regime_oos_file": "regime_oos.csv",
        "subgroup_oos_file": "subgroup_oos.csv",
        "capital_cost_file": "capital_cost_scenarios.csv",
        "signals_file": "signals.parquet",
        "trades_file": "trades.parquet",
        "daily_equity_file": "daily_equity.parquet",
        "rejections_file": "rejections.parquet",
        "trial_ledger_file": "trial_ledger.json",
        "engineering_test_log": "engineering_tests.log",
        "performance_report_file": "performance_network.json",
        "sector_results": sector_results,
        "required_metrics": NULL_METRICS,
        "promotion_to_production": False,
        "next_review_questions": [
            "Which licensed vendor covers delistings, regular-session volume, and PIT industry from 2010?",
            "Confirm 2024-2025 must be labeled holdout_contaminated=true if previously viewed.",
            "Do not promote any A/B/C/D or M1-M4 candidate from this pack.",
        ],
    })
    (RETURN_PACK_DIR / "return_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (REPORTS_DIR / "return_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    (RETURN_PACK_DIR / "engineering_tests.log").write_text(test_log or "not-run-in-builder\n", encoding="utf-8")
    audit_src = REPORTS_DIR / "audit.md"
    if audit_src.is_file():
        (RETURN_PACK_DIR / "audit.md").write_text(audit_src.read_text(encoding="utf-8"), encoding="utf-8")
    (RETURN_PACK_DIR / "performance_network.json").write_text(
        json.dumps(
            {
                "batch_downloads": 0,
                "intraday_provider_calls": 0,
                "repeat_refresh_fetches": 0,
                "cache_key": "session_date|feature_version|config_hash|universe_version",
                "atomic_publish": "tempfile + os.replace; failed publish retains previous snapshot",
                "note": "Shadow API is default-off. Refresh refuses to fetch without RESEARCH_EOD_V1_ENABLED and licensed data.",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _write_code_artifacts()
    (RETURN_PACK_DIR / "README.md").write_text(
        "\n".join(
            [
                "# 第一轮回传包",
                "",
                "状态：`DATA_INSUFFICIENT`。没有许可的十年 PIT 行情，因此 **没有市场收益数字，没有赢家，不晋升生产**。",
                "",
                "必含文件：",
                "",
                "- `audit.md`、`data_audit.json`、`hashes.json`、`trial_ledger.json`",
                "- `all_experiments.csv`（864 行 + 表头）",
                "- `etf_subasset_experiments.csv`（180）",
                "- `composite_experiments.csv`（12）",
                "- `annual_oos.csv` / `regime_oos.csv` / `subgroup_oos.csv` / `capital_cost_scenarios.csv`",
                "- `signals.parquet` / `trades.parquet` / `daily_equity.parquet` / `rejections.parquet` + `*.schema.json`",
                "- `engineering_fixture_trades.parquet`：合成工程夹具，不是市场回测",
                "- `sector_reports/*.md`（24）",
                "- `return_summary.json`",
                "- `engineering_tests.log`、`performance_network.json`、`code_diff_*`",
                "",
                "台账另含 864 条 `technical_plus_event_guard` overlay，全部 `DATA_INSUFFICIENT`。",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return {
        "primary": len(primary_rows),
        "etf": len(etf_rows),
        "composite": len(composite_rows),
        "ledger": len(ledger),
        "event_overlay": len(event_entries),
        "source_sha": source_sha,
        "config_sha256": digest,
    }


def _write_code_artifacts() -> None:
    repo = RESEARCH_ROOT.parents[1]
    baseline = "8b620d760f05020d2c1b51a3c3228a89fd9f3120"

    def _git(*args: str) -> str:
        try:
            return subprocess.check_output(["git", *args], cwd=repo, text=True)
        except Exception:
            return ""

    (RETURN_PACK_DIR / "code_diff_stat.txt").write_text(
        _git("diff", "--stat", baseline) or "unavailable\n",
        encoding="utf-8",
    )
    (RETURN_PACK_DIR / "code_diff_integration.patch").write_text(
        _git("diff", baseline, "--", "backend/app/config.py", "backend/app/main.py") or "unavailable\n",
        encoding="utf-8",
    )
    listed = _git(
        "ls-files",
        "backend/app/services/research_eod_v1",
        "backend/app/api/research_eod_v1.py",
        "research/option_pro_us_eod_v1",
        "tests/test_research_eod_v1_algorithms.py",
        "tests/test_research_eod_v1_backtest.py",
        "tests/test_research_eod_v1_checklist.py",
        "tests/test_research_eod_v1_compat.py",
        "tests/test_research_eod_v1_composite.py",
        "tests/test_research_eod_v1_eod_shadow.py",
        "tests/test_research_eod_v1_features.py",
        "tests/test_research_eod_v1_lookahead.py",
        "tests/test_research_eod_v1_registry.py",
        "tests/test_research_eod_v1_reporting.py",
        "tests/test_research_eod_v1_venue.py",
    )
    (RETURN_PACK_DIR / "code_file_index.txt").write_text(listed or "unavailable\n", encoding="utf-8")
