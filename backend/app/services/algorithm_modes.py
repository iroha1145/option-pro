"""Stable production algorithm IDs, versions, and resolution rules.

Screener ranking and radar sort are independent. The system default is the
original production algorithm. An explicit user or request choice is never
overwritten by a later admin default.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping


SCREENER_FAMILY = "screener"
RADAR_FAMILY = "radar"

PRODUCTION_ALGORITHM = "production"
FOLLOW_DEFAULT = "follow_default"

A0_ALGORITHM = "a0_mid_long"
A0_VERSION = "a0-mid-long-v1"
A0_SCORE_BASIS = "0.5 * score_mid + 0.5 * score_long"
A0_SUPPORTED_TIMEFRAME = "all"
A0_SUPPORTED_PROFILE = "balanced"

T1_ALGORITHM = "t1_daily_priority"
T1_VERSION = "t1-daily-priority-v1"
T1_SCORE_BASIS = "production_order + t1_daily_conditions_boost"

SCREENER_ALGORITHMS = (PRODUCTION_ALGORITHM, A0_ALGORITHM)
RADAR_ALGORITHMS = (PRODUCTION_ALGORITHM, T1_ALGORITHM)
USER_CHOICES = (FOLLOW_DEFAULT, *SCREENER_ALGORITHMS)
RADAR_USER_CHOICES = (FOLLOW_DEFAULT, *RADAR_ALGORITHMS)

SCREENER_ALIASES = {
    "": FOLLOW_DEFAULT,
    "default": FOLLOW_DEFAULT,
    "follow": FOLLOW_DEFAULT,
    "original": PRODUCTION_ALGORITHM,
    "legacy": PRODUCTION_ALGORITHM,
    "strength-v3": PRODUCTION_ALGORITHM,
    "a0": A0_ALGORITHM,
    "mid_long": A0_ALGORITHM,
    "mid-long": A0_ALGORITHM,
}

RADAR_ALIASES = {
    "": FOLLOW_DEFAULT,
    "default": FOLLOW_DEFAULT,
    "follow": FOLLOW_DEFAULT,
    "original": PRODUCTION_ALGORITHM,
    "legacy": PRODUCTION_ALGORITHM,
    "breakout-score-v1": PRODUCTION_ALGORITHM,
    "t1": T1_ALGORITHM,
    "t1_daily_strong_proxy": T1_ALGORITHM,
}

INCOMPATIBLE_VIEW = "incompatible_view"
UNKNOWN_ALGORITHM = "unknown_algorithm"
A0_UNAVAILABLE = "a0_scores_unavailable"


class UnknownAlgorithmError(ValueError):
    """The request named an algorithm this deployment does not accept."""

    def __init__(self, family: str, value: str) -> None:
        super().__init__(f"unknown {family} algorithm: {value}")
        self.family = family
        self.value = value
        self.code = UNKNOWN_ALGORITHM


class ConflictingAlgorithmError(ValueError):
    """An explicit algorithm cannot be applied to the requested view."""

    def __init__(self, algorithm: str, reason: str) -> None:
        super().__init__(reason)
        self.algorithm = algorithm
        self.reason = reason
        self.code = "algorithm_view_conflict"


@dataclass(frozen=True)
class AlgorithmResolution:
    family: Literal["screener", "radar"]
    requested: str | None
    user_choice: str | None
    admin_default: str
    effective: str
    version: str
    score_basis: str
    source: str
    fallback_reason: str | None = None

    def as_public_dict(self) -> dict[str, Any]:
        return {
            "requested_algorithm": self.requested,
            "user_choice": self.user_choice,
            "admin_default_algorithm": self.admin_default,
            "effective_algorithm": self.effective,
            "algorithm_version": self.version,
            "score_basis": self.score_basis,
            "resolution_source": self.source,
            "fallback_reason": self.fallback_reason,
        }


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def canonicalize_screener_algorithm(value: Any, *, allow_follow: bool = False) -> str | None:
    raw = _clean(value)
    if raw is None:
        return None
    key = raw.lower().replace(" ", "_")
    mapped = SCREENER_ALIASES.get(key, key)
    allowed = USER_CHOICES if allow_follow else SCREENER_ALGORITHMS
    if mapped not in allowed:
        raise UnknownAlgorithmError(SCREENER_FAMILY, raw)
    return mapped


def canonicalize_radar_algorithm(value: Any, *, allow_follow: bool = False) -> str | None:
    raw = _clean(value)
    if raw is None:
        return None
    key = raw.lower().replace(" ", "_")
    mapped = RADAR_ALIASES.get(key, key)
    allowed = RADAR_USER_CHOICES if allow_follow else RADAR_ALGORITHMS
    if mapped not in allowed:
        raise UnknownAlgorithmError(RADAR_FAMILY, raw)
    return mapped


def screener_version(algorithm: str) -> str:
    if algorithm == A0_ALGORITHM:
        return A0_VERSION
    return "strength-v3"


def radar_version(algorithm: str) -> str:
    if algorithm == T1_ALGORITHM:
        return T1_VERSION
    return "breakout-score-v1"


def screener_score_basis(algorithm: str) -> str:
    if algorithm == A0_ALGORITHM:
        return A0_SCORE_BASIS
    return "ranking_score"


def radar_score_basis(algorithm: str) -> str:
    if algorithm == T1_ALGORITHM:
        return T1_SCORE_BASIS
    return "event_at_desc,alert_priority_score_desc,event_id_desc"


def a0_view_supported(timeframe: Any, profile: Any) -> bool:
    return (
        str(timeframe or "").strip() == A0_SUPPORTED_TIMEFRAME
        and str(profile or "").strip() == A0_SUPPORTED_PROFILE
    )


def resolve_screener_algorithm(
    *,
    requested: Any = None,
    user_choice: Any = None,
    admin_default: Any = None,
    timeframe: Any = "all",
    profile: Any = "balanced",
    explicit_request: bool = False,
) -> AlgorithmResolution:
    requested_id = canonicalize_screener_algorithm(requested, allow_follow=True)
    user_id = canonicalize_screener_algorithm(user_choice, allow_follow=True)
    admin_id = canonicalize_screener_algorithm(admin_default) or PRODUCTION_ALGORITHM

    if requested_id == FOLLOW_DEFAULT:
        requested_id = None
        explicit_request = False
    if user_id == FOLLOW_DEFAULT:
        user_id = None

    if requested_id is not None:
        effective = requested_id
        source = "request"
    elif user_id is not None:
        effective = user_id
        source = "user_preference"
    else:
        effective = admin_id
        source = "admin_default" if admin_default not in (None, "") else "system_default"

    fallback_reason = None
    if effective == A0_ALGORITHM and not a0_view_supported(timeframe, profile):
        if explicit_request or source == "request":
            raise ConflictingAlgorithmError(
                A0_ALGORITHM,
                "A0 mid/long ranking only supports timeframe=all and profile=balanced",
            )
        fallback_reason = INCOMPATIBLE_VIEW
        effective = PRODUCTION_ALGORITHM
        source = f"{source}+fallback"

    return AlgorithmResolution(
        family=SCREENER_FAMILY,
        requested=_clean(requested),
        user_choice=user_id,
        admin_default=admin_id,
        effective=effective,
        version=screener_version(effective),
        score_basis=screener_score_basis(effective),
        source=source,
        fallback_reason=fallback_reason,
    )


def resolve_radar_algorithm(
    *,
    requested: Any = None,
    user_choice: Any = None,
    admin_default: Any = None,
) -> AlgorithmResolution:
    requested_id = canonicalize_radar_algorithm(requested, allow_follow=True)
    user_id = canonicalize_radar_algorithm(user_choice, allow_follow=True)
    admin_id = canonicalize_radar_algorithm(admin_default) or PRODUCTION_ALGORITHM

    if requested_id == FOLLOW_DEFAULT:
        requested_id = None
    if user_id == FOLLOW_DEFAULT:
        user_id = None

    if requested_id is not None:
        effective = requested_id
        source = "request"
    elif user_id is not None:
        effective = user_id
        source = "user_preference"
    else:
        effective = admin_id
        source = "admin_default" if admin_default not in (None, "") else "system_default"

    return AlgorithmResolution(
        family=RADAR_FAMILY,
        requested=_clean(requested),
        user_choice=user_id,
        admin_default=admin_id,
        effective=effective,
        version=radar_version(effective),
        score_basis=radar_score_basis(effective),
        source=source,
        fallback_reason=None,
    )


def admin_algorithm_defaults(settings: Any = None) -> dict[str, str]:
    algorithms = None
    if settings is not None:
        algorithms = getattr(settings, "algorithms", None)
        if algorithms is None and isinstance(settings, Mapping):
            algorithms = settings.get("algorithms")
    screener_raw = None
    radar_raw = None
    if algorithms is not None:
        screener_raw = getattr(algorithms, "screener_ranking_algorithm", None)
        radar_raw = getattr(algorithms, "radar_sort_algorithm", None)
        if isinstance(algorithms, Mapping):
            screener_raw = algorithms.get("screener_ranking_algorithm", screener_raw)
            radar_raw = algorithms.get("radar_sort_algorithm", radar_raw)
    try:
        screener = canonicalize_screener_algorithm(screener_raw) or PRODUCTION_ALGORITHM
    except UnknownAlgorithmError:
        screener = PRODUCTION_ALGORITHM
    try:
        radar = canonicalize_radar_algorithm(radar_raw) or PRODUCTION_ALGORITHM
    except UnknownAlgorithmError:
        radar = PRODUCTION_ALGORITHM
    return {
        "screener_ranking_algorithm": screener,
        "radar_sort_algorithm": radar,
    }
