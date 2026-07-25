"""Offline container smoke for Optix 宏观环境.

Runs one complete MacroConditionsTask inside the production image with a fake
FRED transport and a synthetic ETF fixture, then checks the macro database and
the read API. It never touches the real network: the FRED client gets an
``httpx.MockTransport`` and the ETF chain gets a local generator.

Prints one JSON object on stdout for the CI step to assert on.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import math
import sys
from pathlib import Path

import httpx

from app.config import Settings
from app.personal_config import MacroConfig, get_personal_config
from app.services.macro_conditions.registry import (
    ETF_SYMBOLS,
    FACTORS,
    MODULES,
    SCORING_VERSION,
    SERIES_BY_ID,
)
from app.services.macro_conditions.repository import SCHEMA_VERSION, MacroRepository
from app.worker.tasks import MacroConditionsTask


FAKE_KEY = "0123456789abcdef0123456789abcdef"
END = dt.date(2026, 7, 23)
START = END - dt.timedelta(days=366 * 8)

_UNITS = {
    "usd_amount": "Millions of U.S. Dollars",
    "percent": "Percent",
    "index": "Index",
    "usd_per_barrel": "Dollars per Barrel",
    "usd_per_mmbtu": "Dollars per Million BTU",
}
_BASE = {
    "usd_amount": 3_000_000.0,
    "percent": 4.0,
    "index": 20.0,
    "usd_per_barrel": 78.0,
    "usd_per_mmbtu": 3.2,
}


def _observation_dates(series_id: str) -> list[dt.date]:
    spec = SERIES_BY_ID[series_id]
    days: list[dt.date] = []
    cursor = START
    while cursor <= END:
        if spec.expected_frequency == "W":
            if cursor.weekday() == (4 if series_id == "NFCI" else 2):
                days.append(cursor)
        elif cursor.weekday() < 5:
            days.append(cursor)
        cursor += dt.timedelta(days=1)
    return days


def _fred_handler(request: httpx.Request) -> httpx.Response:
    assert str(request.url).startswith("https://api.stlouisfed.org"), request.url
    series_id = request.url.params.get("series_id")
    spec = SERIES_BY_ID[series_id]
    family = spec.expected_units_family
    if request.url.path == "/fred/series":
        payload = {
            "seriess": [
                {
                    "id": series_id,
                    "units": _UNITS[family],
                    "frequency_short": spec.expected_frequency,
                    "last_updated": "2026-07-24 08:31:02-05",
                    "realtime_start": "2026-07-24",
                    "realtime_end": "9999-12-31",
                }
            ]
        }
        return httpx.Response(
            200, json=payload, headers={"Content-Type": "application/json"}
        )
    base = _BASE[family]
    phase = sum(ord(character) for character in series_id) % 360
    observations = []
    for index, day in enumerate(_observation_dates(series_id)):
        wave = math.sin(2 * math.pi * (index / 252.0) + phase * math.pi / 180.0)
        value = base * (1.0 + 0.08 * wave)
        observations.append({"date": day.isoformat(), "value": f"{value:.6f}"})
    return httpx.Response(
        200,
        json={"observations": observations},
        headers={"Content-Type": "application/json"},
    )


class _SyntheticEtfProxy:
    """Stands in for the shared daily chain with a deterministic local series."""

    def read(self, symbols=ETF_SYMBOLS, *, period: str = "", periods=None):
        from app.services.macro_conditions.models import EtfObservation

        results: dict[str, tuple] = {}
        for symbol in symbols:
            phase = sum(ord(character) for character in symbol) % 360
            rows = []
            cursor = START
            index = 0
            while cursor <= END:
                if cursor.weekday() < 5:
                    wave = math.sin(2 * math.pi * (index / 252.0) + phase * math.pi / 180.0)
                    price = 100.0 * math.exp(0.05 * index / 252.0) * (1.0 + 0.09 * wave)
                    rows.append(
                        EtfObservation(
                            symbol=symbol,
                            observation_date=cursor,
                            adjusted_close=round(price, 4),
                            provider="ContainerFixture",
                        )
                    )
                    index += 1
                cursor += dt.timedelta(days=1)
            results[symbol] = tuple(rows)
        return results, {}


def _macro_config() -> object:
    from types import SimpleNamespace

    base = get_personal_config()
    return SimpleNamespace(
        access=base.access,
        features=base.features,
        ai=base.ai,
        catalyst=base.catalyst,
        breakout=base.breakout,
        public_home=base.public_home,
        macro=MacroConfig(),
        storage=base.storage,
        catalyst_sync_enabled=base.catalyst_sync_enabled,
        catalyst_manual_enabled=base.catalyst_manual_enabled,
        catalyst_scheduled_enabled=base.catalyst_scheduled_enabled,
    )


async def _run() -> dict:
    settings = Settings(FRED_API_KEY=FAKE_KEY)
    database = Path(settings.macro_conditions_db_path)
    database.parent.mkdir(parents=True, exist_ok=True)

    from app.services.macro_conditions.fred_client import FredClient
    from app.services.macro_conditions.service import (
        MacroConditionsService,
        MacroServiceConfig,
    )

    personal = _macro_config()
    repository = MacroRepository(database)
    service = MacroConditionsService(
        repository,
        config=MacroServiceConfig.from_personal_config(personal),
        fred_factory=lambda: FredClient(
            FAKE_KEY,
            transport=httpx.MockTransport(_fred_handler),
            sleep=lambda _seconds: None,
        ),
        proxy=_SyntheticEtfProxy(),
    )
    task = MacroConditionsTask(
        "container-smoke",
        settings=settings,
        personal_config=personal,
        service_factory=lambda: service,
    )
    result = await task()

    reader = MacroRepository(database, read_only=True)
    composite = reader.latest_composite()
    snapshot_date = dt.date.fromisoformat(str(composite["snapshot_date"]))
    modules = reader.modules_at(snapshot_date)
    factors = reader.factors_at(snapshot_date)
    integrity = reader.integrity_report()

    read_service = MacroConditionsService(
        MacroRepository(database, read_only=True),
        config=MacroServiceConfig.from_personal_config(personal),
    )
    current = read_service.current(key_configured=True)
    ai_block = read_service.ai_context(key_configured=True)

    return {
        "task_status": result.status,
        "published": bool(result.details.get("published")),
        "series_succeeded": int(result.details.get("series_succeeded") or 0),
        "series_failed": int(result.details.get("series_failed") or 0),
        "journal_mode": integrity["journal_mode"],
        "integrity_check": integrity["integrity_check"],
        "foreign_key_violations": integrity["foreign_key_violations"],
        "schema_version": integrity["schema_version"],
        # Reported so the gate can compare the running schema against the code's
        # own constant instead of a literal copied into ci.yml -- that copy went
        # stale the moment the version was bumped, and the gate failed on the
        # migration rather than on anything being wrong.
        "expected_schema_version": SCHEMA_VERSION,
        "registered_factors": len(FACTORS),
        "factor_rows": len(factors),
        "module_rows": len(modules),
        "scoring_version": SCORING_VERSION,
        "composite_score_present": composite.get("score") is not None,
        "valid_module_count": int(composite.get("valid_module_count") or 0),
        "regime_present": bool(composite.get("regime")),
        "history_basis": composite.get("history_basis"),
        "api_status": current["status"],
        "api_module_count": len(current["modules"]),
        "ai_block_modules": len((ai_block or {}).get("module_scores") or {}),
        "ai_block_bytes": len(
            json.dumps(ai_block or {}, ensure_ascii=False, separators=(",", ":")).encode()
        ),
        "total_modules": len(MODULES),
    }


def main() -> int:
    payload = asyncio.run(_run())
    json.dump(payload, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
