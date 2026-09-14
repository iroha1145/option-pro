"""Run identity for research replays. Dates or tickers are not identity."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from app.services.research.protocol import (
    FEATURE_VERSION,
    FROZEN_PROTOCOL,
    NORMALIZATION_VERSION,
    RESEARCH_PROTOCOL_VERSION,
    SCORE_VERSION,
    protocol_hash,
)


IDENTITY_SCHEMA_VERSION = "research-run-identity-v1"

MATERIAL_KEYS = (
    "identity_schema_version",
    "command",
    "git_commit",
    "score_version",
    "feature_version",
    "normalization_version",
    "protocol_version",
    "protocol_hash",
    "dataset_id",
    "dataset_content_sha256",
    "universe",
    "split",
    "dates_sha256",
    "step",
    "limit",
    "timeframe",
    "profile",
    "top",
    "min_price",
    "min_avg_dollar_volume",
    "allow_sealed",
    "cost_bps",
    "hold_days",
    "parameters",
)


class RunIdentityError(ValueError):
    """Resume was asked to reuse an incompatible or missing checkpoint."""


def current_git_commit(root: str | Path | None = None) -> str | None:
    cwd = Path(root) if root is not None else Path(__file__).resolve().parents[4]
    try:
        raw = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    text = raw.decode("utf-8").strip()
    return text or None


def dates_sha256(dates: Iterable[Any]) -> str:
    encoded = json.dumps(
        [str(item) for item in dates],
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def canonical_identity(payload: Mapping[str, Any]) -> dict[str, Any]:
    identity = {key: payload.get(key) for key in MATERIAL_KEYS}
    identity["identity_schema_version"] = IDENTITY_SCHEMA_VERSION
    return identity


def identity_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        canonical_identity(payload),
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_run_identity(
    *,
    command: str,
    dataset: Any,
    split: str,
    dates: Sequence[Any],
    step: int = 1,
    limit: int = 0,
    allow_sealed: bool = False,
    parameters: Mapping[str, Any] | None = None,
    timeframe: str | None = None,
    profile: str | None = None,
    top: int | None = None,
    min_price: float | None = None,
    min_avg_dollar_volume: float | None = None,
    cost_bps: float | None = None,
    hold_days: int | None = None,
    universe: str | None = None,
    git_commit: str | None = None,
) -> dict[str, Any]:
    manifest = getattr(dataset, "manifest", {}) or {}
    params = dict(parameters or {})
    identity = canonical_identity(
        {
            "identity_schema_version": IDENTITY_SCHEMA_VERSION,
            "command": command,
            "git_commit": git_commit if git_commit is not None else current_git_commit(),
            "score_version": SCORE_VERSION,
            "feature_version": FEATURE_VERSION,
            "normalization_version": NORMALIZATION_VERSION,
            "protocol_version": RESEARCH_PROTOCOL_VERSION,
            "protocol_hash": protocol_hash(),
            "dataset_id": manifest.get("dataset_id"),
            "dataset_content_sha256": manifest.get("content_sha256"),
            "universe": universe or FROZEN_PROTOCOL.get("universe"),
            "split": split,
            "dates_sha256": dates_sha256(dates),
            "step": int(step),
            "limit": int(limit or 0),
            "timeframe": timeframe if timeframe is not None else params.get("timeframe"),
            "profile": profile if profile is not None else params.get("profile"),
            "top": top if top is not None else params.get("top"),
            "min_price": min_price if min_price is not None else params.get("min_price"),
            "min_avg_dollar_volume": (
                min_avg_dollar_volume
                if min_avg_dollar_volume is not None
                else params.get("min_avg_dollar_volume")
            ),
            "allow_sealed": bool(allow_sealed),
            "cost_bps": cost_bps,
            "hold_days": hold_days,
            "parameters": params,
        }
    )
    identity["identity_hash"] = identity_hash(identity)
    return identity


def identity_diffs(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> dict[str, Any]:
    diffs: dict[str, Any] = {}
    for key in MATERIAL_KEYS:
        if expected.get(key) != actual.get(key):
            diffs[key] = {"expected": expected.get(key), "actual": actual.get(key)}
    return diffs


def assert_compatible_identity(stored: Mapping[str, Any] | None, expected: Mapping[str, Any]) -> None:
    if stored is None:
        raise RunIdentityError(
            "refusing to resume a checkpoint that has no run manifest; start a new run id"
        )
    if stored.get("identity_schema_version") != IDENTITY_SCHEMA_VERSION:
        raise RunIdentityError(
            "refusing to resume a checkpoint with an unknown or missing identity schema"
        )
    diffs = identity_diffs(canonical_identity(expected), canonical_identity(stored))
    if diffs:
        raise RunIdentityError(
            "run identity changed; start a new run id instead of --resume: "
            + json.dumps(diffs, ensure_ascii=True, default=str)
        )
