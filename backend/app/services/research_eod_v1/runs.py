"""Signed research runs. A checkpoint cannot silently serve a different config."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence


class CheckpointSignatureError(ValueError):
    """Saved done_sessions belong to another run signature."""


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))


def run_signature(
    *,
    registry: Mapping[str, Any],
    profile: str,
    horizon: str,
    label_horizons: Sequence[int],
    feature_version: str,
    statistics_version: str,
    data_hash: str,
    universe_version: str,
    member_policy: str,
    reference_policy: str,
    start: date | str,
    end: date | str,
    available_factors: Sequence[str],
    timing_policy: str,
    registry_version: Any = None,
) -> str:
    body = {
        "available_factors": list(available_factors),
        "data_hash": data_hash,
        "end": str(end),
        "feature_version": feature_version,
        "horizon": horizon,
        "label_horizons": list(label_horizons),
        "member_policy": member_policy,
        "profile": profile,
        "reference_policy": reference_policy,
        "registry": registry,
        "registry_version": registry_version if registry_version is not None else registry.get("schema_version"),
        "start": str(start),
        "statistics_version": statistics_version,
        "timing_policy": timing_policy,
        "universe_version": universe_version,
    }
    return hashlib.sha256(canonical_json(body).encode()).hexdigest()


def scorer_cache_key(
    *,
    profile: str,
    horizon: str,
    registry_version: Any,
    security_id: str | None = None,
) -> str:
    parts = [str(profile), str(horizon), str(registry_version)]
    if security_id:
        parts.append(str(security_id))
    return "|".join(parts)


def load_signed_checkpoint(path: Path, expected_signature: str) -> dict[str, Any]:
    saved = json.loads(path.read_text(encoding="utf-8"))
    found = saved.get("signature")
    if found != expected_signature:
        raise CheckpointSignatureError(
            f"checkpoint signature mismatch: have {found}, want {expected_signature}"
        )
    return saved


def write_signed_checkpoint(path: Path, payload: Mapping[str, Any], signature: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    body = dict(payload)
    body["signature"] = signature
    tmp.write_text(canonical_json(body) + "\n", encoding="utf-8")
    tmp.replace(path)


def cache_hit_definitions() -> dict[str, str]:
    return {
        "feature_cache": (
            "Raw/cross-section factors keyed by session, security, universe, feature_version, "
            "and reference policy. Independent of profile, horizon tilt, and score weights."
        ),
        "score_cache": (
            "Scores and eligibility keyed by run_signature plus security. A weight, profile, "
            "horizon, floor, or label-horizon change must miss this layer."
        ),
        "event_cache": (
            "Deduped event groups keyed by security, family, profile, score horizon, and "
            "label horizon under trading-session adjacency. Not N_eff."
        ),
        "ic_cache": (
            "Grouped Spearman keyed by session, theme, family, profile, horizon, label "
            "horizon, and the score vector identity. Eligible-only subsets are not this cache."
        ),
        "checkpoint": (
            "Signed done keys are (session, theme, algorithm, profile, horizon). A signature "
            "mismatch rejects the file. Row uniqueness uses committed_row_key."
        ),
    }


def run_dir_name(signature: str) -> str:
    return f"run_{signature[:16]}"


def committed_row_key(
    *,
    session: str,
    theme_id: str,
    algorithm: str,
    profile: str,
    horizon: str,
    security_id: str,
    label_horizon: Any,
) -> str:
    return "|".join(
        [
            str(session),
            str(theme_id),
            str(algorithm),
            str(profile),
            str(horizon),
            str(security_id),
            str(label_horizon),
        ]
    )
