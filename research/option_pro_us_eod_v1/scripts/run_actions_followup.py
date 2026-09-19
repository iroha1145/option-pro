"""Single-ticker / short-window follow-up after free-tier 403s.

    PYTHONPATH=backend python research/option_pro_us_eod_v1/scripts/run_actions_followup.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.research_eod_v1.data.massive_env import MassiveEnvProvider  # noqa: E402
from app.services.research_eod_v1.data.sharadar import SharadarClient, credential_present  # noqa: E402
from app.services.research_eod_v1.data.sharadar_acceptance import reconcile_aligned_returns  # noqa: E402
from app.services.research_eod_v1.data.sharadar_identity import identity_from_ticker_row  # noqa: E402
from app.services.research_eod_v1.data.sharadar_schema import (  # noqa: E402
    ALLOWED_END,
    ENV_KEY_NAME,
    RECONCILE_MIN_RETURN_COVERAGE,
    RECONCILE_MIN_SECURITIES,
)
from app.services.research_eod_v1.mathutil import finite  # noqa: E402

PACK = ROOT / "research" / "option_pro_us_eod_v1" / "return_pack" / "sharadar_v3"
STORE = Path.home() / "optix-data" / "authorized_sharadar_samples" / "actions_history_identity"
MAIN = PACK / "actions_history_identity_report.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _head() -> str | None:
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    return result.stdout.strip() or None


def _store(path: Path, rows: list[dict]) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8")
    return {"path": str(path), "rows": len(rows), "bytes": path.stat().st_size, "not_committed_to_git": True}


def _brief(page) -> dict:
    rows = list(page.rows)
    dates = sorted({str(row.get("date") or "")[:10] for row in rows if row.get("date")})
    inside = [item for item in dates if item <= str(ALLOWED_END)]
    return {
        "status": page.status,
        "http_status": page.http_status,
        "row_count": page.row_count,
        "first_date": dates[0] if dates else None,
        "last_date": dates[-1] if dates else None,
        "rows_inside_allowed_end": len(inside),
        "rows_after_allowed_end": sum(1 for item in dates if item > str(ALLOWED_END)),
        "vendor_error": page.vendor_error,
    }


def _returns(rows: list[dict], *, price_field: str, security_id: str) -> list[dict]:
    ordered = []
    for row in rows:
        raw = str(row.get("date") or row.get("session_date") or "")[:10]
        try:
            session = date.fromisoformat(raw)
        except ValueError:
            continue
        price = finite(row.get(price_field))
        if price is None or price <= 0:
            continue
        ordered.append((session, price, finite(row.get("volume"))))
    ordered.sort()
    out = []
    previous = None
    for session, price, volume in ordered:
        if previous is not None:
            out.append({
                "security_id": security_id,
                "session_date": session.isoformat(),
                "return": price / previous[1] - 1.0,
                "volume": volume,
            })
        previous = (session, price, volume)
    return out


def main() -> int:
    present = bool((os.environ.get(ENV_KEY_NAME) or "").strip())
    assert present is credential_present()
    print({"credential_present": present})
    if not present:
        return 2
    client = SharadarClient(allow_network=True)
    follow = {
        "generated_at": _now(),
        "code_sha": _head(),
        "credential_present": present,
        "note": "single ticker and short windows after Exceeds free tier on multi-ticker or 2010-2024 spans",
    }
    actions = []
    for ticker, window in (
        ("AAPL", {"from": "2023-01-01", "to": str(ALLOWED_END)}),
        ("MSFT", {"from": "2023-01-01", "to": str(ALLOWED_END)}),
        ("AAPL", {"from": "2024-06-24", "to": str(ALLOWED_END)}),
        ("BBBYQ", {"from": "2023-01-01", "to": "2023-05-31"}),
    ):
        page = client.fetch_page("actions", extra={"ticker": ticker, **window})
        artifact = _store(STORE / "followup" / f"actions_{ticker}_{window['from']}_{window['to']}.jsonl", list(page.rows))
        actions.append({"ticker": ticker, "window": window, **_brief(page), "artifact": artifact})

    prices = []
    for ticker, window in (
        ("BBBYQ", {"from": "2023-04-03", "to": "2023-05-02"}),
        ("BBBYQ", {"from": "2024-05-20", "to": str(ALLOWED_END)}),
        ("XOM", {"from": "2024-05-20", "to": str(ALLOWED_END)}),
        ("HD", {"from": "2024-05-20", "to": str(ALLOWED_END)}),
        ("CSCO", {"from": "2024-05-20", "to": str(ALLOWED_END)}),
    ):
        page = client.fetch_page("stocks", extra={"ticker": ticker, **window})
        artifact = _store(STORE / "followup" / f"stocks_{ticker}_{window['from']}_{window['to']}.jsonl", list(page.rows))
        prices.append({"ticker": ticker, "window": window, **_brief(page), "artifact": artifact})

    hd_master = client.fetch_page("tickers", extra={"ticker": "HD"})
    hd_row = next((row for row in hd_master.rows if str(row.get("table") or "").lower() in {"", "stocks", "sep"}), None)
    hd_identity = identity_from_ticker_row(hd_row).to_dict() if hd_row and hd_row.get("permaticker") else None

    control = MassiveEnvProvider(allow_network=True)
    start = date.fromisoformat("2024-05-20")
    end = ALLOWED_END + timedelta(days=1)
    sharadar_returns = []
    control_returns = []
    per = []
    existing = {
        "MSFT": "sharadar:198508",
        "AAPL": "sharadar:199059",
        "JNJ": "sharadar:199185",
        "JPM": "sharadar:199853",
        "WMT": "sharadar:199233",
        "PG": "sharadar:199411",
        "KO": "sharadar:199839",
        "IBM": "sharadar:199623",
        "SPY": "sharadar:118691",
    }
    for ticker, sid, table in [(*item, "funds" if item[0] == "SPY" else "stocks") for item in existing.items()]:
        path = STORE / "coverage" / f"{table}_{ticker}_2024.jsonl"
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.is_file() else []
        s_ret = _returns(rows, price_field="closeunadj", security_id=sid)
        bars = control.fetch_daily_bars(ticker, start, end)
        m_rows = []
        if isinstance(bars, list):
            m_rows = [{"date": bar.session_date.isoformat(), "closeunadj": bar.raw_close, "volume": bar.volume} for bar in bars]
        m_ret = _returns(m_rows, price_field="closeunadj", security_id=sid)
        sharadar_returns.extend(s_ret)
        control_returns.extend(m_ret)
        per.append({"ticker": ticker, "security_id": sid, "sharadar_pairs": len(s_ret), "control_pairs": len(m_ret), "control_status": "READ_OK" if isinstance(bars, list) else str(bars)})

    extras = {"XOM": None, "HD": hd_identity, "CSCO": None}
    for item in prices:
        ticker = item["ticker"]
        if ticker == "BBBYQ":
            continue
        path = Path(item["artifact"]["path"])
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.is_file() else []
        if ticker == "HD" and hd_identity:
            sid = hd_identity["security_id"]
        elif ticker == "XOM":
            sid = "sharadar:199739"
        else:
            master = client.fetch_page("tickers", extra={"ticker": ticker})
            row = next((row for row in master.rows if str(row.get("table") or "").lower() in {"", "stocks", "sep"}), None)
            sid = f"sharadar:{row['permaticker']}" if row and row.get("permaticker") else ticker
            extras[ticker] = identity_from_ticker_row(row).to_dict() if row and row.get("permaticker") else None
        s_ret = _returns(rows, price_field="closeunadj", security_id=sid)
        bars = control.fetch_daily_bars(ticker, start, end)
        m_rows = []
        if isinstance(bars, list):
            m_rows = [{"date": bar.session_date.isoformat(), "closeunadj": bar.raw_close, "volume": bar.volume} for bar in bars]
        m_ret = _returns(m_rows, price_field="closeunadj", security_id=sid)
        sharadar_returns.extend(s_ret)
        control_returns.extend(m_ret)
        per.append({"ticker": ticker, "security_id": sid, "sharadar_pairs": len(s_ret), "control_pairs": len(m_ret), "control_status": "READ_OK" if isinstance(bars, list) else str(bars)})

    reconcile = reconcile_aligned_returns(
        sharadar_returns,
        control_returns,
        source_available=bool(control_returns),
        source={
            "kind": "massive_unadjusted_daily",
            "available": bool(control_returns),
            "not_sent_to_api_sharadar_com": True,
            "price_basis": "sharadar_closeunadj_vs_massive_adjusted_false",
            "older_windows_empty_on_this_account": True,
        },
        min_return_coverage=RECONCILE_MIN_RETURN_COVERAGE,
        min_securities=RECONCILE_MIN_SECURITIES,
    )
    reconcile.pop("rows", None)
    follow.update({
        "actions": actions,
        "prices": prices,
        "hd_identity": hd_identity,
        "extra_identities": extras,
        "reconcile": reconcile,
        "per_security": per,
        "real_supplier_requests": client.live_request_count,
        "channel_confirmed": client.channel_confirmed(),
        "purchase_attempted": False,
        "full_market_bulk_started": False,
    })
    MAIN.parent.mkdir(parents=True, exist_ok=True)
    (PACK / "actions_followup_report.json").write_text(json.dumps(follow, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    if MAIN.exists():
        main = json.loads(MAIN.read_text(encoding="utf-8"))
        main["followup"] = {
            "generated_at": follow["generated_at"],
            "actions": actions,
            "prices": prices,
            "reconcile": reconcile,
            "per_security": per,
            "real_supplier_requests": follow["real_supplier_requests"],
        }
        main["terminal_status"] = "PARTIAL_EVIDENCE"
        if any(item.get("rows_inside_allowed_end") for item in actions):
            main["terminal_status"] = "ACTIONS_BOUNDED_NONEMPTY"
        MAIN.write_text(json.dumps(main, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    print(json.dumps({
        "actions": [{k: item[k] for k in ("ticker", "status", "http_status", "row_count", "rows_inside_allowed_end", "vendor_error")} for item in actions],
        "prices": [{k: item[k] for k in ("ticker", "status", "http_status", "row_count", "vendor_error")} for item in prices],
        "reconcile_status": reconcile["status"],
        "return_coverage_n": reconcile["return_coverage_n"],
        "securities_n": reconcile["securities_n"],
        "insufficient_reason": reconcile.get("insufficient_reason"),
        "live_requests": client.live_request_count,
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
