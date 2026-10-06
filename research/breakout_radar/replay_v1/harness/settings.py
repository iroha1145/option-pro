"""Production settings for the replay, the settings-hash proof and the variant table."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.services.breakouts.config import BreakoutSettings
from app.services.breakouts.worker import _stable_hash

# config_hash of every production scan in the 2026-09-08..25 export.
PRODUCTION_CONFIG_HASH = "cc09185b00548d9ddafd54086c9944619dcb44dc56d34b3b821c278f6d7a46e5"

# BreakoutSettings.model_fields at origin/main 35ab1395, the code production ran.
# Fields added since (allow_otc) are excluded from the proof hash and recorded apart.
PRODUCTION_FIELDS_35ab1395 = (
    "enabled", "discovery_provider", "provider_timeout_seconds",
    "provider_connect_timeout_seconds", "provider_read_timeout_seconds",
    "provider_retry_attempts", "provider_retry_after_cap_seconds",
    "provider_cache_ttl_seconds", "provider_stale_ttl_seconds",
    "provider_failure_threshold", "provider_circuit_open_seconds",
    "provider_max_response_bytes", "provider_result_limit", "provider_max_concurrency",
    "provider_min_market_cap", "allow_etf", "daily_enrich_limit", "intraday_enrich_limit",
    "expired_due_limit", "scan_interval_premarket_seconds", "scan_interval_regular_seconds",
    "scan_interval_closed_seconds", "worker_lease_ttl_seconds", "worker_health_stale_seconds",
    "raw_payload_retention_hours", "scan_retention_days", "retention_batch_size", "min_price",
    "min_avg_dollar_volume", "regular_min_change_pct", "regular_min_relative_volume",
    "regular_min_dollar_volume", "premarket_min_change_pct", "premarket_min_dollar_volume",
    "base_min_days", "base_max_days", "pivot_tolerance_atr", "break_buffer_atr",
    "break_buffer_pct", "opening_range_minutes", "confirmation_bars", "max_chase_distance_atr",
    "event_ttl_seconds", "scoring_version", "feature_version", "detector_version",
    "api_schema_version", "provider_schema_version", "range_persistence_mode",
    "range_persistence_validation_version", "range_persistence_version",
    "range_persistence_length", "range_persistence_fast_length", "range_persistence_slope_days",
    "range_persistence_ratio_window", "range_persistence_ratio_threshold",
    "range_persistence_min_history_multiplier", "range_persistence_trend_family_weight",
    "range_persistence_final_weight_cap", "range_persistence_breakout_interaction_enabled",
    "range_persistence_breakout_interaction_cap",
)

# Production config/personal.toml [breakout] plus the two RANGE_PERSISTENCE_* worker
# environment variables; everything else is the code default. The two fixes this
# branch ships are stated explicitly so the baseline is production *after* them
# whatever the code default is at the time: no OTC rows, no ordinary ETFs.
PRODUCTION_ALIASES: dict[str, Any] = {
    "BREAKOUT_RADAR_ENABLED": True,
    "RANGE_PERSISTENCE_MODE": "enabled",  # personal.toml "active"
    "RANGE_PERSISTENCE_VALIDATION_VERSION": "range-persistence-v1",
    "BREAKOUT_SCAN_INTERVAL_REGULAR_SECONDS": 300,
    "BREAKOUT_SCAN_INTERVAL_PREMARKET_SECONDS": 600,
    "BREAKOUT_SCAN_INTERVAL_CLOSED_SECONDS": 1800,
    "BREAKOUT_SCAN_RETENTION_DAYS": 90,
    "BREAKOUT_ALLOW_OTC": False,
    "BREAKOUT_ALLOW_ETF": False,
}

# Values the September 2026 production ran with for fields the fixes change. The
# proof hash overlays them so it still equals the export's config_hash; the full
# hash (every field, actual values) is what production publishes after deploy.
SEPTEMBER_PRODUCTION_VALUES: dict[str, Any] = {"allow_etf": True}

# Candidate switches (DATA_SPEC section 8.2 and 13). Keys are settings aliases; the
# "research" entry holds private-attribute overrides that leave the hash unchanged.
VARIANTS: dict[str, dict[str, Any]] = {
    "baseline": {},
    "confirm3": {"BREAKOUT_CONFIRMATION_BARS": 3},
    "chase15": {"BREAKOUT_MAX_CHASE_DISTANCE_ATR": 1.5},
    "orb15": {"BREAKOUT_OPENING_RANGE_MINUTES": 15},
    "orb60": {"BREAKOUT_OPENING_RANGE_MINUTES": 60},
    "disc5": {"BREAKOUT_REGULAR_MIN_CHANGE_PCT": 5.0},
    "adv25": {"BREAKOUT_MIN_AVG_DOLLAR_VOLUME": 25_000_000},
    "basemin15": {"BREAKOUT_BASE_MIN_DAYS": 15},
    "rvol2": {"research": {"strong_single_rvol_min": 2.0}},
    "lookback10": {"research": {"rvol_lookback_sessions": 10}},
    # Smoke-only: production's Yahoo fallback fetched 20 calendar days, so its rvol history
    # held 19 sessions; this variant checks how much of the rvol gap that window explains.
    "lookback19": {"research": {"rvol_lookback_sessions": 19}},
    # Smoke test only: keep production's OTC rows as slot takers (DATA_SPEC 11.2).
    "hybrid_otc": {"BREAKOUT_ALLOW_OTC": True},
    # Smoke-only: production listed ordinary ETFs until the second fix; comparisons
    # with the September export run hybrid_otc+hybrid_etf.
    "hybrid_etf": {"BREAKOUT_ALLOW_ETF": True},
}


def variant_spec(name: str) -> dict[str, Any]:
    """Merge ``a+b`` variant names; the same switch set twice is an error."""

    merged: dict[str, Any] = {}
    research: dict[str, Any] = {}
    for part in name.split("+"):
        part = part.strip()
        if part not in VARIANTS:
            raise KeyError(f"unknown variant: {part}")
        for key, value in VARIANTS[part].items():
            if key == "research":
                for rkey, rvalue in value.items():
                    if rkey in research:
                        raise ValueError(f"{name}: research switch {rkey} set twice")
                    research[rkey] = rvalue
                continue
            if key in merged:
                raise ValueError(f"{name}: setting {key} set twice")
            merged[key] = value
    if research:
        merged["research"] = research
    return merged


def build_settings(variant: str, db_path: Path | str) -> BreakoutSettings:
    spec = variant_spec(variant)
    research = spec.pop("research", {})
    aliases = {**PRODUCTION_ALIASES, **spec}
    settings = BreakoutSettings(_env_file=None, db_path=str(db_path), **aliases)
    if research:
        settings = settings.with_research_overrides(**research)
    return settings


def production_field_hash(settings: BreakoutSettings) -> str:
    """The September export's config_hash, recomputed from these settings.

    Production hashed ``model_dump(mode="json")`` of the 61 fields it had at
    35ab1395; the fields added since (allow_otc) are left out and the fields the
    fixes flipped (allow_etf) take their September values, so the check proves
    every other value matches production.
    """

    dump = {**settings.model_dump(mode="json"), **SEPTEMBER_PRODUCTION_VALUES}
    return _stable_hash({name: dump[name] for name in PRODUCTION_FIELDS_35ab1395})


def full_hash(settings: BreakoutSettings) -> str:
    return _stable_hash(settings.model_dump(mode="json"))


def assert_production_hash(settings: BreakoutSettings) -> None:
    """The baseline must hash like production over production's own field set."""

    observed = production_field_hash(settings)
    if observed != PRODUCTION_CONFIG_HASH:
        raise RuntimeError(
            "baseline settings do not hash like production: "
            f"{observed} != {PRODUCTION_CONFIG_HASH}"
        )
