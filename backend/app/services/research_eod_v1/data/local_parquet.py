"""Read authorized offline parquet/csv exports. Missing files are not invented."""

from __future__ import annotations

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
    hash_payload,
)


def default_local_root() -> Path:
    env = os.environ.get("RESEARCH_EOD_LOCAL_DATA")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[5] / "research" / "option_pro_us_eod_v1" / "data" / "local"


class LocalParquetProvider:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else default_local_root()

    def probe_capabilities(self) -> ProviderCapabilities:
        bars = self.root / "daily_bars"
        present = bars.is_dir() and any(bars.iterdir())
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
        parquet = self.root / "daily_bars" / f"{symbol}.parquet"
        csv_path = self.root / "daily_bars" / f"{symbol}.csv"
        if parquet.is_file():
            rows = self._bars_from_parquet(parquet, symbol, identity)
        elif csv_path.is_file():
            rows = self._bars_from_csv(csv_path, symbol, identity)
        else:
            return []
        return [row for row in rows if start <= row.session_date < end]

    def fetch_corporate_actions(self, symbol: str, start: date, end: date) -> list[CorporateAction] | str:
        path = self.root / "actions" / f"{symbol}.csv"
        if not path.is_file():
            return UNSUPPORTED
        out: list[CorporateAction] = []
        for line in path.read_text(encoding="utf-8").splitlines()[1:]:
            if not line.strip():
                continue
            session_s, kind, value = line.split(",")[:3]
            session = date.fromisoformat(session_s)
            if start <= session < end:
                out.append(CorporateAction(symbol, session, kind, float(value)))
        return out

    def load_classification_history(self, symbol: str) -> Mapping[str, Any] | str:
        return UNSUPPORTED

    def export_snapshot(self, path: str) -> DatasetMeta | str:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        listing = sorted(str(item) for item in self.root.rglob("*") if item.is_file())
        digest = hash_payload(listing)
        target.write_text("\n".join(listing) + "\n", encoding="utf-8")
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
        for line in path.read_text(encoding="utf-8").splitlines()[1:]:
            if not line.strip():
                continue
            parts = line.split(",")
            rows.append(
                SecurityIdentity(
                    security_id=parts[0],
                    provider_symbol=parts[1] if len(parts) > 1 else parts[0],
                    share_class=None,
                    security_type=parts[2] if len(parts) > 2 else "CS",
                    primary_mic=parts[3] if len(parts) > 3 else None,
                    listing_country=parts[4] if len(parts) > 4 else "US",
                    asset_track=parts[5] if len(parts) > 5 else "stock",
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
                    security_type=str(row.get("security_type") or "CS"),
                    primary_mic=row.get("primary_mic"),
                    listing_country=str(row.get("listing_country") or "US"),
                    asset_track=str(row.get("asset_track") or "stock"),
                )
            )
        return out

    def _bars_from_csv(self, path: Path, symbol: str, identity: SecurityIdentity | None) -> list[ResearchBar]:
        out: list[ResearchBar] = []
        for line in path.read_text(encoding="utf-8").splitlines()[1:]:
            if not line.strip():
                continue
            parts = line.split(",")
            session = date.fromisoformat(parts[0])
            close = float(parts[4]) if len(parts) > 4 and parts[4] else None
            volume = float(parts[5]) if len(parts) > 5 and parts[5] else None
            out.append(
                ResearchBar(
                    security_id=identity.security_id if identity else symbol,
                    session_date=session,
                    open=float(parts[1]) if parts[1] else None,
                    high=float(parts[2]) if parts[2] else None,
                    low=float(parts[3]) if parts[3] else None,
                    close=close,
                    raw_open=float(parts[1]) if parts[1] else None,
                    raw_close=close,
                    volume=volume,
                    dollar_volume=(None if close is None or volume is None else close * volume),
                    tri=close,
                )
            )
        return out

    def _bars_from_parquet(self, path: Path, symbol: str, identity: SecurityIdentity | None) -> list[ResearchBar]:
        try:
            import pandas as pd
        except ImportError:
            return []
        frame = pd.read_parquet(path)
        out: list[ResearchBar] = []
        for row in frame.to_dict(orient="records"):
            session = row.get("session_date") or row.get("date")
            if hasattr(session, "date"):
                session = session.date()
            elif isinstance(session, str):
                session = date.fromisoformat(session[:10])
            close = row.get("close")
            volume = row.get("volume")
            out.append(
                ResearchBar(
                    security_id=identity.security_id if identity else symbol,
                    session_date=session,
                    open=row.get("open"),
                    high=row.get("high"),
                    low=row.get("low"),
                    close=close,
                    raw_open=row.get("raw_open", row.get("open")),
                    raw_close=row.get("raw_close", close),
                    volume=volume,
                    dollar_volume=row.get("dollar_volume"),
                    tri=row.get("tri", close),
                )
            )
        return out
