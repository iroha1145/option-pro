from __future__ import annotations

from datetime import date
from pathlib import Path

from app.services.research_eod_v1.data.contract import UNSUPPORTED, ResearchBar
from app.services.research_eod_v1.data.local_parquet import LocalParquetProvider
from app.services.research_eod_v1.data.massive_env import MassiveEnvProvider
from app.services.research_eod_v1.data.probe import run_provider_probe
from app.services.research_eod_v1.data.yahoo import DOWNLOAD_PARAMS, YahooDiagnosticProvider


def test_local_provider_reads_csv_and_does_not_invent_missing(tmp_path) -> None:
    bars = tmp_path / "daily_bars"
    bars.mkdir()
    (bars / "AAA.csv").write_text(
        "date,open,high,low,close,volume\n2024-01-02,10,11,9,10.5,1000\n2024-01-03,10.5,11,10,10.8,1100\n",
        encoding="utf-8",
    )
    provider = LocalParquetProvider(tmp_path)
    rows = provider.fetch_daily_bars("AAA", date(2024, 1, 2), date(2024, 1, 4))
    assert isinstance(rows, list) and len(rows) == 2
    assert isinstance(rows[0], ResearchBar)
    assert provider.fetch_daily_bars("BBB", date(2024, 1, 2), date(2024, 1, 4)) == []
    assert provider.load_classification_history("AAA") == UNSUPPORTED


def test_yahoo_params_are_explicit_and_offline_is_unsupported() -> None:
    assert DOWNLOAD_PARAMS["auto_adjust"] is False
    assert DOWNLOAD_PARAMS["repair"] is False
    assert DOWNLOAD_PARAMS["keepna"] is True
    assert DOWNLOAD_PARAMS["prepost"] is False
    provider = YahooDiagnosticProvider(allow_network=False)
    assert provider.fetch_daily_bars("SPY", date(2024, 1, 2), date(2024, 2, 1)) == UNSUPPORTED
    caps = provider.probe_capabilities()
    assert caps.raw_price_verified is False
    assert caps.classification_history == UNSUPPORTED


def test_massive_without_env_is_auth_required(monkeypatch) -> None:
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    provider = MassiveEnvProvider(allow_network=True)
    caps = provider.probe_capabilities()
    assert caps.probe_status == "AUTH_REQUIRED"
    assert provider.fetch_daily_bars("SPY", date(2024, 1, 2), date(2024, 2, 1)) == UNSUPPORTED


def test_offline_probe_does_not_select_a_vendor() -> None:
    report = run_provider_probe(allow_network=False, sample=("SPY",))
    assert report["secret_present_in_report"] is False
    assert report["selected_provider"] in {None, "local_parquet"}
    blob = str(report).lower()
    assert "bearer" not in blob
    assert "api_key" not in blob
    assert "apikey" not in blob
