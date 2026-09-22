"""Optional research watch groups. Default off. No scoring and no live prices.

#185 shadow variants are a different parameter set from the research gates:
baseline uses ATR policy ``legacy``; track_atr uses ``track_liquid_v1``;
entry_state only relaxes a public EXTENDED flag; raw_momentum adds windowed
branches. None of those names is G1_stock_reference, G2_extension_discovery,
or G3_risk_discovery.

This module classifies an already frozen full-list payload. Ordinary strength
GETs do not call it unless EOD_RESEARCH_WATCH_GROUPS is explicitly enabled and
a precomputed sidecar is present. It does not replace the main board, and it
does not rescore when the sidecar is missing or is for another view.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.services.algorithm_modes import EOD_DEFAULT_TIMEFRAME, EOD_LIMITED_TIMEFRAMES
from app.services.research_eod_v1.constants import HORIZONS, PROFILES

logger = logging.getLogger("optix.eod.watch_groups")

G1_STOCK_REFERENCE = "G1_stock_reference"
G2_EXTENSION_DISCOVERY = "G2_extension_discovery"
G3_RISK_DISCOVERY = "G3_risk_discovery"
B0_CURRENT = "B0_current"
GATE_VARIANTS = (G1_STOCK_REFERENCE, G2_EXTENSION_DISCOVERY, G3_RISK_DISCOVERY)
LAYER_KEYS = ("discovery", "technical_entry", "qualified_entry")
UNVERIFIED_REASONS = frozenset({"DOLLAR_LIQUIDITY_UNVERIFIED", "VOLUME_SESSION_UNVERIFIED"})
TECHNICAL = "technical_candidates"
EXTENSION = "extension_watch"
HIGH_VOL = "high_volatility_watch"
CONFIRMED_TRACKS = frozenset({"stock", "etf"})
PROTOCOL_V1 = "research_watch_layers_v1"
ALLOWED_PROTOCOLS = frozenset({PROTOCOL_V1})
ALLOWED_SOURCES = frozenset({
    "synthetic_watch_layers_v1",
    "published_watch_layers_v1",
})
WATCH_DISPLAY_LIMIT = 20
SHADOW_VARIANTS = {
    "baseline": {"atr_policy": "legacy", "research_gate": None},
    "track_atr": {"atr_policy": "track_liquid_v1", "research_gate": None},
    "entry_state": {"atr_policy": None, "research_gate": None},
    "raw_momentum": {"atr_policy": None, "research_gate": None},
}
QUALIFICATION_NOTE = "观察分组不进入主榜，不授予严格资格，也不表示可以买入。"
PAGE_FILTER_NOTE = "已按当前行业与最低价筛选。"
SECTOR_FILTER_NOTE = "已按当前行业筛选。"
PRICE_FILTER_NOTE = "已按当前最低价筛选。"
GLOBAL_REFERENCE_NOTE = "全局参考，未按当前行业或最低价筛选。"
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,15}$")
_TRACK_KEYS = ("stock_or_etf_track", "asset_track")


class WatchInputError(ValueError):
    """The sidecar is present but is not a usable precomputed watch list."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _enabled() -> bool:
    return os.environ.get("EOD_RESEARCH_WATCH_GROUPS", "").strip().lower() in {"1", "true", "yes"}


def _omit(reason: str) -> None:
    logger.warning("research watch groups omitted reason=%s", reason)


def stock_roster_digest(ids: Sequence[str]) -> str:
    """Hash the roster id list exactly as it appears in the document."""

    return hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()


def manifest_path_for(path: Path) -> Path:
    return path.with_name(f"{path.name}.manifest.json")


def normalized_watch_horizon(timeframe: Any) -> str | None:
    """Same rule as ``resolve_screener_algorithm``: omitted or ``all`` is mid."""

    if timeframe is None or timeframe == "" or timeframe == "all":
        return EOD_DEFAULT_TIMEFRAME
    text = str(timeframe).strip()
    if text == "all":
        return EOD_DEFAULT_TIMEFRAME
    if text in EOD_LIMITED_TIMEFRAMES:
        return text
    return None


def _session_date(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    text = value[:10]
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None
    return text


def _finite_score(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def _validate_row(row: Any, seen: set[str]) -> str:
    if not isinstance(row, Mapping):
        raise WatchInputError("row_type")
    security_id = row.get("security_id")
    if not isinstance(security_id, str) or _ID_RE.fullmatch(security_id) is None:
        raise WatchInputError("security_id_invalid")
    if security_id in seen:
        raise WatchInputError("duplicate_security_id")
    seen.add(security_id)
    if not _finite_score(row.get("score")):
        raise WatchInputError("score_invalid")
    reasons = row.get("rejection_reasons")
    if not isinstance(reasons, list) or any(not isinstance(item, str) for item in reasons):
        raise WatchInputError("reasons_type")
    labels: list[str] = []
    for key in _TRACK_KEYS:
        if key not in row or row.get(key) is None:
            continue
        value = row.get(key)
        if not isinstance(value, str):
            raise WatchInputError("asset_track_type")
        labels.append(value if value in CONFIRMED_TRACKS else "unconfirmed")
    if len(set(labels)) > 1:
        raise WatchInputError("asset_track_conflict")
    return security_id


def _validate_variant(block: Any) -> None:
    if not isinstance(block, Mapping):
        raise WatchInputError("variant_type")
    for key in LAYER_KEYS:
        rows = block.get(key)
        if not isinstance(rows, list):
            raise WatchInputError("list_type")
        seen: set[str] = set()
        for row in rows:
            _validate_row(row, seen)


def _ids(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    return [str(row.get("security_id")) for row in rows]


def _roster_ids(document: Mapping[str, Any]) -> frozenset[str]:
    if "stock_roster" not in document or document.get("stock_roster") is None:
        return frozenset()
    roster = document.get("stock_roster")
    if not isinstance(roster, Mapping):
        raise WatchInputError("roster_type")
    ids = roster.get("ids")
    digest = roster.get("sha256")
    if not isinstance(ids, list) or not isinstance(digest, str):
        raise WatchInputError("roster_type")
    if any(not isinstance(item, str) or _ID_RE.fullmatch(item) is None for item in ids):
        raise WatchInputError("roster_id")
    if len(ids) != len(set(ids)):
        raise WatchInputError("roster_duplicate")
    expected = stock_roster_digest(ids)
    if len(digest) != len(expected) or not hmac.compare_digest(digest.lower(), expected):
        raise WatchInputError("roster_hash")
    return frozenset(ids)


def _validate_document(document: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(document, Mapping):
        raise WatchInputError("document_type")
    identity = document.get("identity")
    layers = document.get("layers")
    if not isinstance(identity, Mapping):
        raise WatchInputError("identity_type")
    if not isinstance(layers, Mapping):
        raise WatchInputError("layers_type")
    session_date = _session_date(identity.get("session_date"))
    if session_date is None or identity.get("session_date") != session_date:
        raise WatchInputError("identity_session")
    profile = identity.get("profile")
    horizon = identity.get("horizon")
    protocol = identity.get("protocol")
    source = identity.get("source")
    if profile not in PROFILES:
        raise WatchInputError("identity_profile")
    if horizon not in HORIZONS:
        raise WatchInputError("identity_horizon")
    if protocol not in ALLOWED_PROTOCOLS:
        raise WatchInputError("protocol_rejected")
    if source not in ALLOWED_SOURCES:
        raise WatchInputError("source_rejected")
    for name in GATE_VARIANTS:
        if name not in layers:
            raise WatchInputError("variant_missing")
        _validate_variant(layers[name])
    if B0_CURRENT in layers:
        _validate_variant(layers[B0_CURRENT])
    for extra_name, extra_block in layers.items():
        if extra_name in {*GATE_VARIANTS, B0_CURRENT}:
            continue
        if not isinstance(extra_block, Mapping):
            raise WatchInputError("variant_type")
    g1 = layers[G1_STOCK_REFERENCE]
    g1_technical = _ids(g1["technical_entry"])
    g1_qualified = _ids(g1["qualified_entry"])
    for name in (G2_EXTENSION_DISCOVERY, G3_RISK_DISCOVERY):
        block = layers[name]
        if _ids(block["technical_entry"]) != g1_technical:
            raise WatchInputError("technical_entry_diverges")
        if _ids(block["qualified_entry"]) != g1_qualified:
            raise WatchInputError("qualified_entry_diverges")
    return {
        "session_date": session_date,
        "profile": str(profile),
        "horizon": str(horizon),
        "protocol": str(protocol),
        "source": str(source),
        "roster_ids": _roster_ids(document),
        "layers": layers,
    }


def _explicit_kind(row: Mapping[str, Any], roster_ids: frozenset[str]) -> str:
    labels: list[str] = []
    for key in _TRACK_KEYS:
        if key not in row or row.get(key) is None:
            continue
        value = row.get(key)
        labels.append(value if value in CONFIRMED_TRACKS else "unconfirmed")
    if len(set(labels)) > 1:
        raise WatchInputError("asset_track_conflict")
    if labels:
        return labels[0]
    if str(row.get("security_id")) in roster_ids:
        return "stock"
    return "unconfirmed"


def _reasons(row: Mapping[str, Any]) -> list[str]:
    return [item for item in row.get("rejection_reasons") or [] if isinstance(item, str)]


def _stock_row(
    row: Mapping[str, Any],
    *,
    qualified_ids: set[str],
    source_date: str,
) -> dict[str, Any]:
    security_id = str(row.get("security_id"))
    reasons = _reasons(row)
    unverified = any(reason in UNVERIFIED_REASONS for reason in reasons)
    copied = {
        "security_id": security_id,
        "score": row.get("score"),
        "algorithm_id": row.get("algorithm_id"),
        "sector_context": row.get("sector_context"),
        "rejection_reasons": reasons,
        "qualified": security_id in qualified_ids and not unverified,
        "tradable": False,
        "source_date": source_date,
    }
    for key in ("sector_id", "price", "price_unknown"):
        if key in row:
            copied[key] = row.get(key)
    return copied


def _unchanged(row: Mapping[str, Any]) -> dict[str, Any]:
    copied = dict(row)
    if "rejection_reasons" in copied:
        copied["rejection_reasons"] = list(copied.get("rejection_reasons") or [])
    return copied


def classify_watch_groups(payload: Mapping[str, Any], *, limit: int | None = None) -> dict[str, Any]:
    """Classify full source lists, then truncate. Does not mutate the input."""

    validated = _validate_document(payload)
    layers = validated["layers"]
    roster_ids = validated["roster_ids"]
    source_date = validated["session_date"]
    qualified_ids = set(_ids(layers[G1_STOCK_REFERENCE]["qualified_entry"]))
    g1_technical = list(layers[G1_STOCK_REFERENCE]["technical_entry"])
    g2_discovery = list(layers[G2_EXTENSION_DISCOVERY]["discovery"])
    g3_discovery = list(layers[G3_RISK_DISCOVERY]["discovery"])
    g1_ids = set(_ids(g1_technical))
    g2_ids = set(_ids(g2_discovery))

    def kind(row: Mapping[str, Any]) -> str:
        return _explicit_kind(row, roster_ids)

    technical_rows = [row for row in g1_technical if kind(row) == "stock"]
    extension_rows = [row for row in g2_discovery if kind(row) == "stock" and row.get("security_id") not in g1_ids]
    placed = set(g1_ids)
    placed.update(row.get("security_id") for row in extension_rows)
    high_rows = [
        row
        for row in g3_discovery
        if kind(row) == "stock" and row.get("security_id") not in g2_ids and row.get("security_id") not in placed
    ]
    etf_rows: list[Mapping[str, Any]] = []
    seen_etf: set[Any] = set()
    for source in (g1_technical, g2_discovery, g3_discovery):
        for row in source:
            if kind(row) != "etf" or row.get("security_id") in seen_etf:
                continue
            seen_etf.add(row.get("security_id"))
            etf_rows.append(row)
    grouped = {
        TECHNICAL: technical_rows,
        EXTENSION: extension_rows,
        HIGH_VOL: high_rows,
    }
    selected = {
        name: [_stock_row(row, qualified_ids=qualified_ids, source_date=source_date) for row in rows]
        for name, rows in grouped.items()
    }
    totals = {name: len(rows) for name, rows in selected.items()}
    if limit is not None:
        for name in (TECHNICAL, EXTENSION, HIGH_VOL):
            selected[name] = selected[name][:limit]
    return {
        "session_date": source_date,
        "profile": validated["profile"],
        "horizon": validated["horizon"],
        "protocol": validated["protocol"],
        "source": validated["source"],
        TECHNICAL: selected[TECHNICAL],
        EXTENSION: selected[EXTENSION],
        HIGH_VOL: selected[HIGH_VOL],
        "totals": totals,
        "etf_unchanged": [_unchanged(row) for row in etf_rows],
    }


def _encode(payload: Mapping[str, Any]) -> bytes:
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False, sort_keys=True)
    return (text + "\n").encode("utf-8")


def _atomic_write_bytes(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def publish_watch_sidecar(document: Mapping[str, Any], path: Path) -> dict[str, Any]:
    """Atomically publish one view. A partial file is not a valid snapshot."""

    validated = _validate_document(document)
    encoded = _encode(document)
    digest = hashlib.sha256(encoded).hexdigest()
    manifest = {
        "sha256": digest,
        "protocol": validated["protocol"],
        "source": validated["source"],
        "profile": validated["profile"],
        "horizon": validated["horizon"],
        "session_date": validated["session_date"],
    }
    _atomic_write_bytes(path, encoded)
    try:
        _atomic_write_bytes(manifest_path_for(path), _encode(manifest))
    except Exception:
        _omit("manifest_publish_failed")
        raise
    return manifest


def _load_verified(path: Path) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str]:
    """Hash and parse one sidecar read. The manifest must describe those bytes."""

    try:
        raw = path.read_bytes()
    except OSError:
        return None, None, "unreadable"
    digest = hashlib.sha256(raw).hexdigest()
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None, None, "invalid_json"
    if not isinstance(parsed, dict):
        return None, None, "document_type"
    try:
        manifest_raw = manifest_path_for(path).read_bytes()
    except OSError:
        return None, None, "manifest_missing"
    try:
        manifest = json.loads(manifest_raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None, None, "manifest_invalid"
    if not isinstance(manifest, dict):
        return None, None, "manifest_invalid"
    claimed = manifest.get("sha256")
    if not isinstance(claimed, str) or len(claimed) != len(digest) or not hmac.compare_digest(claimed.lower(), digest):
        return None, None, "manifest_mismatch"
    return parsed, manifest, "ok"


def _session_of(payload: Mapping[str, Any]) -> str | None:
    for key in ("served_session", "score_data_through"):
        found = _session_date(payload.get(key))
        if found is not None:
            return found
    return None


def _mismatch_reason(payload: Mapping[str, Any], grouped: Mapping[str, Any]) -> str | None:
    served = _session_of(payload)
    if served is None:
        return "served_date_missing"
    if served != grouped["session_date"]:
        return "session_mismatch"
    params = payload.get("params")
    if not isinstance(params, Mapping):
        return "snapshot_params_missing"
    if params.get("profile") != grouped["profile"]:
        return "profile_mismatch"
    if "timeframe" not in params:
        horizon = EOD_DEFAULT_TIMEFRAME
    else:
        horizon = normalized_watch_horizon(params.get("timeframe"))
    if horizon != grouped["horizon"]:
        return "horizon_mismatch"
    return None


def _page_filters(payload: Mapping[str, Any]) -> tuple[str | None, float]:
    params = payload.get("params")
    if not isinstance(params, Mapping):
        return None, 0.0
    sector = params.get("sector_id")
    if sector == "" or not isinstance(sector, str):
        sector = None
    raw_price = params.get("min_price", 0)
    if isinstance(raw_price, bool) or not isinstance(raw_price, (int, float)) or not math.isfinite(float(raw_price)):
        return sector, 0.0
    return sector, float(raw_price)


def _filterable(row: Mapping[str, Any], sector: str | None, min_price: float) -> bool:
    if sector is not None and not isinstance(row.get("sector_id"), str):
        return False
    if min_price > 0:
        if row.get("price_unknown") is True:
            return False
        price = row.get("price")
        if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(float(price)):
            return False
    return True


def _keeps_row(row: Mapping[str, Any], sector: str | None, min_price: float) -> bool:
    if sector is not None and row.get("sector_id") != sector:
        return False
    if min_price > 0 and (row.get("price_unknown") or float(row.get("price") or 0) < min_price):
        return False
    return True


def _filter_note(sector: str | None, min_price: float) -> str:
    if sector is not None and min_price > 0:
        return PAGE_FILTER_NOTE
    if sector is not None:
        return SECTOR_FILTER_NOTE
    return PRICE_FILTER_NOTE


def _public_view(payload: Mapping[str, Any], grouped: Mapping[str, Any]) -> dict[str, Any]:
    sector, min_price = _page_filters(payload)
    selected = {
        EXTENSION: list(grouped[EXTENSION]),
        HIGH_VOL: list(grouped[HIGH_VOL]),
    }
    active = sector is not None or min_price > 0
    if not active:
        scope = "current_view"
        note = ""
    elif all(_filterable(row, sector, min_price) for rows in selected.values() for row in rows):
        for name in selected:
            selected[name] = [row for row in selected[name] if _keeps_row(row, sector, min_price)]
        scope = "page_filters"
        note = _filter_note(sector, min_price)
    else:
        scope = "global_reference"
        note = GLOBAL_REFERENCE_NOTE
    limit = WATCH_DISPLAY_LIMIT
    return {
        "enabled": True,
        "displaces_main_board": False,
        "high_volatility_collapsed": True,
        "qualification_note": QUALIFICATION_NOTE,
        "extension_watch": selected[EXTENSION][:limit],
        "extension_total": len(selected[EXTENSION]),
        "high_volatility_watch": selected[HIGH_VOL][:limit],
        "high_volatility_total": len(selected[HIGH_VOL]),
        "display_limit": limit,
        "source_date": grouped["session_date"],
        "profile": grouped["profile"],
        "horizon": grouped["horizon"],
        "protocol": grouped["protocol"],
        "source": grouped["source"],
        "filter_scope": scope,
        "filter_scope_note": note,
        "shadow_variants_are_research_gates": False,
    }


def _manifest_agrees(manifest: Mapping[str, Any], grouped: Mapping[str, Any]) -> bool:
    return all(manifest.get(key) == grouped[key] for key in ("profile", "horizon", "session_date", "protocol", "source"))


def maybe_attach_watch_groups(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the snapshot unchanged unless one matching precomputed view exists."""

    if not isinstance(payload, dict) or not _enabled():
        return payload
    raw_path = os.environ.get("EOD_RESEARCH_WATCH_LAYERS", "").strip()
    if not raw_path:
        return payload
    sidecar, manifest, reason = _load_verified(Path(raw_path))
    if sidecar is None or manifest is None:
        _omit(reason)
        return payload
    try:
        grouped = classify_watch_groups(sidecar)
    except WatchInputError as exc:
        _omit(exc.reason)
        return payload
    if not _manifest_agrees(manifest, grouped):
        _omit("manifest_identity")
        return payload
    mismatch = _mismatch_reason(payload, grouped)
    if mismatch is not None:
        _omit(mismatch)
        return payload
    attached = dict(payload)
    attached["research_watch_groups"] = _public_view(payload, grouped)
    return attached
