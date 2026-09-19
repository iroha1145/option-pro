"""Write the Sharadar data-gate pack from the actual execution state."""

from __future__ import annotations

import json
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
from app.services.research_eod_v1.data.sharadar_pipeline import execute_data_gate  # noqa: E402
from app.services.research_eod_v1.data.sharadar_schema import (  # noqa: E402
    ACTIONS_FIELDS,
    CHANNEL_DOCS,
    ENV_KEY_NAME,
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


def _git_sha() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
        return
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    RAW_STORE.mkdir(parents=True, exist_ok=True)
    gate = execute_data_gate(store=RAW_STORE, allow_network=True)
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
    }
    identity_contract = {
        "security_id": "sharadar:{permaticker}",
        "do_not_join_history_by_current_ticker": True,
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
            "action": "stop on 5Y; tag 10Y; continue full from 2010-01-01; filename is not entitlement",
        },
        {
            "item": "yahoo_overlap_reconcile",
            "status": reconcile["status"],
            "action": "align security/currency/split scale before comparing; missing fields are insufficient",
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
            "action": "date tables request from=2010-01-01 to=2024-06-28; a page cursor cannot drift to the vendor default year",
            "request_plan": gate["request_plan"],
        },
        {
            "item": "full_download_gate",
            "status": "PASS" if gate["full_download_allowed"].get("allowed") else "BLOCKED",
            "action": "an empty READ_OK page is not sample proof; do not start a full download on AUTH_FAILED or missing entitlement",
            "blockers": gate["full_download_allowed"].get("blockers"),
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
        "probe": "sharadar_v3/provider_probe.json",
        "connect_accept": "sharadar_v3/connect_accept_report.json",
        "stage_summary": "sharadar_v3/gate_stages.json",
        "raw_download_status": gate["raw_download_status"],
    }
    audit = "\n".join([
        "# Sharadar 接通与验收回传",
        "",
        f"当前 head 由 git 记录。feature version 仍为 `{FEATURE_VERSION}`，旧 B0 不按新定义重算。",
        f"旧 B0/R1/R1b/R2/Freeze/固定矩阵标为 `{CONTROL_LABEL}`，不回写历史 JSON。",
        "",
        f"credential_present={str(gate['credential_present']).lower()}。",
        f"live_sharadar_request_count={gate['live_sharadar_request_count']}。",
        "未读取聊天凭据，未向 Nasdaq Data Link 或 Massive 试送密钥，未回退 Yahoo。",
        "官方渠道为 `https://api.sharadar.com/v1.0/data/<table>`；跨域签名下载不再附 key。",
        "分页先持久提交页再推进游标；max_pages/中断为 PARTIAL。HTTP 200 error/HTML 与 503 不能当 READ_OK。",
        "",
        f"日期表请求区间固定为 from={gate['request_plan']['stocks']['extra'].get('from')} to={gate['request_plan']['stocks']['extra'].get('to')}；主表不臆造日期参数。",
        f"探针是否允许全量下载：{json.dumps(gate['full_download_allowed'], ensure_ascii=False)}。",
        "",
        f"四表状态：{json.dumps(gate['tables'], ensure_ascii=False)}。",
        f"原始下载状态 `{gate['raw_download_status']}`；下载完成不等于验收通过。",
        f"分层状态：{json.dumps({name: item['status'] for name, item in gate['stages'].items()}, ensure_ascii=False)}。",
        f"预登记必需项：{json.dumps(gate['stage_summary']['required'], ensure_ascii=False)}；accepted={str(gate['stage_summary']['accepted']).lower()}。",
        "",
        f"16 个身份案例按永久身份与事件年份解析；无行时为 fixture_list_only / AUTH_REQUIRED。",
        f"Yahoo 对账状态 `{reconcile['status']}`；对照源 {json.dumps(reconcile['comparison_source'], ensure_ascii=False)}。",
        f"volume scope `{gate['volume']['status']}`。",
        f"历史预算状态 `{gate['history_budget']['status']}`；覆盖不等于授权，权限状态 `{gate['entitlement']['status']}`，authorized_range_unknown={str(gate['entitlement']['authorized_range_unknown']).lower()}。",
        f"转换跳过行 {gate['transform']['skipped_n']} 条，逐条记录主键与原因。",
        "",
        f"终态：`{terminal}`。这不是策略赢家状态。全市场选优未启动。",
    ]) + "\n"
    _write(V3 / "provider_probe.json", probe)
    _write(V3 / "schema.json", schema)
    _write(V3 / "identity_contract.json", identity_contract)
    _write(V3 / "delist_identity_acceptance.json", {
        "status": "AUTH_REQUIRED" if all(item.get("verification") == "fixture_list_only" for item in gate["delist"]) else "REVIEW",
        "fixture_n": len(gate["delist"]),
        "cases": gate["delist"],
    })
    _write(V3 / "source_reconciliation.csv", reconciliation_csv(reconcile))
    _write(V3 / "source_reconciliation.json", reconcile)
    _write(V3 / "volume_scope_audit.json", gate["volume"])
    _write(V3 / "history_budget.json", gate["history_budget"])
    _write(V3 / "gap_list.json", gap_list)
    _write(V3 / "acceptance_checks.json", gate["checks"])
    _write(V3 / "event_invariants.json", gate["event_invariants"])
    _write(V3 / "gate_stages.json", {
        "stages": gate["stages"],
        "summary": gate["stage_summary"],
        "raw_download_status": gate["raw_download_status"],
        "terminal_status": terminal,
        "raw_download_complete_is_independent": True,
    })
    _write(V3 / "request_plan.json", {
        "plan": gate["request_plan"],
        "full_download_allowed": gate["full_download_allowed"],
        "allowed_window": {"from": gate["request_plan"]["stocks"]["extra"].get("from"), "to": gate["request_plan"]["stocks"]["extra"].get("to")},
    })
    _write(V3 / "transform_skipped_rows.json", gate["transform"])
    _write(V3 / "connect_accept_report.json", {
        "credential_present": gate["credential_present"],
        "live_sharadar_request_count": gate["live_sharadar_request_count"],
        "tables": gate["tables"],
        "raw_row_counts": gate["raw_row_counts"],
        "isolated_row_counts": gate["isolated_row_counts"],
        "store": gate["store"],
        "stages": {name: item["status"] for name, item in gate["stages"].items()},
        "stage_summary": gate["stage_summary"],
        "raw_download_status": gate["raw_download_status"],
        "entitlement": gate["entitlement"],
        "transform": gate["transform"],
        "terminal_status": terminal,
        "checks": gate["checks"],
        "delist_verification": [item.get("verification") for item in gate["delist"]],
    })
    _write(PACK / "control_current_list_214_index.json", archive)
    _write(PACK / "control_current_list_214_index.md", "\n".join([
        f"# {CONTROL_LABEL}",
        "",
        "旧当前名单路线结果归档。不要把 213/214 差异藏进一个标签。",
        "汽车 D/V 只是已见小池对照，不是全市场基线。",
        "本文件是新索引，不改写被引用的历史 JSON。",
        "",
        f"list_n=214；quality_valid_n={archive.get('quality_valid_n')}；quality_insufficient_n={archive.get('quality_insufficient_n')}。",
    ]) + "\n")
    _write(PACK / "algorithm_sharadar_v3_head.json", head)
    _write(PACK / "algorithm_sharadar_v3_audit.md", audit)
    _write(PACK / "algorithm_sharadar_connect_accept_head.json", head)
    _write(PACK / "algorithm_sharadar_connect_accept_audit.md", audit)
    _write(PACK / "algorithm_sharadar_v3_stop.json", {
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
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
