from __future__ import annotations

from datetime import date
from pathlib import Path

from app.services.research_eod_v1.data.contract import UNSUPPORTED, ResearchBar
from app.services.research_eod_v1.data.local_parquet import LocalParquetProvider
from app.services.research_eod_v1.data.massive_env import MassiveEnvProvider
from app.services.research_eod_v1.data.probe import run_provider_probe
from app.services.research_eod_v1.data.yahoo import DOWNLOAD_PARAMS, YahooDiagnosticProvider, _ohlc_ok


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
    assert rows[0].raw_open is None
    assert rows[0].raw_close is None
    assert provider.fetch_daily_bars("BBB", date(2024, 1, 2), date(2024, 1, 4)) == []
    assert provider.load_classification_history("AAA") == UNSUPPORTED


def test_local_provider_preserves_halt_and_adjustment_fields(tmp_path) -> None:
    bars = tmp_path / "daily_bars"
    bars.mkdir()
    (bars / "AAA.csv").write_text(
        "date,open,high,low,close,volume,halted,price_adjustment,volume_adjustment,vintage_status\n"
        "2024-01-02,10,11,9,10.5,1000,true,split_adjusted,unverified,PARTIAL\n",
        encoding="utf-8",
    )
    rows = LocalParquetProvider(tmp_path).fetch_daily_bars("AAA", date(2024, 1, 2), date(2024, 1, 3))
    assert rows[0].halted is True
    assert rows[0].price_adjustment == "split_adjusted"
    assert rows[0].volume_adjustment == "unverified"
    assert rows[0].vintage_status == "PARTIAL"


def test_local_provider_does_not_default_us_cs_identity(tmp_path) -> None:
    (tmp_path / "security_master.csv").write_text(
        "security_id,provider_symbol,asset_track\nAAA,AAA,stock\n",
        encoding="utf-8",
    )
    identities = LocalParquetProvider(tmp_path).load_security_master()
    assert identities[0].security_type == "UNKNOWN"
    assert identities[0].listing_country == ""
    assert identities[0].identity_confidence == "unverified"


def test_yahoo_rejects_inconsistent_ohlc_without_repair() -> None:
    assert _ohlc_ok(10.0, 11.0, 9.0, 10.5)
    assert not _ohlc_ok(10.0, 10.2, 9.0, 10.5)
    assert _ohlc_ok(10.0, None, 9.0, 10.5)


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
