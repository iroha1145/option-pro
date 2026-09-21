from __future__ import annotations

from copy import deepcopy
import unittest

from app.services.eod_limited.market_registry import load_market_registry
from app.services.eod_limited.price_only import apply_price_only_track
from app.services.eod_limited.weight_provenance import (
    BASE_SOURCE,
    PRIOR_SOURCE,
    validate_weight_sources,
    weight_provenance,
)
from app.services.research_eod_v1.capability import (
    PRICE_ONLY_DIAGNOSTIC,
    diagnostic_weights,
    family_required,
    rescore_row,
)
from app.services.research_eod_v1.registry_scoring import (
    FACTORS,
    load_registry,
    normalized,
    resolve_weights,
    score_features,
)


class WeightProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = load_registry()
        self.family = "A_trend_quality"

    def test_all_96_stored_priors_match_and_registry_is_unchanged(self) -> None:
        before = deepcopy(self.registry)
        result = validate_weight_sources(self.registry)
        self.assertEqual(result["checked_by_source"], {PRIOR_SOURCE: 96, BASE_SOURCE: 0})
        self.assertLess(result["maximum_absolute_error"], 5e-11)
        self.assertEqual(self.registry, before)
        for theme, spec in self.registry["sectors"].items():
            for family, base in self.registry["base_algorithm_weights"].items():
                for profile, p in self.registry["profiles"].items():
                    for horizon, h in self.registry["horizons"].items():
                        expected = normalized({
                            factor: base[factor] * spec["factor_prior"][factor]
                            * p["factor_tilt"][i] * h["factor_tilt"][i]
                            for i, factor in enumerate(FACTORS)
                        })
                        actual = resolve_weights(self.registry, theme, family, profile, horizon)
                        for factor in FACTORS:
                            self.assertAlmostEqual(actual[factor], expected[factor], delta=1e-9)

    def test_reapplying_prior_or_altering_another_theme_is_detected(self) -> None:
        for theme in ("semiconductors", "consumer_electronics"):
            with self.subTest(theme=theme):
                changed = deepcopy(self.registry)
                sector = changed["sectors"][theme]
                weights = sector["candidates"][self.family]["weights"]
                sector["candidates"][self.family]["weights"] = normalized({
                    factor: weights[factor] * sector["factor_prior"][factor] for factor in FACTORS
                })
                with self.assertRaisesRegex(ValueError, "disagree"):
                    validate_weight_sources(changed)

    def test_generic_stock_context_uses_base_only(self) -> None:
        registry = load_market_registry()
        result = validate_weight_sources(registry)
        self.assertEqual(result["checked_by_source"], {PRIOR_SOURCE: 96, BASE_SOURCE: 4})
        provenance = weight_provenance(
            registry, theme_id="all_market_stocks", family=self.family,
            profile="balanced", horizon="mid", track=PRICE_ONLY_DIAGNOSTIC,
        )
        self.assertEqual(provenance["source"], BASE_SOURCE)
        self.assertEqual(provenance["prior_application_count"], 0)

    def test_provenance_identifies_registry_and_tilts(self) -> None:
        kwargs = dict(theme_id="semiconductors", family=self.family,
                      profile="balanced", horizon="mid", track=PRICE_ONLY_DIAGNOSTIC)
        first = weight_provenance(self.registry, **kwargs)
        self.assertEqual(first, weight_provenance(self.registry, **kwargs))
        self.assertEqual(first["prior_application_count"], 1)
        self.assertEqual(first["profile_factor_tilt"], self.registry["profiles"]["balanced"]["factor_tilt"])
        self.assertEqual(first["horizon_factor_tilt"], self.registry["horizons"]["mid"]["factor_tilt"])
        changed = deepcopy(self.registry)
        changed["profiles"]["balanced"]["factor_tilt"][0] += 0.01
        second = weight_provenance(changed, **kwargs)
        self.assertNotEqual(first["registry_sha256"], second["registry_sha256"])
        self.assertNotEqual(first["id"], second["id"])

    def _apply(self, row: dict, *, family: str | None = None) -> dict:
        return apply_price_only_track(
            {"rows": [row]}, registry=self.registry, theme_id="semiconductors",
            family=family or self.family, profile="balanced", horizon="mid",
        )

    def test_rescore_replaces_stale_contributions_and_missing_optional_weights(self) -> None:
        for missing in ((), ("G",), ("B", "G")):
            with self.subTest(missing=missing):
                factors = {factor: float(75 + i * 3) for i, factor in enumerate(FACTORS)}
                factors.update({factor: None for factor in missing})
                configured = resolve_weights(self.registry, "semiconductors", self.family)
                upstream = score_features(factors, configured)
                row = {
                    "factors": factors, "score": upstream.score,
                    "configured_weights": configured,
                    "effective_weights": upstream.effective_weights,
                    "score_components": upstream.contributions,
                    "rejection_reasons": ["LOW_COVERAGE"] if upstream.score is None else [],
                    "status": "rejected" if upstream.score is None else "eligible",
                }
                before = deepcopy(row)
                payload = self._apply(row)
                final = payload["rows"][0]
                track = diagnostic_weights(configured, track=PRICE_ONLY_DIAGNOSTIC, family=self.family)
                expected = score_features(factors, track)
                self.assertEqual(final["score"], expected.score)
                self.assertEqual(final["effective_weights"], expected.effective_weights)
                self.assertEqual(final["score_components"], expected.contributions)
                self.assertAlmostEqual(sum(final["score_components"].values()), final["score"])
                self.assertAlmostEqual(sum(final["effective_weights"].values()), 1)
                self.assertEqual(final["configured_weights"], configured)
                self.assertEqual(final["track_weights"], track)
                self.assertEqual(final["weight_provenance_id"], payload["weight_provenance"]["id"])
                self.assertNotIn("weight_provenance", final)
                self.assertEqual(payload["weight_provenance"]["disabled_factors"], ["G"])
                self.assertEqual(final["capability_flags"], {
                    "volume_verified": False,
                    "dollar_liquidity_verified": False,
                    "volume_session_verified": False,
                })
                self.assertEqual(final["status"], "watch")
                self.assertEqual(final["rejection_reasons"], ["DOLLAR_LIQUIDITY_UNVERIFIED"])
                self.assertEqual(row, before)

    def test_missing_required_or_insufficient_coverage_clears_final_items(self) -> None:
        for missing in (("T",), ("B", "P", "V", "G")):
            with self.subTest(missing=missing):
                factors = {factor: 80.0 for factor in FACTORS}
                factors.update({factor: None for factor in missing})
                final = self._apply({
                    "factors": factors, "score": 88.0, "status": "eligible",
                    "effective_weights": {"T": 1.0}, "score_components": {"T": 88.0},
                })["rows"][0]
                self.assertIsNone(final["score"])
                self.assertEqual(final["effective_weights"], {})
                self.assertEqual(final["score_components"], {})
                self.assertEqual(final["status"], "rejected")
                self.assertIn("DATA_INSUFFICIENT", final["rejection_reasons"])
                self.assertTrue(final["configured_weights"])
                self.assertTrue(final["track_weights"])

    def test_source_reject_without_factors_keeps_reason_and_clears_final_items(self) -> None:
        final = self._apply({
            "score": None, "factors": {}, "status": "rejected",
            "rejection_reasons": ["MISSING_T_BAR"],
            "effective_weights": {"T": 1.0}, "score_components": {"T": 80.0},
        })["rows"][0]
        self.assertEqual(final["rejection_reasons"], ["MISSING_T_BAR"])
        self.assertEqual(final["effective_weights"], {})
        self.assertEqual(final["score_components"], {})
        self.assertIsNone(final["score"])

    def test_technical_rejection_and_unknown_volume_qualification_are_preserved(self) -> None:
        factors = {factor: 80.0 for factor in FACTORS}
        final = self._apply({"factors": factors, "rejection_reasons": ["EXTENDED"]})["rows"][0]
        self.assertEqual(final["status"], "rejected")
        self.assertEqual(final["rejection_reasons"], ["EXTENDED"])
        self.assertAlmostEqual(sum(final["score_components"].values()), final["score"])
        final = self._apply({"factors": factors}, family="B_confirmed_base_breakout")["rows"][0]
        self.assertEqual(final["status"], "watch")
        self.assertEqual(set(final["rejection_reasons"]), {
            "DOLLAR_LIQUIDITY_UNVERIFIED", "VOLUME_SESSION_UNVERIFIED",
        })

    def test_rescore_exposes_actual_result_even_below_score_floor(self) -> None:
        weights = resolve_weights(self.registry, "semiconductors", self.family)
        result = rescore_row(
            {"factors": {factor: 20.0 for factor in FACTORS}}, weights,
            coverage_min=.9, required=family_required(self.family), score_floor=70,
        )
        self.assertFalse(result["final_eligible"])
        self.assertEqual(result["rejection_reasons"], ["LOW_SCORE"])
        self.assertAlmostEqual(sum(result["score_components"].values()), result["score"])
        self.assertAlmostEqual(sum(result["effective_weights"].values()), 1)
