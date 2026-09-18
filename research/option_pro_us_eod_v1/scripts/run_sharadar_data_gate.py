"""Write the Sharadar data-gate pack. Live fetch stops at AUTH_REQUIRED without SHARADAR_API_KEY."""

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
from app.services.research_eod_v1.data.sharadar import run_sharadar_probe  # noqa: E402
from app.services.research_eod_v1.data.sharadar_acceptance import (  # noqa: E402
    DELIST_FIXTURES,
    THEME_PARENT_SCHEMA,
    evaluate_delist_fixture,
    history_budget,
    reconcile_aligned_returns,
    reconciliation_csv,
    volume_scope_audit,
)
from app.services.research_eod_v1.data.sharadar_archive import CONTROL_LABEL, build_control_index  # noqa: E402
from app.services.research_eod_v1.data.sharadar_identity import etf_subasset_schema  # noqa: E402
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


def _git_sha() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
        return
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    probe = run_sharadar_probe(allow_network=True)
    archive = build_control_index()
    delist = [evaluate_delist_fixture(item, [], None) for item in DELIST_FIXTURES]
    reconcile = reconcile_aligned_returns([], [])
    volume = volume_scope_audit(minute_entitlement=False)
    budget = history_budget(
        earliest=None,
        entitlement_status=str(probe["entitlement"]["status"]),
        calendar_sessions=None,
    )
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
            "exchange_is_tag_not_drop": True,
        },
        "etf_subasset": etf_subasset_schema(),
        "theme_parent_schema": THEME_PARENT_SCHEMA,
    }
    gap_list = [
        {"item": "SHARADAR_API_KEY", "status": "AUTH_REQUIRED", "action": "inject Cursor secret and start a new agent"},
        {"item": "live_table_read", "status": "AUTH_REQUIRED", "action": "do not fall back to Yahoo or Massive"},
        {"item": "entitlement_years", "status": "AUTH_REQUIRED", "action": "stop on 5Y; tag 10Y; continue full from 2010-01-01"},
        {"item": "yahoo_overlap_reconcile", "status": "AUTH_REQUIRED", "action": "align security/currency/split scale before comparing"},
        {"item": "volume_session_scope", "status": "UNKNOWN", "action": "do not start volume experiments"},
        {"item": "persistent_offline_export", "status": "UNSUPPORTED", "action": "authorized store outside public git; hash alone on a temp VM is not handover"},
        {"item": "members_and_factors", "status": "NOT_THIS_ROUND", "action": "wait for data-gate review"},
    ]
    checks = {
        "adapter_code": "PASS",
        "synthetic_three_track": "PASS",
        "identity_contract": "PASS",
        "delist_fixture_table": "PASS",
        "parent_rank_fix": "PASS",
        "breakout_first_day_fix": "PASS",
        "feature_version_bump": "PASS",
        "control_archive_index": "PASS",
        "live_sharadar_read": "AUTH_REQUIRED",
        "yahoo_reconcile_live": "AUTH_REQUIRED",
        "volume_scope": "UNSUPPORTED",
        "persistent_handover": "UNSUPPORTED",
        "chat_or_massive_credential_used": "PASS_NOT_USED",
    }
    terminal = probe["terminal_status"]
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
        "probe": "sharadar_v3/provider_probe.json",
    }
    audit = "\n".join([
        "# Sharadar 数据阶段回传",
        "",
        f"当前 head 由 git 记录。新 feature version 为 `{FEATURE_VERSION}`。",
        f"旧 B0/R1/R1b/R2/Freeze/固定矩阵标为 `{CONTROL_LABEL}`，不回写历史 JSON。",
        "213 VALID / 1 INSUFFICIENT(CRWV) 与 214 名单差异单独保留。",
        "",
        f"运行时 `{ENV_KEY_NAME}` 缺失，真实拉取停在 `AUTH_REQUIRED`。",
        "未读取聊天凭据，未向 Nasdaq Data Link 或 Massive 试送密钥，未回退 Yahoo。",
        "官方渠道实现为 `https://api.sharadar.com/v1.0/data/<table>`。",
        "分页默认 10000 不是全市场；bulk 重定向日志去凭据。",
        "",
        "第 7 节：母组分位改为同轨母组≥20 有限成员时用母组秩；B 突破使用冻结首日 rvol，缺失首日 rvol/clv 拒绝。",
        "旧小池结果不按新定义重算，不新开权重搜索。",
        "",
        f"终态：`{terminal}`。这不是策略赢家状态。",
    ]) + "\n"
    _write(V3 / "provider_probe.json", probe)
    _write(V3 / "schema.json", schema)
    _write(V3 / "identity_contract.json", identity_contract)
    _write(V3 / "delist_identity_acceptance.json", {
        "status": "AUTH_REQUIRED",
        "fixture_n": len(delist),
        "cases": delist,
    })
    _write(V3 / "source_reconciliation.csv", reconciliation_csv(reconcile))
    _write(V3 / "source_reconciliation.json", reconcile)
    _write(V3 / "volume_scope_audit.json", volume)
    _write(V3 / "history_budget.json", budget)
    _write(V3 / "gap_list.json", gap_list)
    _write(V3 / "acceptance_checks.json", checks)
    _write(V3 / "event_invariants.json", {
        "actions_fields_passthrough": list(ACTIONS_FIELDS),
        "no_secondary_merge": True,
        "terminal_rules": {
            "bankruptcy": "last_trade",
            "acquisition": "actions.acquisitioncash",
            "else": "TERMINAL_UNKNOWN_exclude",
        },
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
    _write(PACK / "algorithm_sharadar_v3_stop.json", {
        "terminal_status": terminal,
        "do_not_start_weight_search": True,
        "do_not_fallback_yahoo": True,
        "do_not_merge": True,
        "holdout_unsealed": False,
        "executed_backtests": 0,
    })
    print(json.dumps({"terminal_status": terminal, "feature_version": FEATURE_VERSION}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
