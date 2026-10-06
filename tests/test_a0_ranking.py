from __future__ import annotations


from app.api.strength import (
    DEFAULT_STRENGTH_SCAN_PARAMETERS,
    normalize_strength_scan_parameters,
    strength_scan_parameters_hash,
)
from app.services.algorithm_modes import A0_ALGORITHM, PRODUCTION_ALGORITHM


def test_production_parameter_hash_stays_on_the_default_identity() -> None:
    production = normalize_strength_scan_parameters(dict(DEFAULT_STRENGTH_SCAN_PARAMETERS))
    with_explicit = normalize_strength_scan_parameters(
        {**DEFAULT_STRENGTH_SCAN_PARAMETERS, "ranking_algorithm": PRODUCTION_ALGORITHM}
    )
    a0 = normalize_strength_scan_parameters(
        {**DEFAULT_STRENGTH_SCAN_PARAMETERS, "ranking_algorithm": A0_ALGORITHM}
    )
    assert production == dict(DEFAULT_STRENGTH_SCAN_PARAMETERS)
    assert with_explicit == production
    assert "ranking_algorithm" not in production
    assert a0["ranking_algorithm"] == A0_ALGORITHM
    assert strength_scan_parameters_hash(production) == strength_scan_parameters_hash(
        dict(DEFAULT_STRENGTH_SCAN_PARAMETERS)
    )
    assert strength_scan_parameters_hash(a0) != strength_scan_parameters_hash(production)
