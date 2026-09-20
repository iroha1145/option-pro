"""Explicit adapters for retired strength snapshot regression tests.

These exercise the retained historical file format, freshness rules and
publisher. They are not current public scan or refresh contracts; replacement
API and worker tests use the unmodified production entry points.
"""
from __future__ import annotations

from unittest.mock import patch

from app.api import strength
from app.services.algorithm_modes import (
    AlgorithmResolution, PRODUCTION_ALGORITHM,
    screener_score_basis, screener_version,
)
from app.worker.tasks import StrengthRefreshTask


async def read_legacy_snapshot(_request=None, **parameters):
    ranking = parameters.get("ranking_algorithm") or PRODUCTION_ALGORITHM
    resolution = AlgorithmResolution(
        family="screener", requested=ranking, user_choice=None,
        admin_default=PRODUCTION_ALGORITHM, effective=ranking,
        version=screener_version(ranking), score_basis=screener_score_basis(ranking),
        source="historical_snapshot_test",
    )
    payload, _, _ = await strength._scan_snapshot_payload(**parameters, resolution=resolution)
    return payload


class LegacySnapshotTask(StrengthRefreshTask):
    """Exercise the historical publisher through shared task bookkeeping."""

    async def _run(self, parameters):
        return await self._run_legacy_snapshot(parameters)

    async def _run_action_batch(self, actions):
        with patch.object(strength, "strength_execution_parameters", strength.normalize_strength_scan_parameters):
            return await super()._run_action_batch(actions)

    async def _run_scheduled_batch(self):
        with patch.object(strength, "scheduled_strength_scan_parameters", lambda: dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS)):
            return await super()._run_scheduled_batch()
