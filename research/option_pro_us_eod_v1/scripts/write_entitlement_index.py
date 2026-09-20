"""Build the entitlement/history compact index from already-recorded reports.

No vendor requests. Historical AUTH_FAILED wrappers are explained, not rewritten.

    PYTHONPATH=backend python research/option_pro_us_eod_v1/scripts/write_entitlement_index.py
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.research_eod_v1.data.sharadar import (  # noqa: E402
    account_scope_record,
    explain_access_class,
)
from app.services.research_eod_v1.data.sharadar_schema import (  # noqa: E402
    ACCESS_CLASS_VERSION,
    ACCOUNT_SCOPE,
    ACCOUNT_SCOPE_KIND,
    ENV_KEY_NAME,
    OFFICIAL_SCHEMA_FORMATS,
)

PACK = ROOT / "research" / "option_pro_us_eod_v1" / "return_pack" / "sharadar_v3"
STORE = Path.home() / "optix-data" / "authorized_sharadar_samples"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _head() -> str | None:
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    return result.stdout.strip() or None


def _load(name: str) -> dict:
    return json.loads((PACK / name).read_text(encoding="utf-8"))


def _hash_file(path: Path) -> dict:
    digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    return {
        "path": str(path),
        "sha256": digest,
        "bytes": path.stat().st_size if path.is_file() else 0,
        "exists": path.is_file(),
        "not_committed_to_git": True,
    }


def _explain(http_status, vendor_message, **kwargs) -> dict:
    return explain_access_class(http_status, vendor_message, **kwargs)


def main() -> int:
    sample = _load("authorized_sample_report.json")
    history = _load("actions_history_identity_report.json")
    follow = _load("actions_followup_report.json")
    credential_miss = _load("runtime_credential_report.json")
    local_checks = _load("local_checks.json")
    probes = history.get("actions", {}).get("probes", [])
    bulk = history.get("actions", {}).get("bulk_status", [])
    schema = history.get("actions", {}).get("schema", {})
    follow_actions = follow.get("actions", [])
    follow_prices = follow.get("prices", [])
    reconcile = follow.get("reconcile", {})
    identity = history.get("identity", {})

    mapped = []
    for item in probes:
        vendor = (item.get("vendor_error") or {})
        mapped.append({
            "shape": item.get("shape"),
            "http_status": item.get("http_status"),
            "page_status_recorded": item.get("status"),
            "vendor_message": vendor.get("vendor_message"),
            "row_count": item.get("row_count"),
            "not_rewritten": True,
            **_explain(item.get("http_status"), vendor.get("vendor_message"), page_status=item.get("status")),
        })
    for item in bulk:
        vendor = (item.get("vendor_error") or {})
        mapped.append({
            "shape": f"bulk_status years={item.get('years')}",
            "http_status": item.get("http_status"),
            "page_status_recorded": item.get("status"),
            "vendor_message": vendor.get("vendor_message"),
            "not_rewritten": True,
            **_explain(item.get("http_status"), vendor.get("vendor_message"), page_status=item.get("status")),
        })
    mapped.append({
        "shape": "schema/actions?format=json",
        "http_status": schema.get("http_status"),
        "page_status_recorded": schema.get("status"),
        "vendor_message": (schema.get("vendor_error") or {}).get("vendor_message"),
        "not_rewritten": True,
        **_explain(
            schema.get("http_status"),
            (schema.get("vendor_error") or {}).get("vendor_message"),
            endpoint="/v1.0/schema/actions",
            query_format="json",
            page_status=schema.get("status"),
        ),
    })

    artifacts = []
    for rel in (
        STORE / "actions_history_identity" / "followup" / "actions_AAPL_2023-01-01_2024-06-28.jsonl",
        STORE / "actions_history_identity" / "followup" / "actions_MSFT_2023-01-01_2024-06-28.jsonl",
        STORE / "actions_history_identity" / "coverage" / "stocks_MSFT_2024.jsonl",
        STORE / "actions_history_identity" / "coverage" / "funds_SPY_2024.jsonl",
    ):
        artifacts.append(_hash_file(rel))

    payload = {
        "generated_at": _now(),
        "index_head": _head(),
        "access_class_version": ACCESS_CLASS_VERSION,
        "environment": {
            "credential_present": bool((os.environ.get(ENV_KEY_NAME) or "").strip()),
            "massive_present": bool((os.environ.get("MASSIVE_API_KEY") or "").strip()),
            "branch": "cursor/sharadar-data-first-4939",
            "note": "runtime_credential_report.json is a different Cloud Agent run with credential_present=false; it does not overwrite later authorized runs",
        },
        "runs": [
            {
                "source": "authorized_sample_report.json",
                "code_sha": sample.get("code_sha"),
                "generated_at": sample.get("generated_at"),
                "real_supplier_requests": sample.get("real_supplier_requests"),
                "cache_reads": sample.get("cache_reads", 0),
                "mock_requests": sample.get("mock_requests", 0),
                "credential_present": sample.get("credential_present"),
            },
            {
                "source": "runtime_credential_report.json",
                "code_sha": credential_miss.get("head"),
                "generated_at": credential_miss.get("generated_at"),
                "real_supplier_requests": 0,
                "cache_reads": 0,
                "mock_requests": 0,
                "credential_present": credential_miss.get("credential_present"),
            },
            {
                "source": "actions_history_identity_report.json",
                "code_sha": history.get("code_sha"),
                "generated_at": history.get("generated_at"),
                "real_supplier_requests": history.get("real_supplier_requests"),
                "cache_reads": history.get("cache_reads", 0),
                "mock_requests": history.get("mock_requests", 0),
                "credential_present": history.get("credential_present"),
            },
            {
                "source": "actions_followup_report.json",
                "code_sha": follow.get("code_sha"),
                "generated_at": follow.get("generated_at"),
                "real_supplier_requests": follow.get("real_supplier_requests"),
                "cache_reads": 0,
                "mock_requests": 0,
                "credential_present": follow.get("credential_present"),
            },
        ],
        "mapped_access": mapped,
        "identity": {
            "direct_bbby": "READ_OK_EMPTY_lookup_miss",
            "vendor_ticker": ((identity.get("chosen") or {}) if isinstance(identity, dict) else {}).get("ticker"),
            "permaticker": "197799",
            "security_id": "sharadar:197799",
            "prices_and_actions": "403 observed_access_or_quota_limit",
            "smoke_only": True,
            "sixteen_cases_not_claimed": True,
        },
        "coverage": {
            "windows": ["2010-01-04..2010-02-12", "2016-01-04..2016-02-12", "2020-03-02..2020-04-09", "2024-05-20..2024-06-28"],
            "older_windows": "HTTP 200 empty; firstpricedate is not row evidence",
            "window_2024_accessible_rows": 28,
            "followup_actions_inside_allowed_end": [
                {k: item.get(k) for k in ("ticker", "http_status", "row_count", "rows_inside_allowed_end", "status")}
                for item in follow_actions
            ],
            "followup_prices": [
                {k: item.get(k) for k in ("ticker", "http_status", "row_count", "status")}
                for item in follow_prices
            ],
        },
        "reconcile": {
            "comparison_source": "massive_unadjusted_daily",
            "yahoo_label_not_used": True,
            "unadjusted_simple_return": {
                "status": reconcile.get("status"),
                "return_coverage_n": reconcile.get("return_coverage_n"),
                "securities_n": reconcile.get("securities_n"),
            },
            "split_adjusted_geometric_price": "UNKNOWN",
            "total_return_with_dividends": "UNKNOWN",
            "share_basis_volume_and_turnover": "UNKNOWN",
            "session_calendar": "UNKNOWN",
            "economic_ledger": "UNKNOWN",
            "overall_status_does_not_imply_other_fields": True,
        },
        "layers": {
            "master_identity": "PARTIAL: BBBYQ/197799 resolved; 16-case set not done",
            "price_coverage": "PARTIAL: 2024 accessible names have rows; 2010/2016/2020 empty; BBBYQ/XOM/INTC limited",
            "actions": "PARTIAL: AAPL/MSFT single-ticker allowed-window rows; multi-ticker and BBBYQ 403",
            "economic_settlement": "BLOCKED: acquisitionof is not a documented cash unit; share-basis proof now binds the target",
        },
        "local_checks": {
            "groups": local_checks.get("groups"),
            "satisfied": local_checks.get("satisfied"),
            "not_satisfied": local_checks.get("not_satisfied"),
        },
        "artifacts": artifacts,
        "restore": {
            "authorized_dir": str(STORE / "actions_history_identity"),
            "paid_rows_not_in_git": True,
            "does_not_overwrite_mock_or_full_store": True,
        },
        "account_scope": account_scope_record(),
        "entitlement_conclusion": {
            "terminal": ACCOUNT_SCOPE,
            "previous_terminal": "B",
            "kind": ACCOUNT_SCOPE_KIND,
            "not_a_vendor_error_code": True,
            "owner_confirmed": True,
            "purchased_sku": False,
            "vendor_reply": None,
            "http_401_seen": False,
            "same_key_reads_stocks_funds_tickers": True,
            "observed_access_or_quota_limit": "403 Exceeds free tier on multi-ticker, BBBYQ, some late names; now out of scope for this free Sample account",
            "forbidden_reason_unknown": "bulk status=True 403 Forbidden; bulk is out of scope, not a purchased-SKU mystery",
            "older_history_empty_200": True,
            "long_history_full_market_bulk_not_purchased_entitlement_bugs": True,
            "schema_format_json_400_not_subscription": True,
            "official_schema_formats": list(OFFICIAL_SCHEMA_FORMATS),
            "next_action": "pause formal data phase until a source and budget are decided; do not rerun failed coverage probes; do not ask a new agent to fix coverage",
            "owner": "project_owner_confirmed",
            "do_not_split_into_unlimited_single_ticker_windows": True,
            "do_not_rerun_same_failed_probes": True,
            "do_not_ask_new_agent_to_fix_coverage": True,
            "do_not_auto_purchase": True,
            "do_not_resume_old_214_weight_search": True,
            "ten_year_formal_requirement_not_lowered": True,
            "formal_data_phase": "paused_until_source_and_budget",
        },
        "full_backfill_started": False,
        "executed_backtests": 0,
        "purchase_attempted": False,
        "yahoo_fallback": False,
        "factor_or_weight_search": False,
    }
    (PACK / "entitlement_and_history_index.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )

    md = [
        f"# 权限与长历史索引（账户范围 {ACCOUNT_SCOPE}）",
        "",
        f"索引 head：`{payload['index_head']}`。映射版本 `{ACCESS_CLASS_VERSION}`。账户范围 `{ACCOUNT_SCOPE}`（`{ACCOUNT_SCOPE_KIND}`，不是供应商错误码）。不倒写历史原始响应。本轮无新的供应商失败探针。",
        "",
        "## 各次运行（分别记账）",
        "",
        "| 文件 | code_sha | 时间 | 凭据 | 真实请求 | 缓存 | mock |",
        "| --- | --- | --- | --- | ---: | ---: | ---: |",
    ]
    for run in payload["runs"]:
        md.append(
            f"| `{run['source']}` | `{run['code_sha']}` | {run['generated_at']} | {run['credential_present']} | {run['real_supplier_requests']} | {run['cache_reads']} | {run['mock_requests']} |"
        )
    md += [
        "",
        "`runtime_credential_report.json` 是另一次 Cloud Agent（无环境变量）。它不覆盖后续已授权运行。",
        "",
        "## AUTH_FAILED 包装的解释（原始 HTTP 保留）",
        "",
        "| 请求 | HTTP | 厂商 message | 记录的页状态 | access_class |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in mapped:
        md.append(
            f"| {item['shape']} | {item['http_status']} | {item.get('vendor_message') or '—'} | {item['page_status_recorded']} | `{item['access_class']}` |"
        )
    md += [
        "",
        "- `observed_access_or_quota_limit`：已观察到的访问范围/配额限制，不推导“用户没订阅”。",
        "- `forbidden_reason_unknown`：bulk `403 Forbidden`，原因未知。",
        "- `unsupported_schema_format`：`format=json` 的 400，不是订阅证据。官方 format 为 postgres/sqlite/mysql；已有 `ACTIONS_FIELDS`，不再试错。",
        "- 本轮没有 HTTP 401。",
        "",
        "## 分层状态",
        "",
        f"- 主表身份：{payload['layers']['master_identity']}",
        f"- 价格覆盖：{payload['layers']['price_coverage']}",
        f"- 行动：{payload['layers']['actions']}",
        f"- 经济结算：{payload['layers']['economic_settlement']}",
        "",
        "## 对账字段",
        "",
        "对照源是 Massive `adjusted=false`，不是 Yahoo。",
        "",
        "| 字段 | 状态 |",
        "| --- | --- |",
        f"| 未复权简单收益 | {reconcile.get('status')}，{reconcile.get('return_coverage_n')} 对 / {reconcile.get('securities_n')} 证券 |",
        "| 拆股复权几何价格 | UNKNOWN |",
        "| 含分红总回报 | UNKNOWN |",
        "| 真实股份口径成交量与成交额 | UNKNOWN |",
        "| 时段口径 | UNKNOWN |",
        "| 经济账本 | UNKNOWN |",
        "",
        "一个总 PASS 不宣称其他字段也已通过。",
        "",
        "## 工件",
        "",
    ]
    for item in artifacts:
        md.append(f"- `{item['path']}` sha256=`{item['sha256']}` bytes={item['bytes']}（不进公开 git）")
    md += [
        "",
        "## 结论",
        "",
        f"负责人已确认：本账户仅免费 Sample，未购买任何套餐。当前 Sharadar 能力标记为 **{ACCOUNT_SCOPE}**。这是账户范围标记，不是新的供应商错误码。",
        "",
        "已记录的 403 / 空长历史 / bulk Forbidden 是免费 Sample 的范围外访问，不再当作已购权限异常排查，不再重复失败探针，也不再要求更换 agent 去补覆盖。",
        "",
        "适配器与既有工程修复保留。不自动购买，不恢复旧 214 小池权重搜索，不降低十年以上正式验证要求。数据来源与预算确定后，再恢复正式数据阶段。",
        "",
        "先前终态 B（等账户持有人向供应商确认产品）已由负责人确认关闭。",
        "",
        "`full_backfill_started=false`，`executed_backtests=0`，`purchase_attempted=false`。",
        "",
    ]
    (PACK / "entitlement_and_history_index.md").write_text("\n".join(md), encoding="utf-8")
    print(json.dumps({
        "index_head": payload["index_head"],
        "terminal": ACCOUNT_SCOPE,
        "mapped_n": len(mapped),
        "out": [
            "research/option_pro_us_eod_v1/return_pack/sharadar_v3/entitlement_and_history_index.md",
            "research/option_pro_us_eod_v1/return_pack/sharadar_v3/entitlement_and_history_index.json",
        ],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
