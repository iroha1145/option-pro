"""Explicit adapters for retired strength snapshot regression tests.

These exercise the retained historical file format, freshness rules and
publisher. They are not current public scan or refresh contracts; replacement
API and worker tests use the unmodified production entry points.
"""
from __future__ import annotations

from unittest.mock import patch
from typing import Any

from app.data_paths import get_data_paths
from app.worker.runtime import TaskResult
from app.worker.tasks import _call_local, _timestamp_text

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

    async def _run(self, parameters: dict[str, Any]) -> TaskResult:
        """Historical snapshot implementation retained for compatibility tests.

        Scheduled and queued work only enters _run, which executes EOD.
        """
        from app.api.strength import (
            _existing_strength_publication,
            _strength_snapshot_path,
            _write_strength_snapshot,
            normalize_strength_scan_parameters,
            strength_scan_parameters_hash,
        )
        from app.services.strength.freshness import (
            decide_published_snapshot_replacement,
            strength_payload_is_publishable,
        )
        from app.services.strength.scanner import STRENGTH_SCORE_VERSION, scan_strength
        from app.services.utils import sanitize

        from app.services.algorithm_modes import EOD_LIMITED_V1

        scanner = self._scanner or scan_strength
        writer = self._writer or _write_strength_snapshot
        base_path = self._snapshot_path or get_data_paths().strength_snapshot
        parameters = normalize_strength_scan_parameters(parameters)
        if parameters.get("ranking_algorithm") == EOD_LIMITED_V1:
            raise ValueError("EOD cannot execute through the legacy snapshot writer")
        path = _strength_snapshot_path(parameters, base_path=base_path)
        payload = sanitize(
            await _call_local(
                scanner,
                **parameters,
                force_refresh=True,
            )
        )
        digest = strength_scan_parameters_hash(parameters)
        through = payload.get("score_data_through") if isinstance(payload, dict) else None
        publishable, publish_reason = strength_payload_is_publishable(payload)
        if not publishable:
            return TaskResult(
                status="degraded",
                error_code="strength_input_unavailable",
                details={
                    "result": "kept_previous_snapshot",
                    "reason": publish_reason,
                    "snapshot": path.name,
                    "parameters": parameters,
                    "parameters_hash": digest,
                    "score_data_through": through,
                    "published": False,
                },
            )
        saved_at = float(self._clock())
        existing_saved_at, existing_payload = await _call_local(
            _existing_strength_publication, path, parameters=parameters,
        )
        decision = decide_published_snapshot_replacement(
            existing_saved_at=existing_saved_at,
            incoming_saved_at=saved_at,
            existing_payload=existing_payload,
            incoming_payload=payload,
            expected_scoring_version=STRENGTH_SCORE_VERSION,
        )
        if not decision.replace:
            keep = decision.keep_result or "kept_previous_snapshot"
            if keep == "kept_newer_publish":
                return TaskResult(
                    status="idle",
                    details={
                        "result": keep,
                        "reason": decision.reason,
                        "snapshot": path.name,
                        "parameters": parameters,
                        "parameters_hash": digest,
                        "score_data_through": through,
                        "published": False,
                    },
                )
            return TaskResult(
                status="degraded",
                error_code="strength_input_unavailable",
                details={
                    "result": "kept_previous_snapshot",
                    "reason": decision.reason,
                    "snapshot": path.name,
                    "parameters": parameters,
                    "parameters_hash": digest,
                    "score_data_through": through,
                    "published": False,
                },
            )
        outcome = await _call_local(
            writer,
            path,
            parameters=parameters,
            payload=payload,
            saved_at=saved_at,
            base_path=base_path,
        )
        count = int(payload.get("count") or 0) if isinstance(payload, dict) else 0
        published = outcome == "written"
        if not published:
            keep = outcome or "kept_previous_snapshot"
            if keep == "kept_newer_publish":
                return TaskResult(
                    status="idle",
                    details={
                        "result": keep,
                        "snapshot": path.name,
                        "count": max(0, count),
                        "parameters": parameters,
                        "parameters_hash": digest,
                        "completed_at": _timestamp_text(saved_at),
                        "score_data_through": through,
                        "published": False,
                    },
                )
            return TaskResult(
                status="degraded",
                error_code="strength_input_unavailable",
                details={
                    "result": "kept_previous_snapshot",
                    "reason": keep,
                    "snapshot": path.name,
                    "count": max(0, count),
                    "parameters": parameters,
                    "parameters_hash": digest,
                    "completed_at": _timestamp_text(saved_at),
                    "score_data_through": through,
                    "published": False,
                },
            )
        return TaskResult(
            status="idle",
            details={
                "result": "refreshed",
                "snapshot": path.name,
                "count": max(0, count),
                "parameters": parameters,
                "parameters_hash": digest,
                "completed_at": _timestamp_text(saved_at),
                "score_data_through": through,
                "score_version": (
                    payload.get("score_version") if isinstance(payload, dict) else None
                ),
                "published": True,
            },
        )

    async def _run_action_batch(self, actions):
        with patch.object(strength, "strength_execution_parameters", strength.normalize_strength_scan_parameters):
            return await super()._run_action_batch(actions)

    async def _run_scheduled_batch(self):
        with patch.object(strength, "scheduled_strength_scan_parameters", lambda: dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS)):
            return await super()._run_scheduled_batch()
