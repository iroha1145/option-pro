"""Read authorized offline parquet/csv exports. Missing files are not invented."""

from __future__ import annotations

import csv
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from app.services.research_eod_v1.data.contract import (
    UNSUPPORTED,
    CorporateAction,
    DatasetMeta,
    ProviderCapabilities,
    ResearchBar,
    SecurityIdentity,
    hash_file_bytes,
)


def default_local_root() -> Path:
    env = os.environ.get("RESEARCH_EOD_LOCAL_DATA")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[5] / "research" / "option_pro_us_eod_v1" / "data" / "local"


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if hasattr(value, "date"):
        return value.date()
    text = str(value)[:10]
    if not text:
        return None
    return date.fromisoformat(text)


def _identity_field(value: Any, default: str) -> str:
    if value is None:
        return default
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return default
    return text


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes"}


def _as_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _optional_contract_field(row: Mapping[str, Any], key: str) -> str | None:
    if key not in row:
        return None
    value = row.get(key)
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return None
    return text


def _research_bar_from_row(
    row: Mapping[str, Any],
    *,
    symbol: str,
    identity: SecurityIdentity | None,
    session: date,
) -> ResearchBar:
    close = _as_float(row.get("close"))
    volume = _as_float(row.get("volume"))
    vintage = _optional_contract_field(row, "vintage_status") or "offline_export"
    return ResearchBar(
        security_id=str(row.get("security_id") or (identity.security_id if identity else symbol)),
        session_date=session,
        open=_as_float(row.get("open")),
        high=_as_float(row.get("high")),
        low=_as_float(row.get("low")),
        close=close,
        raw_open=_as_float(row["raw_open"]) if "raw_open" in row else None,
        raw_close=_as_float(row["raw_close"]) if "raw_close" in row else None,
        volume=volume,
        dollar_volume=_as_float(row.get("dollar_volume")),
        tri=_as_float(row["tri"]) if "tri" in row else None,
        volume_scope=_optional_contract_field(row, "volume_scope") or "UNKNOWN",
        price_adjustment=_optional_contract_field(row, "price_adjustment") or "unverified",
        volume_adjustment=_optional_contract_field(row, "volume_adjustment") or "unverified",
        halted=_as_bool(row.get("halted")) if "halted" in row else False,
        economic_known_at=_as_datetime(row.get("economic_known_at")),
        source_published_at=_as_datetime(row.get("source_published_at")),
        retrieved_at=_as_datetime(row.get("retrieved_at")),
        finalized_at=_as_datetime(row.get("finalized_at")),
        vintage_status=vintage,
        partial=_as_bool(row.get("partial")),
    )


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


class LocalParquetProvider:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else default_local_root()

    def probe_capabilities(self) -> ProviderCapabilities:
        bars = self.root / "daily_bars"
        combined = self.root / "daily_bars.parquet"
        present = (bars.is_dir() and any(bars.iterdir())) or combined.is_file()
        return ProviderCapabilities(
            provider="local_parquet",
            dataset_id="research-eod-local",
            dataset_version="v1",
            probe_status="ACCESS_TESTED" if self.root.exists() else "DOCUMENTED_ONLY",
            daily_bars="available" if present else "empty",
            corporate_actions="available" if (self.root / "actions").exists() else UNSUPPORTED,
            classification_history=UNSUPPORTED,
            security_master="available" if (self.root / "security_master.parquet").exists() else UNSUPPORTED,
            delisted_coverage="unknown",
            volume_session_scope="UNKNOWN",
            raw_price_verified=False,
            notes=("Local files are used only when present. Path existence is not market history.",),
            capability_level="SAMPLE_VERIFIED" if present else "ACCESS_TESTED",
        )

    def load_security_master(self) -> list[SecurityIdentity] | str:
        path = self.root / "security_master.parquet"
        if not path.is_file():
            csv_path = self.root / "security_master.csv"
            if not csv_path.is_file():
                return UNSUPPORTED
            return self._identities_from_csv(csv_path)
        return self._identities_from_parquet(path)

    def fetch_daily_bars(
        self,
        symbol: str,
        start: date,
        end: date,
        *,
        identity: SecurityIdentity | None = None,
    ) -> list[ResearchBar] | str:
        per_file = self.root / "daily_bars" / f"{symbol}.parquet"
        csv_path = self.root / "daily_bars" / f"{symbol}.csv"
        combined = self.root / "daily_bars.parquet"
        combined_csv = self.root / "daily_bars.csv"
        if per_file.is_file():
            rows = self._bars_from_parquet(per_file, symbol, identity)
        elif csv_path.is_file():
            rows = self._bars_from_csv(csv_path, symbol, identity)
        elif combined.is_file():
            rows = [row for row in self._bars_from_parquet(combined, symbol, identity) if row.security_id == (identity.security_id if identity else symbol)]
        elif combined_csv.is_file():
            rows = [row for row in self._bars_from_csv(combined_csv, symbol, identity) if row.security_id == (identity.security_id if identity else symbol)]
        else:
            return []
        return [row for row in rows if start <= row.session_date < end]

    def fetch_corporate_actions(self, symbol: str, start: date, end: date) -> list[CorporateAction] | str:
        path = self.root / "actions" / f"{symbol}.csv"
        if not path.is_file():
            return UNSUPPORTED
        out: list[CorporateAction] = []
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                session = _as_date(row.get("session_date") or row.get("date"))
                if session is None or not (start <= session < end):
                    continue
                out.append(
                    CorporateAction(
                        symbol,
                        session,
                        str(row.get("kind") or row.get("type") or ""),
                        float(row.get("value") or 0.0),
                        effective_at=_as_date(row.get("effective_at")),
                        pay_date=_as_date(row.get("pay_date")),
                        settlement_at=_as_date(row.get("settlement_at")),
                    )
                )
        return out

    def load_classification_history(self, symbol: str) -> Mapping[str, Any] | str:
        return UNSUPPORTED

    def export_snapshot(self, path: str) -> DatasetMeta | str:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        files = sorted(item for item in self.root.rglob("*") if item.is_file())
        digest = hash_file_bytes(files) if files else ""
        target.write_text("\n".join(str(item) for item in files) + "\n", encoding="utf-8")
        return DatasetMeta(
            provider="local_parquet",
            dataset_id="research-eod-local",
            dataset_version="v1",
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            request_params_redacted={"root": str(self.root)},
            content_sha256=digest,
        )

    def _identities_from_csv(self, path: Path) -> list[SecurityIdentity]:
        rows: list[SecurityIdentity] = []
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                rows.append(
                    SecurityIdentity(
                        security_id=str(row.get("security_id") or row.get("ticker")),
                        provider_symbol=str(row.get("provider_symbol") or row.get("ticker") or row.get("security_id")),
                        share_class=row.get("share_class") or None,
                        security_type=_identity_field(row.get("security_type"), "UNKNOWN"),
                        primary_mic=row.get("primary_mic") or None,
                        listing_country=_identity_field(row.get("listing_country"), ""),
                        asset_track=str(row.get("asset_track") or "stock"),
                        identity_confidence=_identity_field(row.get("identity_confidence"), "unverified"),
                    )
                )
        return rows

    def _identities_from_parquet(self, path: Path) -> list[SecurityIdentity]:
        try:
            import pandas as pd
        except ImportError:
            return UNSUPPORTED
        frame = pd.read_parquet(path)
        out: list[SecurityIdentity] = []
        for row in frame.to_dict(orient="records"):
            out.append(
                SecurityIdentity(
                    security_id=str(row.get("security_id") or row.get("ticker")),
                    provider_symbol=str(row.get("provider_symbol") or row.get("ticker")),
                    share_class=row.get("share_class"),
                    security_type=_identity_field(row.get("security_type"), "UNKNOWN"),
                    primary_mic=row.get("primary_mic"),
                    listing_country=_identity_field(row.get("listing_country"), ""),
                    asset_track=str(row.get("asset_track") or "stock"),
                    identity_confidence=_identity_field(row.get("identity_confidence"), "unverified"),
                )
            )
        return out

    def _bars_from_csv(self, path: Path, symbol: str, identity: SecurityIdentity | None) -> list[ResearchBar]:
        out: list[ResearchBar] = []
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                session = _as_date(row.get("session_date") or row.get("date"))
                if session is None:
                    continue
                out.append(_research_bar_from_row(row, symbol=symbol, identity=identity, session=session))
        return out

    def _bars_from_parquet(self, path: Path, symbol: str, identity: SecurityIdentity | None) -> list[ResearchBar]:
        try:
            import pandas as pd
        except ImportError:
            return []
        frame = pd.read_parquet(path)
        out: list[ResearchBar] = []
        for row in frame.to_dict(orient="records"):
            session = _as_date(row.get("session_date") or row.get("date"))
            if session is None:
                continue
            sid = str(row.get("security_id") or (identity.security_id if identity else symbol))
            if "security_id" in frame.columns and sid not in {symbol, identity.security_id if identity else symbol}:
                if str(row.get("security_id")) != (identity.security_id if identity else symbol):
                    continue
            out.append(_research_bar_from_row(row, symbol=symbol, identity=identity, session=session))
        return out
