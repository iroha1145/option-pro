"""Write the Sharadar data-gate pack from the actual execution state.

Exit codes: 0 = DATA_GATE_ACCEPTED, 2 = credential / entitlement / network
blocked, 3 = partial or insufficient (review required), 1 = crashed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from app.services.research_eod_v1 import FEATURE_VERSION  # noqa: E402
from app.services.research_eod_v1.data.sharadar_acceptance import (  # noqa: E402
    THEME_PARENT_SCHEMA,
    reconciliation_csv,
)
from app.services.research_eod_v1.data.sharadar_archive import CONTROL_LABEL, build_control_index  # noqa: E402
from app.services.research_eod_v1.data.sharadar_identity import etf_subasset_schema  # noqa: E402
from app.services.research_eod_v1.data.sharadar import SharadarClient  # noqa: E402
from app.services.research_eod_v1.data.sharadar_pipeline import execute_data_gate  # noqa: E402
from app.services.research_eod_v1.data.sharadar_reconcile_source import RECONCILE_CACHE_ENV  # noqa: E402
from app.services.research_eod_v1.data.sharadar_schema import (  # noqa: E402
    ACTIONS_FIELDS,
    CHANNEL_DOCS,
    DOWNLOAD_MODES,
    ENV_KEY_NAME,
    EVIDENCE_ROW_CAP,
    FORMULA_VERSION,
    FUNDS_FIELDS,
    OFFICIAL_BASE_URL,
    OFFICIAL_CHANNEL,
    STOCKS_FIELDS,
    TABLES,
    TICKERS_FIELDS,
)
from app.services.research_eod_v1.paths import RETURN_PACK_DIR  # noqa: E402

PACK = RETURN_PACK_DIR
V3 = PACK / "sharadar_v3"
RAW_STORE = ROOT / "research" / "option_pro_us_eod_v1" / "data" / "sharadar_raw"

BLOCKED_TERMINALS = {"AUTH_REQUIRED", "AUTH_FAILED", "ENTITLEMENT_MISSING", "NETWORK_UNAVAILABLE"}


def _git_sha() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
        return
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=RAW_STORE, help="private raw store (outside git)")
    parser.add_argument("--mode", choices=list(DOWNLOAD_MODES), default="bulk_first")
    parser.add_argument(
        "--reconcile-cache",
        action="append",
        type=Path,
        default=None,
        help=f"captured comparison cache (parquet or trusted pkl); repeatable; also {RECONCILE_CACHE_ENV}",
    )
    parser.add_argument("--no-completeness-check", action="store_true", help="skip the sampled per-day recount after a paged download")
    parser.add_argument("--evidence-rows", type=int, default=5_000, help="max reconciliation rows written into the public pack")
    parser.add_argument(
        "--pack-dir",
        type=Path,
        default=PACK,
        help="where the head/audit/stop/control-index files go; point it outside the repo for a local plumbing run",
    )
    parser.add_argument("--out", type=Path, default=None, help="directory for the sharadar_v3 files (default: <pack-dir>/sharadar_v3)")
    parser.add_argument(
        "--base-url",
        default=None,
        help="override the API base URL for a local fake server; the key is still only attached to the official https origin",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    store: Path = args.store
    pack: Path = args.pack_dir
    out: Path = args.out if args.out is not None else pack / "sharadar_v3"
    store.mkdir(parents=True, exist_ok=True)
    reconcile_paths = list(args.reconcile_cache) if args.reconcile_cache else None
    client = SharadarClient(allow_network=True, base_url=args.base_url) if args.base_url else None
    gate = execute_data_gate(
        client=client,
        store=store,
        allow_network=True,
        mode=args.mode,
        reconcile_cache_paths=reconcile_paths,
        verify_completeness=not args.no_completeness_check,
    )
    archive = build_control_index()
    probe = gate["probe"]
    reconcile = gate["reconcile"]
    schema = {
        "channel": OFFICIAL_CHANNEL,
        "base_url": OFFICIAL_BASE_URL,
        "docs": list(CHANNEL_DOCS),
        "tables": TABLES,
        "fields": {
            "stocks": list(STOCKS_FIELDS),
            "funds": list(FUNDS_FIELDS),
            "tickers": list(TICKERS_FIELDS),
            "actions": list(ACTIONS_FIELDS),
        },
        "formula_version": FORMULA_VERSION,
        "credential_env": ENV_KEY_NAME,
        "nasdaq_data_link_client": False,
        "download_mode_requested": args.mode,
        "download_modes_used": gate.get("download_modes_used"),
    }
    identity_contract = {
        "security_id": "sharadar:{permaticker}",
        "do_not_join_history_by_current_ticker": True,
        "numeric_suffix_resolved_by_ticker_base": True,
        "firstpricedate_is_not_legal_list_date": True,
        "firstpricedate_not_exported_as_listed_at": True,
        "relatedtickers_not_auto_aliases": True,
        "exchange_is_latest_primary_venue": True,
        "flags": [
            "VENUE_HISTORY_UNVERIFIED",
            "PRICE_HISTORY_RECONSTRUCTED",
            "CLASSIFICATION_CURRENT",
            "ETF_SUBASSET_MAPPING_MANUAL",
        ],
        "strategy_pool": {
            "raw_universe_unsifted": True,
            "category": "Domestic Common Stock or ADR Common Stock including Primary/Secondary Class",
            "currency": "USD",
            "closeunadj_min": 5.0,
            "unadj_adv20_min": 20_000_000,
            "unadj_adv20_excludes_T": True,
            "exchange_is_tag_not_drop": True,
            "computed_in_pipeline": bool((gate.get("daily_pool") or {}).get("computed")),
        },
        "etf_subasset": etf_subasset_schema(),
        "theme_parent_schema": THEME_PARENT_SCHEMA,
    }
    gap_list = [
        {
            "item": "SHARADAR_API_KEY",
            "status": "PASS" if gate["credential_present"] else "AUTH_REQUIRED",
            "action": "inject Cursor secret and start a new agent" if not gate["credential_present"] else "credential_present_only_boolean",
        },
        {
            "item": "live_table_read",
            "status": gate["checks"]["live_sharadar_read"],
            "action": "do not fall back to Yahoo or Massive",
        },
        {
            "item": "entitlement_years",
            "status": gate["entitlement"]["status"],
            "action": "stop on 5Y; tag 10Y; continue full from 2010-01-01; tier comes from bulk status metadata, not from the earliest observed row",
            "probe": gate.get("entitlement_probe"),
        },
        {
            "item": "download_mode",
            "status": "PASS" if gate.get("download_modes_used") else "NOT_RUN",
            "action": "bulk zip first for the backfill; paged fetch with explicit sort only as fallback / incremental",
            "modes": gate.get("download_modes_used"),
        },
        {
            "item": "yahoo_overlap_reconcile",
            "status": reconcile["status"],
            "action": "align security/currency/split scale before comparing; fewer than the minimum comparisons is INSUFFICIENT",
            "thresholds": reconcile.get("thresholds"),
        },
        {
            "item": "volume_session_scope",
            "status": gate["volume"]["status"],
            "action": "do not start volume experiments",
        },
        {
            "item": "persistent_offline_export",
            "status": gate["checks"]["persistent_handover"],
            "action": "authorized store outside public git; resume from checkpoints",
            "store": gate["store"],
        },
        {
            "item": "explicit_history_request_bounds",
            "status": gate["checks"]["explicit_history_request_bounds"],
            "action": "every paged date-table request seen must carry from=2010-01-01 to=2024-06-28",
        },
        {
            "item": "full_download_gate",
            "status": "PASS" if gate["full_download_allowed"].get("allowed") else "BLOCKED",
            "action": "an empty READ_OK page is not sample proof; do not start a full download on AUTH_FAILED or missing entitlement",
            "blockers": gate["full_download_allowed"].get("blockers"),
        },
        {
            "item": "action_vocabulary",
            "status": "OBSERVED" if (gate.get("action_vocabulary") or {}).get("total_rows") else "NOT_OBSERVED",
            "action": "compare the observed action strings with the classifier's assumed vocabulary before trusting terminal labels",
            "observed": (gate.get("action_vocabulary") or {}).get("observed"),
        },
        {
            "item": "members_and_factors",
            "status": "NOT_THIS_ROUND",
            "action": "wait for data-gate review",
        },
    ]
    terminal = gate["terminal_status"]
    head = {
        "head": _git_sha(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "feature_version": FEATURE_VERSION,
        "archived_feature_version": "us-eod-research-features-v1.5",
        "archive_label": CONTROL_LABEL,
        "terminal_status": terminal,
        "executed_backtests": 0,
        "yahoo_fallback_used": False,
        "massive_key_used": False,
        "weight_search": False,
        "credential_present": gate["credential_present"],
        "live_sharadar_request_count": gate["live_sharadar_request_count"],
        "download_modes_used": gate.get("download_modes_used"),
        "probe": "sharadar_v3/provider_probe.json",
        "connect_accept": "sharadar_v3/connect_accept_report.json",
        "stage_summary": "sharadar_v3/gate_stages.json",
        "raw_download_status": gate["raw_download_status"],
    }
    stage_line = json.dumps({name: item["status"] for name, item in gate["stages"].items()}, ensure_ascii=False)
    audit = "\n".join([
        "# Sharadar 接通与验收回传",
        "",
        f"当前 head 由 git 记录。feature version 为 `{FEATURE_VERSION}`，旧 B0 不按新定义重算。",
        f"旧 B0/R1/R1b/R2/Freeze/固定矩阵标为 `{CONTROL_LABEL}`，不回写历史 JSON。",
        "",
        f"credential_present={str(gate['credential_present']).lower()}。",
        f"live_sharadar_request_count={gate['live_sharadar_request_count']}。",
        f"channel_confirmed={probe.get('channel_confirmed')}。",
        "未读取聊天凭据，未向 Nasdaq Data Link 或 Massive 试送密钥，未回退 Yahoo。",
        "官方渠道为 `https://api.sharadar.com/v1.0/data/<table>`；跨域签名下载不再附 key。",
        f"下载模式：请求 `{args.mode}`，实际 {json.dumps(gate.get('download_modes_used') or {}, ensure_ascii=False)}。",
        "分页只在收到合法空页时才算完成；短页、空响应体、重定向、HTTP 错误都不算完成。中断后盘上已提交的行照常读取。",
        "",
        f"日期表分页请求区间固定为 from={gate['request_plan']['stocks']['extra'].get('from')} to={gate['request_plan']['stocks']['extra'].get('to')}，sort={gate['request_plan']['stocks']['extra'].get('sort')}；主表不臆造日期参数。",
        f"探针是否允许全量下载：{json.dumps(gate['full_download_allowed'], ensure_ascii=False)}。",
        f"订阅档位探针：{json.dumps(gate.get('entitlement_probe'), ensure_ascii=False)}。",
        "",
        f"原始下载状态 `{gate['raw_download_status']}`；下载完成不等于验收通过。",
        f"分层状态：{stage_line}。",
        f"预登记必需项：{json.dumps(gate['stage_summary']['required'], ensure_ascii=False)}；accepted={str(gate['stage_summary']['accepted']).lower()}。",
        "",
        f"16 个身份案例：解析 {gate['checks'].get('delist_resolved_n')} 个，具体终值 {gate['checks'].get('delist_concrete_terminal_n')} 个；无行时为 fixture_list_only / AUTH_REQUIRED。",
        f"Yahoo 对账状态 `{reconcile['status']}`（{reconcile.get('insufficient_reason') or 'n/a'}）；对照源 {json.dumps(reconcile['comparison_source'], ensure_ascii=False)}。",
        f"volume scope `{gate['volume']['status']}`。",
        f"历史预算状态 `{gate['history_budget']['status']}`；覆盖不等于授权，权限状态 `{gate['entitlement']['status']}`，authorized_range_unknown={str(gate['entitlement']['authorized_range_unknown']).lower()}。",
        f"转换跳过行 {gate['transform']['skipped_n']} 条（占比 {gate['transform'].get('skipped_share')}），按原因计数，样本最多 {EVIDENCE_ROW_CAP} 条。",
        f"策略池摘要：{json.dumps({k: v for k, v in (gate.get('daily_pool') or {}).items() if k in ('computed', 'session_n', 'pool_size_median', 'venue_unverified_share')}, ensure_ascii=False)}。",
        f"公司行动词表（观察到的前 20 个）：{json.dumps(dict(list(((gate.get('action_vocabulary') or {}).get('observed') or {}).items())[:20]), ensure_ascii=False)}。",
        "",
        f"终态：`{terminal}`。这不是策略赢家状态。全市场选优未启动。",
    ]) + "\n"
    _write(out / "provider_probe.json", probe)
    _write(out / "schema.json", schema)
    _write(out / "identity_contract.json", identity_contract)
    _write(out / "delist_identity_acceptance.json", {
        "status": "AUTH_REQUIRED" if all(item.get("verification") == "fixture_list_only" for item in gate["delist"]) else "REVIEW",
        "fixture_n": len(gate["delist"]),
        "resolved_n": gate["checks"].get("delist_resolved_n"),
        "concrete_terminal_n": gate["checks"].get("delist_concrete_terminal_n"),
        "cases": gate["delist"],
    })
    _write(out / "source_reconciliation.csv", reconciliation_csv(reconcile, max_rows=int(args.evidence_rows)))
    public_reconcile = {k: v for k, v in reconcile.items() if k != "rows"}
    public_reconcile["rows_sample"] = list(reconcile.get("rows") or [])[: int(args.evidence_rows)]
    public_reconcile["rows_total"] = len(reconcile.get("rows") or [])
    _write(out / "source_reconciliation.json", public_reconcile)
    _write(store / "source_reconciliation_full.jsonl", "\n".join(json.dumps(row, default=str) for row in (reconcile.get("rows") or [])) + "\n")
    _write(out / "volume_scope_audit.json", gate["volume"])
    _write(out / "history_budget.json", gate["history_budget"])
    _write(out / "daily_pool_summary.json", gate.get("daily_pool"))
    _write(out / "action_vocabulary.json", gate.get("action_vocabulary"))
    _write(out / "gap_list.json", gap_list)
    _write(out / "acceptance_checks.json", gate["checks"])
    _write(out / "event_invariants.json", gate["event_invariants"])
    _write(out / "gate_stages.json", {
        "stages": gate["stages"],
        "summary": gate["stage_summary"],
        "raw_download_status": gate["raw_download_status"],
        "terminal_status": terminal,
        "raw_download_complete_is_independent": True,
    })
    _write(out / "request_plan.json", {
        "plan": gate["request_plan"],
        "download_mode_requested": args.mode,
        "download_modes_used": gate.get("download_modes_used"),
        "full_download_allowed": gate["full_download_allowed"],
        "allowed_window": {"from": gate["request_plan"]["stocks"]["extra"].get("from"), "to": gate["request_plan"]["stocks"]["extra"].get("to")},
    })
    _write(out / "transform_skipped_rows.json", gate["transform"])
    _write(out / "connect_accept_report.json", {
        "credential_present": gate["credential_present"],
        "live_sharadar_request_count": gate["live_sharadar_request_count"],
        "channel_confirmed": probe.get("channel_confirmed"),
        "tables": gate["tables"],
        "raw_row_counts": gate["raw_row_counts"],
        "isolated_row_counts": gate["isolated_row_counts"],
        "store": gate["store"],
        "stages": {name: item["status"] for name, item in gate["stages"].items()},
        "stage_summary": gate["stage_summary"],
        "raw_download_status": gate["raw_download_status"],
        "entitlement": gate["entitlement"],
        "entitlement_probe": gate.get("entitlement_probe"),
        "transform": {k: v for k, v in gate["transform"].items() if k != "skipped_rows"},
        "daily_pool": gate.get("daily_pool"),
        "terminal_status": terminal,
        "checks": gate["checks"],
        "delist_verification": [item.get("verification") for item in gate["delist"]],
    })
    _write(pack / "control_current_list_214_index.json", archive)
    _write(pack / "control_current_list_214_index.md", "\n".join([
        f"# {CONTROL_LABEL}",
        "",
        "旧当前名单路线结果归档。不要把 213/214 差异藏进一个标签。",
        "汽车 D/V 只是已见小池对照，不是全市场基线。",
        "本文件是新索引，不改写被引用的历史 JSON。",
        "",
        f"list_n=214；quality_valid_n={archive.get('quality_valid_n')}；quality_insufficient_n={archive.get('quality_insufficient_n')}。",
    ]) + "\n")
    _write(pack / "algorithm_sharadar_v3_head.json", head)
    _write(pack / "algorithm_sharadar_v3_audit.md", audit)
    _write(pack / "algorithm_sharadar_connect_accept_head.json", head)
    _write(pack / "algorithm_sharadar_connect_accept_audit.md", audit)
    _write(pack / "algorithm_sharadar_v3_stop.json", {
        "terminal_status": terminal,
        "do_not_start_weight_search": True,
        "do_not_fallback_yahoo": True,
        "do_not_merge": True,
        "holdout_unsealed": False,
        "executed_backtests": 0,
        "members_and_factors_not_started": True,
    })
    print(json.dumps({
        "terminal_status": terminal,
        "feature_version": FEATURE_VERSION,
        "credential_present": gate["credential_present"],
        "live_sharadar_request_count": gate["live_sharadar_request_count"],
        "download_modes_used": gate.get("download_modes_used"),
        "stages": {name: item["status"] for name, item in gate["stages"].items()},
    }, indent=2))
    if terminal == "DATA_GATE_ACCEPTED":
        return 0
    if terminal in BLOCKED_TERMINALS:
        return 2
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
