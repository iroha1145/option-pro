from app.services.algorithm_modes import PRODUCTION_ALGORITHM, resolve_radar_algorithm, resolve_screener_algorithm
from app.services.research_eod_v1.snapshot import compute_snapshot


def test_production_defaults_unchanged() -> None:
    screener = resolve_screener_algorithm()
    radar = resolve_radar_algorithm()
    assert screener.effective == PRODUCTION_ALGORITHM
    assert radar.effective == PRODUCTION_ALGORITHM
    assert "research" not in screener.effective
    assert "research" not in radar.effective


def test_compute_snapshot_is_pure_and_has_no_network_import() -> None:
    import inspect
    from app.services.research_eod_v1 import snapshot as snap

    source = inspect.getsource(snap)
    assert "yfinance" not in source
    assert "httpx" not in source
    assert "datetime.now" not in source
    assert "compute_snapshot" in source
    assert callable(compute_snapshot)
