"""Offline research data adapters. Production market-data vendors stay unchanged."""

from app.services.research_eod_v1.data.capture_store import (
    ImmutableCaptureStore,
    classify_capture,
    stamp_bars_for_clock,
)
from app.services.research_eod_v1.data.contract import (
    UNSUPPORTED,
    ProviderCapabilities,
    ResearchBar,
    SecurityIdentity,
)
from app.services.research_eod_v1.data.local_parquet import LocalParquetProvider
from app.services.research_eod_v1.data.yahoo import YahooDiagnosticProvider

__all__ = [
    "UNSUPPORTED",
    "ImmutableCaptureStore",
    "LocalParquetProvider",
    "ProviderCapabilities",
    "ResearchBar",
    "SecurityIdentity",
    "YahooDiagnosticProvider",
    "classify_capture",
    "stamp_bars_for_clock",
]
