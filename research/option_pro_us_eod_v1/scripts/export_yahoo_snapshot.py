"""Write the current-universe Yahoo bars into the return pack for PR review."""

from __future__ import annotations

import hashlib
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.research_eod_v1.config_load import load_registry  # noqa: E402
from app.services.research_eod_v1.data.to_series import bars_to_series  # noqa: E402
from app.services.research_eod_v1.data.yahoo import DOWNLOAD_PARAMS, YahooDiagnosticProvider  # noqa: E402
from app.services.research_eod_v1.fixtures import as_of_after_close  # noqa: E402
from app.services.research_eod_v1.snapshot import compute_snapshot  # noqa: E402
from app.services.research_eod_v1.universe_audit import ETF_SUBASSET_HINTS  # noqa: E402
from app.services.research_eod_v1.venue import CURRENT_UNIVERSE_VENUE_NOTES  # noqa: E402
from app.services.sectors import SECTORS  # noqa: E402

START = date(2015, 1, 2)
END = date(2026, 9, 17)
OUT = Path(__file__).resolve().parents[1] / "return_pack" / "yahoo_current_universe"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _venue(ticker: str, track: str) -> dict:
    note = CURRENT_UNIVERSE_VENUE_NOTES.get(ticker)
    if note:
        return {
            "listing_country": note.get("listing_country") or "US",
            "exchange": note.get("exchange") or "NASDAQ",
            "security_type": "ETF" if track == "etf" else "CS",
        }
    return {
        "listing_country": "US",
        "exchange": "NASDAQ",
        "mic": "XNAS",
        "security_type": "ETF" if track == "etf" else "CS",
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    appearances: dict[str, list[str]] = defaultdict(list)
    for theme_id, sector in SECTORS.items():
        for ticker in sector["tickers"]:
            appearances[ticker].append(theme_id)
    provider = YahooDiagnosticProvider(allow_network=True)
    batched: dict[str, list] = {}
    tickers = list(appearances)
    for offset in range(0, len(tickers), 40):
        batched.update(provider.fetch_daily_bars_batch(tickers[offset : offset + 40], START, END))

    bar_rows: list[dict] = []
    master_rows: list[dict] = []
    series_map = {}
    for ticker, themes in appearances.items():
        track = "etf" if ticker in ETF_SUBASSET_HINTS or themes == ["etfs"] else "stock"
        bars = batched.get(ticker) or []
        master_rows.append(
            {
                "security_id": ticker,
                "provider_symbol": ticker,
                "themes": "|".join(themes),
                "asset_track": track,
                "bars": len(bars),
                "first": bars[0].session_date.isoformat() if bars else "",
                "last": bars[-1].session_date.isoformat() if bars else "",
            }
        )
        for bar in bars:
            bar_rows.append(
                {
                    "security_id": bar.security_id,
                    "session_date": bar.session_date.isoformat(),
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "raw_open": bar.raw_open,
                    "raw_close": bar.raw_close,
                    "volume": bar.volume,
                    "dollar_volume": bar.dollar_volume,
                    "tri": bar.tri,
                    "volume_scope": bar.volume_scope,
                    "price_adjustment": bar.price_adjustment,
                    "vintage_status": bar.vintage_status,
                }
            )
        if bars:
            series = bars_to_series(
                bars,
                security_id=ticker,
                asset_track=track,
                theme_ids=tuple(themes),
                industry_id=themes[0],
                parent_industry_id=themes[0],
                venue_metadata=_venue(ticker, track),
            )
            if series is not None:
                series_map[ticker] = series

    bars_path = OUT / "daily_bars.parquet"
    master_path = OUT / "security_master.csv"
    snap_path = OUT / "theme_snapshot_rows.parquet"
    pd.DataFrame(bar_rows).to_parquet(bars_path, index=False)
    pd.DataFrame(master_rows).to_csv(master_path, index=False, lineterminator="\n")

    registry = load_registry()
    last_dates = [series.dates[-1] for series in series_map.values()]
    session = max(last_dates)
    as_of = as_of_after_close(session)
    snap_rows: list[dict] = []
    for theme_id in SECTORS:
        payload = compute_snapshot(
            as_of,
            series_map,
            "u_current_yahoo_diagnostic",
            registry,
            sector_id=theme_id,
            algorithm="A_trend_quality",
            profile="balanced",
            horizon="mid",
        )
        for row in payload["rows"]:
            snap_rows.append(
                {
                    "theme_id": theme_id,
                    "security_id": row["security_id"],
                    "session_date": row["session_date"],
                    "status": row["status"],
                    "score": row.get("score"),
                    "adv20": row.get("adv20"),
                    "atr": row.get("atr"),
                    "profile": row.get("profile"),
                    "horizon": row.get("horizon"),
                    "stock_or_etf_track": row.get("stock_or_etf_track"),
                }
            )
    pd.DataFrame(snap_rows).to_parquet(snap_path, index=False)

    retrieved = datetime.now(timezone.utc).isoformat()
    manifest = {
        "provider": "yahoo_yfinance",
        "dataset_id": "yahoo-current-universe-diagnostic",
        "dataset_version": "2026-09-16",
        "retrieved_at": retrieved,
        "request_params_redacted": {
            "start": START.isoformat(),
            "end_exclusive": END.isoformat(),
            **DOWNLOAD_PARAMS,
        },
        "tickers": len(master_rows),
        "bar_rows": len(bar_rows),
        "snapshot_rows": len(snap_rows),
        "session_date": session.isoformat(),
        "files": {
            "daily_bars.parquet": _sha256(bars_path),
            "security_master.csv": _sha256(master_path),
            "theme_snapshot_rows.parquet": _sha256(snap_path),
        },
        "notes": [
            "CURRENT_UNIVERSE_DIAGNOSTIC",
            "download_time_not_pit",
            "Close is not verified raw trade price",
            "not a 10-year PIT market backtest",
        ],
        "secret_present": False,
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (OUT / "README.md").write_text(
        "\n".join(
            [
                "# Yahoo 当前池诊断快照",
                "",
                "本目录是当前 24 主题名单的 Yahoo 日线，供 PR 复核。",
                "不是十年 PIT，不是退市并集，Close 未核验为未复权成交价。",
                "",
                f"- 证券数：{len(master_rows)}",
                f"- 日线条数：{len(bar_rows)}",
                f"- 信号日：{session.isoformat()}",
                f"- 文件：`daily_bars.parquet`、`security_master.csv`、`theme_snapshot_rows.parquet`、`manifest.json`",
                "",
            ]
        )
        ,
        encoding="utf-8",
    )
    print(json.dumps({"tickers": len(master_rows), "bars": len(bar_rows), "session": session.isoformat(), "out": str(OUT)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
