"""Current-universe Yahoo diagnostic. Not a PIT market backtest."""

from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.research_eod_v1.config_load import load_registry  # noqa: E402
from app.services.research_eod_v1.data.to_series import bars_to_series  # noqa: E402
from app.services.research_eod_v1.data.yahoo import YahooDiagnosticProvider  # noqa: E402
from app.services.research_eod_v1.calendar_asof import capture_as_of, last_complete_eod_session  # noqa: E402
from app.services.research_eod_v1.snapshot import compute_snapshot  # noqa: E402
from app.services.research_eod_v1.universe_audit import ETF_SUBASSET_HINTS  # noqa: E402
from app.services.research_eod_v1.venue import CURRENT_UNIVERSE_VENUE_NOTES  # noqa: E402
from app.services.sectors import SECTORS  # noqa: E402

START = date(2015, 1, 2)
END = date(2026, 9, 17)


def _venue(ticker: str, track: str) -> dict:
    note = CURRENT_UNIVERSE_VENUE_NOTES.get(ticker)
    if note:
        return {
            "listing_country": note.get("listing_country") or "US",
            "exchange": note.get("exchange") or "NASDAQ",
            "security_type": "ETF" if track == "etf" else "CS",
            "identity_confidence": "unverified_current_universe_note",
            "industry_source": "theme_tag_diagnostic_not_economic_parent",
        }
    return {
        "listing_country": "US",
        "exchange": "NASDAQ",
        "mic": "XNAS",
        "security_type": "ETF" if track == "etf" else "CS",
        "identity_confidence": "unverified_default_not_checked",
        "industry_source": "theme_tag_diagnostic_not_economic_parent",
    }


def main() -> int:
    pack = Path(__file__).resolve().parents[1] / "return_pack"
    reports = pack / "sector_reports"
    reports.mkdir(parents=True, exist_ok=True)
    provider = YahooDiagnosticProvider(allow_network=True)
    appearances: dict[str, list[str]] = defaultdict(list)
    for theme_id, sector in SECTORS.items():
        for ticker in sector["tickers"]:
            appearances[ticker].append(theme_id)
    coverage_rows = []
    series_map = {}
    tickers = list(appearances)
    batched: dict[str, list] = {}
    for offset in range(0, len(tickers), 40):
        chunk = tickers[offset : offset + 40]
        batched.update(provider.fetch_daily_bars_batch(chunk, START, END))
    for ticker, themes in appearances.items():
        track = "etf" if ticker in ETF_SUBASSET_HINTS or themes == ["etfs"] else "stock"
        bars = batched.get(ticker) or []
        n = len(bars)
        first = bars[0].session_date.isoformat() if n else ""
        last = bars[-1].session_date.isoformat() if n else ""
        coverage_rows.append(
            {
                "ticker": ticker,
                "themes": "|".join(themes),
                "track": track,
                "bars": n,
                "first": first,
                "last": last,
                "status": "ok" if n else "empty",
            }
        )
        if n:
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
    (pack / "download_coverage.csv").write_text("", encoding="utf-8")
    with (pack / "download_coverage.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            lineterminator="\n",
            fieldnames=["ticker", "themes", "track", "bars", "first", "last", "status"],
        )
        writer.writeheader()
        writer.writerows(coverage_rows)
    failures = list(provider.failures)
    (pack / "download_failures.json").write_text(json.dumps(failures, indent=2) + "\n", encoding="utf-8")

    registry = load_registry()
    theme_cards = []
    if "SPY" not in series_map and "SPY" in appearances:
        pass
    clock = capture_as_of(datetime.now(timezone.utc))
    session = last_complete_eod_session(clock) if series_map else None
    as_of = clock if session else None
    for theme_id, sector in SECTORS.items():
        if as_of is None:
            card = {"theme_id": theme_id, "status": "DATA_INSUFFICIENT", "candidates": 0, "eligible": 0}
        else:
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
            eligible = [row for row in payload["rows"] if row.get("status") == "eligible"]
            card = {
                "theme_id": theme_id,
                "status": "CURRENT_UNIVERSE_DIAGNOSTIC",
                "session_date": payload["session_date"],
                "candidates": len(payload["candidate_ids"]),
                "reference": len(payload["reference_ids"]),
                "eligible": len(eligible),
                "capability_flags": [
                    "CURRENT_UNIVERSE_DIAGNOSTIC",
                    "PIT_CLASSIFICATION_MISSING",
                    "CORPORATE_ACTIONS_INCOMPLETE",
                    "EXECUTION_DATA_UNVERIFIED",
                    "SURVIVORSHIP_RISK",
                ],
            }
            (reports / f"{theme_id}.md").write_text(
                "\n".join(
                    [
                        f"# {theme_id}",
                        "",
                        f"- 状态：`CURRENT_UNIVERSE_DIAGNOSTIC`",
                        f"- 信号日：`{payload['session_date']}`",
                        f"- 当前名单候选：{card['candidates']}",
                        f"- 参考池：{card['reference']}",
                        f"- A/balanced/mid 当日合格：{card['eligible']}",
                        f"- 下载成功成员：{sum(1 for row in coverage_rows if theme_id in row['themes'] and row['status']=='ok')}/{len(sector['tickers'])}",
                        "- 不是十年 PIT 回测，不是赢家，不晋升。",
                        "- Yahoo Close 未核验为历史未复权成交价；成交与容量不可评估。",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
        theme_cards.append(card)
    (pack / "theme_diagnostic.json").write_text(json.dumps({"as_of": None if as_of is None else as_of.isoformat(), "cards": theme_cards}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"downloaded": len(series_map), "themes": len(theme_cards), "failures": len(failures)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
