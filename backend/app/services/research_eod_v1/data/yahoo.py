"""Yahoo / yfinance diagnostic adapter. Explicit download params; no silent repair."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Mapping

from app.services.research_eod_v1.data.contract import (
    UNSUPPORTED,
    CorporateAction,
    DatasetMeta,
    ProviderCapabilities,
    ResearchBar,
    SecurityIdentity,
    hash_payload,
)

LOCKED_YFINANCE = "1.5.1"
DOWNLOAD_PARAMS = {
    "interval": "1d",
    "auto_adjust": False,
    "actions": True,
    "repair": False,
    "keepna": True,
    "prepost": False,
    "threads": False,
    "progress": False,
}


class YahooDiagnosticProvider:
    def __init__(self, *, allow_network: bool = True) -> None:
        self.allow_network = allow_network
        self.failures: list[dict[str, Any]] = []

    def probe_capabilities(self) -> ProviderCapabilities:
        version = "missing"
        try:
            import yfinance as yf

            version = getattr(yf, "__version__", "unknown")
        except Exception as exc:
            return ProviderCapabilities(
                provider="yahoo_yfinance",
                dataset_id="yahoo-diagnostic",
                dataset_version=str(version),
                probe_status="FAILED",
                daily_bars=UNSUPPORTED,
                corporate_actions=UNSUPPORTED,
                classification_history=UNSUPPORTED,
                security_master=UNSUPPORTED,
                delisted_coverage="survivorship_only",
                volume_session_scope="UNKNOWN",
                raw_price_verified=False,
                notes=(f"import_failed:{type(exc).__name__}",),
                capability_level="DOCUMENTED_ONLY",
            )
        notes = [
            f"locked_target={LOCKED_YFINANCE}",
            f"imported={version}",
            "auto_adjust=False does not prove Close is historical raw trade price",
            "Adj Close is not an executable price",
            "prepost=False is not proof of regular-session volume",
            "CURRENT_UNIVERSE / SURVIVORSHIP_RISK / CLASSIFICATION_CURRENT",
        ]
        if version != LOCKED_YFINANCE:
            notes.append("VERSION_MISMATCH")
        return ProviderCapabilities(
            provider="yahoo_yfinance",
            dataset_id="yahoo-diagnostic",
            dataset_version=str(version),
            probe_status="ACCESS_TESTED" if self.allow_network else "DOCUMENTED_ONLY",
            daily_bars="available",
            corporate_actions="partial",
            classification_history=UNSUPPORTED,
            security_master=UNSUPPORTED,
            delisted_coverage="survivorship_only",
            volume_session_scope="UNKNOWN",
            raw_price_verified=False,
            notes=tuple(notes),
            capability_level="ACCESS_TESTED",
        )

    def load_security_master(self) -> list[SecurityIdentity] | str:
        return UNSUPPORTED

    def load_classification_history(self, symbol: str) -> Mapping[str, Any] | str:
        return UNSUPPORTED

    def fetch_corporate_actions(self, symbol: str, start: date, end: date) -> list[CorporateAction] | str:
        if not self.allow_network:
            return UNSUPPORTED
        try:
            import yfinance as yf
        except Exception as exc:
            self.failures.append({"symbol": symbol, "stage": "actions", "error": type(exc).__name__})
            return UNSUPPORTED
        ticker = yf.Ticker(symbol)
        out: list[CorporateAction] = []
        try:
            actions = ticker.get_actions()
        except Exception as exc:
            self.failures.append({"symbol": symbol, "stage": "actions", "error": type(exc).__name__})
            return UNSUPPORTED
        if actions is None or getattr(actions, "empty", True):
            return []
        for stamp, row in actions.iterrows():
            session = stamp.date() if hasattr(stamp, "date") else date.fromisoformat(str(stamp)[:10])
            if not (start <= session < end):
                continue
            if float(row.get("Dividends") or 0) != 0:
                out.append(CorporateAction(symbol, session, "dividend", float(row["Dividends"])))
            if float(row.get("Stock Splits") or 0) != 0:
                out.append(CorporateAction(symbol, session, "split", float(row["Stock Splits"])))
        return out

    def fetch_daily_bars(
        self,
        symbol: str,
        start: date,
        end: date,
        *,
        identity: SecurityIdentity | None = None,
    ) -> list[ResearchBar] | str:
        if not self.allow_network:
            return UNSUPPORTED
        try:
            import yfinance as yf
        except Exception as exc:
            self.failures.append({"symbol": symbol, "stage": "download", "error": type(exc).__name__})
            return []
        try:
            frame = yf.download(
                symbol,
                start=start.isoformat(),
                end=end.isoformat(),
                **DOWNLOAD_PARAMS,
            )
        except Exception as exc:
            self.failures.append({"symbol": symbol, "stage": "download", "error": type(exc).__name__, "detail": str(exc)[:200]})
            return []
        if frame is None or getattr(frame, "empty", True):
            self.failures.append({"symbol": symbol, "stage": "download", "error": "empty"})
            return []
        if getattr(frame.columns, "nlevels", 1) > 1:
            frame = frame.copy()
            frame.columns = [str(col[0]).lower() if isinstance(col, tuple) else str(col).lower() for col in frame.columns]
        else:
            frame = frame.rename(columns=str.lower)
        out: list[ResearchBar] = []
        retrieved = datetime.now(timezone.utc)
        for stamp, row in frame.iterrows():
            session = stamp.date() if hasattr(stamp, "date") else date.fromisoformat(str(stamp)[:10])
            close = _finite(row.get("close"))
            raw_close = close
            volume = _finite(row.get("volume"))
            open_ = _finite(row.get("open"))
            missing = close is None or open_ is None
            out.append(
                ResearchBar(
                    security_id=identity.security_id if identity else symbol,
                    session_date=session,
                    open=open_,
                    high=_finite(row.get("high")),
                    low=_finite(row.get("low")),
                    close=close,
                    raw_open=open_,
                    raw_close=raw_close,
                    volume=volume,
                    dollar_volume=None if close is None or volume is None else close * volume,
                    tri=_finite(row.get("adj close")) or close,
                    volume_scope="UNKNOWN",
                    price_adjustment="yahoo_unverified_raw",
                    volume_adjustment="yahoo_unverified",
                    missing=missing,
                    source_published_at=retrieved,
                    vintage_status="download_time_not_pit",
                )
            )
        return out

    def export_snapshot(self, path: str) -> DatasetMeta | str:
        return DatasetMeta(
            provider="yahoo_yfinance",
            dataset_id="yahoo-diagnostic",
            dataset_version=LOCKED_YFINANCE,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            request_params_redacted=dict(DOWNLOAD_PARAMS),
            content_sha256=hash_payload(self.failures),
        )


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number
