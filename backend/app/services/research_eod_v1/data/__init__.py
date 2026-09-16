"""Offline research data adapters. Production market-data vendors stay unchanged."""

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
    "LocalParquetProvider",
    "ProviderCapabilities",
    "ResearchBar",
    "SecurityIdentity",
    "YahooDiagnosticProvider",
]
