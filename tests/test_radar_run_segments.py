"""Retry and clean-up decisions of the segment orchestrator (RUN_SPEC section 7)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "research" / "breakout_radar" / "replay_v1" / "scripts" / "run_segments.py"


def _load():
    spec = importlib.util.spec_from_file_location("radar_run_segments", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_retry_only_after_a_real_failure_without_run_json(tmp_path: Path) -> None:
    rs = _load()
    out = tmp_path / "seg_2026-01-05"
    out.mkdir()
    assert rs.should_retry(1, out, attempt=1) is True
    assert rs.should_retry(1, out, attempt=2) is False  # one retry only
    assert rs.should_retry(0, out, attempt=1) is False
    assert rs.should_retry(-15, out, attempt=1) is False  # killed by SIGTERM: leave it alone
    (out / "STOP").touch()
    assert rs.should_retry(1, out, attempt=1) is False  # stopped on purpose
    (out / "STOP").unlink()
    (out / "run.json").write_text(json.dumps({"scans": 1}))
    assert rs.should_retry(1, out, attempt=1) is False  # finished despite the exit code


def test_finished_needs_run_json_and_every_bundle(tmp_path: Path) -> None:
    rs = _load()
    out = tmp_path / "seg_2026-01-05"
    (out / "baseline").mkdir(parents=True)
    (out / "hybrid_otc_hybrid_etf").mkdir(parents=True)
    variants = rs.variants_of(["--daily-db", "x", "--variants", "baseline,hybrid_otc+hybrid_etf", "--warmup", "1"])
    assert variants == ["baseline", "hybrid_otc+hybrid_etf"]
    assert rs.finished(out, variants) is False
    (out / "run.json").write_text("{}")
    assert rs.finished(out, variants) is False
    (out / "baseline" / "research_bundle.json.gz").write_bytes(b"")
    (out / "hybrid_otc_hybrid_etf" / "research_bundle.json.gz").write_bytes(b"")
    assert rs.finished(out, variants) is True
    assert rs.variants_of(["--variants=baseline"]) == ["baseline"] and rs.variants_of([]) == ["baseline"]
