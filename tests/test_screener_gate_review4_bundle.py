"""Packaging checks for the review-4 compact zip. Synthetic files only."""

from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "research"))

from screener_gate_review4_bundle import (  # noqa: E402
    build_review4_bundle,
    technical_relabel_delta,
    verify_locked_pack,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_locked_pack_refuses_a_changed_file(tmp_path: Path):
    pack = tmp_path / "return_pack"
    pack.mkdir()
    (pack / "manifest.json").write_text("changed", encoding="utf-8")
    with pytest.raises(RuntimeError, match="manifest.json"):
        verify_locked_pack(pack, {"manifest.json": "0" * 64})


def test_relabel_delta_keeps_the_paired_increment_separate_from_the_level():
    old = {"bootstrap_h20_close": {"B0_current": {"H": {"mean": 0.01, "ci95": [-0.1, 0.1]}}}}
    paired = {"conditional_mean": -0.0001, "n_valid": 3, "ci95": [-0.01, 0.01]}
    v2 = {
        "level_means": {"B0_current": {"20": {"close_full": 0.01}}},
        "bootstrap_paired_top20": {"G1_stock_reference": {"20": {"close": {"H": paired}}}},
        "coverage_top20": {"G1_stock_reference": {"added_days": 1}},
    }
    delta = technical_relabel_delta(old, v2)
    assert delta["close_level_matches_old_absolute"] is True
    assert delta["g1_minus_b0_h20_close_paired"]["H"]["conditional_mean"] == -0.0001


def test_bundle_omits_checkpoints_and_keeps_old_hashes(tmp_path: Path):
    research = tmp_path / "research"
    pack = research / "return_pack"
    metrics = research / "return_pack_metrics_v2"
    checkpoints = research / "return_pack_discovery_v2" / "checkpoints"
    pack.mkdir(parents=True)
    metrics.mkdir(parents=True)
    checkpoints.mkdir(parents=True)
    (pack / "manifest.json").write_text("old", encoding="utf-8")
    (pack / "summary.json").write_text(
        json.dumps({"bootstrap_h20_close": {"B0_current": {"H": {"mean": 0.01, "ci95": [0, 0]}}}}),
        encoding="utf-8",
    )
    (metrics / "summary.json").write_text(
        json.dumps(
            {
                "level_means": {"B0_current": {"20": {"close_full": 0.01}}},
                "bootstrap_paired_top20": {"G1_stock_reference": {"20": {"close": {"H": {"conditional_mean": 0.0}}}}},
            }
        ),
        encoding="utf-8",
    )
    (checkpoints / "2022-01-03.json").write_text("{}", encoding="utf-8")
    dest = tmp_path / "out.zip"
    build_review4_bundle(
        research,
        dest,
        require_discovery=False,
        locked={"manifest.json": _sha(pack / "manifest.json")},
    )
    with zipfile.ZipFile(dest) as archive:
        names = set(archive.namelist())
    assert "review4/old_return_pack_sha256.json" in names
    assert "return_pack_metrics_v2/summary.json" in names
    assert "return_pack_discovery_v2/checkpoints/2022-01-03.json" not in names
