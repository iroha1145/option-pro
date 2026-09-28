"""Publication gate of the live all-market batch, now that v1.7 scores stocks plus 12 benchmark funds.

An external review (2026-09-28) asked for these properties of ``worker.run_eod_limited_job``:

1. the coverage denominator is the eligible list of the same directory, session and
   fund scope as the panel, never a count from another batch or scope;
2. SPY, the benchmark every stock score reads, is required apart from overall
   coverage: without its session bar the run is refused even above 90%;
3. the 90% rule holds exactly on integer counts (89% refused, 90% published);
4. a refused run keeps the previous snapshot byte for byte and says why;
5. no partially written or mixed batch becomes the served snapshot.

The 0.90 constant is not a parameter of these tests: every expectation below is the
integer rule ``10 * complete >= 9 * eligible``.
"""
from __future__ import annotations

import dataclasses
import os
from datetime import date
from pathlib import Path

import pytest

from app.services.eod_limited import COMPUTE_VERSION, PURPOSE_LIVE, inference, market_data, worker
from app.services.eod_limited.live_config import LIVE_CONFIG, V16_CONFIG
from app.services.eod_limited.market_data import load_all_market_panel
from app.services.eod_limited.store import (
    publish_batch, read_batch, read_variant, snapshot_dir, snapshot_path, variant_key,
)
from app.services.eod_limited.universe import FUND_SCOPE_ALL, FUND_SCOPE_BENCHMARKS, benchmark_fund_tickers
from app.services.research_eod_v1.constants import HORIZONS, PROFILES
from app.services.research_eod_v1.membership import has_complete_session_bar
from eod_v17_fixtures import build_panel
from tests.test_eod_all_market_data import END, _bar, _directory_row, _install_provider
from tests.test_eod_limited_product import _scored

SESSION = date(2026, 9, 18)
PREVIOUS = "2026-09-17"
OLD_VERSION = "limited-all-market-v1.6"
ALL_VIEWS = sorted(variant_key(profile, horizon) for horizon in HORIZONS for profile in PROFILES)


def _publish_previous(root: Path, *, session: str = PREVIOUS, compute_version: str = OLD_VERSION,
                      coverage: dict | None = None, legacy_view: bool = False) -> bytes:
    """A served live batch with all nine views, each marked; returns the file's bytes.

    ``legacy_view`` adds a tenth view under a key no current run writes (the old
    ``all`` timeframe), so any merge of old views into a new batch would show.
    """
    keys = [(profile, horizon) for horizon in HORIZONS for profile in PROFILES]
    variants = {
        variant_key(profile, horizon): {
            **_scored(session=session, purpose=PURPOSE_LIVE), "profile": profile, "horizon": horizon,
            "compute_version": compute_version, "previous_marker": True,
        }
        for profile, horizon in keys + ([("balanced", "all")] if legacy_view else [])
    }
    outcome = publish_batch({
        "purpose": PURPOSE_LIVE, "synthetic": False, "compute_version": compute_version,
        "attempted_session": session, "served_session": session, "universe": "all_market",
        "coverage": coverage or {"status": "complete", "eligible_count": 5_112, "complete_bar_count": 5_100},
        "available_variants": sorted(variants), "variants": variants,
    }, root=root)
    assert outcome["ok"] is True
    return snapshot_path(root).read_bytes()


def _listing(root: Path) -> list[str]:
    return sorted(path.name for path in snapshot_dir(root).iterdir())


def _serve_market(monkeypatch, panel, *, eligible: int, complete: int) -> dict:
    """Stand in for ``load_all_market_panel``; returns the keyword arguments the worker passed."""
    seen: dict = {}
    manifest = {"status": "complete", "eligible_count": eligible, "complete_bar_count": complete,
                "source_hash": "gate-test", "volume_session_scope": market_data.VOLUME_SCOPE}

    def fake_load(**kwargs):
        seen.update(kwargs)
        coverage = [{"ticker": sid, "status": "ok", "bars": len(series.dates)} for sid, series in panel.items()]
        return panel, coverage, dict(manifest)

    monkeypatch.setattr(market_data, "load_all_market_panel", fake_load)
    return seen


class _ReachedScoring(Exception):
    """Raised where scoring would start: the run passed every publication check."""


def _run_to_gate(monkeypatch, root: Path, *, session: date = SESSION, **kwargs) -> dict | None:
    """The refusal of a live all-market run, or None when it went on to score."""

    def reached(*_args, **_kwargs):
        raise _ReachedScoring

    monkeypatch.setattr(inference, "precompute_all_horizon_inputs", reached)
    try:
        outcome = worker.run_eod_limited_job(session=session, root=root, refresh_context=False, **kwargs)
    except _ReachedScoring:
        return None
    assert outcome["status"] == "DATA_UNAVAILABLE", outcome
    return outcome


def _fast_scoring(monkeypatch, panel, *, fail_at_view: int | None = None, short_view: int | None = None) -> list:
    """Nine stub views stamped with this run's session and version, instead of the model."""
    calls: list = []
    monkeypatch.setattr(inference, "precompute_all_horizon_inputs", lambda *_a, **kw: {
        horizon: ({}, panel, {}) for horizon in kw["horizons"]
    })

    def score(*_args, **kwargs):
        calls.append((kwargs["profile"], kwargs["horizon"]))
        if len(calls) == fail_at_view:
            raise RuntimeError("injected scoring failure")
        scored = len(panel) - (1 if len(calls) == short_view else 0)
        return {**_scored(session=SESSION.isoformat(), purpose=PURPOSE_LIVE), "compute_version": COMPUTE_VERSION,
                "profile": kwargs["profile"], "horizon": kwargs["horizon"], "scored_security_count": scored}

    monkeypatch.setattr(worker, "score_eod_session", score)
    return calls


# ── 1. The denominator is this batch's directory, session and fund scope ─────

STOCKS = [f"S{index:02d}" for index in range(20)]
LATE_STOCKS = STOCKS[-4:]  # history, but no bar on the target session
OTHER_FUNDS = [f"F{index:02d}" for index in range(30)]  # funds outside the benchmark list
BENCHMARKS = sorted(benchmark_fund_tickers())
EVERYONE = STOCKS + BENCHMARKS + OTHER_FUNDS + ["PREF"]


def _market_directory() -> list[dict]:
    return (
        [_directory_row(ticker) for ticker in STOCKS]
        + [_directory_row(ticker, "ETF", "ARCX") for ticker in BENCHMARKS + OTHER_FUNDS]
        + [_directory_row("PREF", "PFD", "XNYS")]
    )


def _grouped(*, late: tuple[str, ...] = tuple(LATE_STOCKS)) -> dict[str, list[dict]]:
    history = {day: [_bar(ticker, 10.0 + index) for index, ticker in enumerate(EVERYONE)]
               for day in ("2026-09-14", "2026-09-15")}
    target = [_bar(ticker, 11.0 + index) for index, ticker in enumerate(EVERYONE) if ticker not in late]
    return {**history, END.isoformat(): target}


def test_denominator_is_the_eligible_list_of_this_directory_and_scope(monkeypatch, tmp_path):
    directory = _market_directory()
    _install_provider(monkeypatch, directory, _grouped())
    assert not set(OTHER_FUNDS) & benchmark_fund_tickers() and len(BENCHMARKS) == 12

    panel, coverage, manifest = load_all_market_panel(end=END, root=tmp_path, fund_scope=FUND_SCOPE_BENCHMARKS)
    statuses = {row["ticker"]: row["status"] for row in coverage}
    assert manifest["fund_scope"] == FUND_SCOPE_BENCHMARKS
    assert manifest["directory_count"] == len(directory)
    assert manifest["eligible_count"] == len(STOCKS) + len(BENCHMARKS) == 32
    # The numerator is the same list with a complete bar on this session.
    assert manifest["complete_bar_count"] == len(panel) == 28
    assert set(panel) == (set(STOCKS) - set(LATE_STOCKS)) | set(BENCHMARKS)
    assert manifest["missing_session_count"] == len(LATE_STOCKS)
    assert {statuses[ticker] for ticker in LATE_STOCKS} == {"missing_session"}
    # Funds outside the scope and unsupported types are in neither count.
    assert {statuses[ticker] for ticker in OTHER_FUNDS} == {"excluded:FUND_OUT_OF_SCOPE"}
    assert manifest["excluded_count"] == len(OTHER_FUNDS) + 1 == len(directory) - manifest["eligible_count"]

    # The same directory under the v1.6 scope counts every fund: the denominator follows the scope.
    panel_all, _coverage, manifest_all = load_all_market_panel(end=END, root=tmp_path, fund_scope=FUND_SCOPE_ALL)
    assert "fund_scope" not in manifest_all
    assert manifest_all["eligible_count"] == 32 + len(OTHER_FUNDS)
    assert manifest_all["complete_bar_count"] == len(panel_all) == 28 + len(OTHER_FUNDS)


def test_funds_outside_the_scope_cannot_make_up_for_missing_stocks(monkeypatch, tmp_path):
    # 28 of 32 (87.5%) under the benchmark scope; 58 of 62 (93.5%) if the other funds counted.
    _install_provider(monkeypatch, _market_directory(), _grouped())
    refusal = _run_to_gate(monkeypatch, tmp_path / "benchmarks", session=END)
    assert refusal["publish"]["reason"] == "all_market_coverage_incomplete"
    coverage = refusal["coverage"]
    assert coverage["fund_scope"] == LIVE_CONFIG.fund_scope == FUND_SCOPE_BENCHMARKS
    assert (coverage["complete_bar_count"], coverage["eligible_count"]) == (28, 32)
    assert coverage["source_dates"]["end"] == END.isoformat()

    all_funds = dataclasses.replace(LIVE_CONFIG, fund_scope=FUND_SCOPE_ALL)
    refusal = _run_to_gate(monkeypatch, tmp_path / "all", session=END, live_config=all_funds)
    # 58 of 62 passes coverage; the loader double's three sessions then fall short
    # of the SPY window the scorers read, the check that follows coverage.
    assert refusal is not None and refusal["publish"]["reason"] == "all_market_benchmark_window_incomplete"


@pytest.mark.parametrize(
    ("previous_eligible", "complete", "expected"),
    [
        # 4,601 of 5,112 passes; against a previous batch's 11,012 it would not.
        (11_012, 4_601, None),
        # 4,600 of 5,112 fails; against a previous batch's 100 it would pass.
        (100, 4_600, "all_market_coverage_incomplete"),
    ],
)
def test_the_gate_counts_this_run_not_the_served_batch(monkeypatch, tmp_path, previous_eligible, complete, expected):
    _publish_previous(tmp_path, coverage={"status": "complete", "eligible_count": previous_eligible,
                                          "complete_bar_count": previous_eligible})
    seen = _serve_market(monkeypatch, worker.build_synthetic_panel(end=SESSION), eligible=5_112, complete=complete)
    refusal = _run_to_gate(monkeypatch, tmp_path)
    assert (refusal and refusal["publish"]["reason"]) == expected
    # One loader call for this session and the configured scope, with no ticker subset.
    assert seen["end"] == SESSION and seen["fund_scope"] == LIVE_CONFIG.fund_scope and seen["tickers"] is None


# ── 2. SPY is required apart from overall coverage ───────────────────────────


@pytest.mark.parametrize(
    ("spy", "config"),
    [("absent", LIVE_CONFIG), ("stale", LIVE_CONFIG), ("absent", V16_CONFIG)],
    ids=["absent", "stale", "absent-v1.6-scope"],
)
def test_spy_without_a_session_bar_blocks_publication_above_ninety_percent(monkeypatch, tmp_path, spy, config):
    before = _publish_previous(tmp_path)
    panel = build_panel()  # SPY, QQQ, four more funds and 60 stocks
    eligible = len(panel)
    if spy == "absent":
        del panel["SPY"]
    else:  # still delivered, but its last bar is the session before
        panel["SPY"] = panel["SPY"].slice_through(date.fromisoformat(PREVIOUS))
    complete = sum(has_complete_session_bar(series, SESSION) for series in panel.values())
    assert (complete, eligible) == (65, 66) and 10 * complete >= 9 * eligible  # the 90% rule alone publishes
    _serve_market(monkeypatch, panel, eligible=eligible, complete=complete)
    outcome = worker.run_eod_limited_job(session=SESSION, root=tmp_path, refresh_context=False, live_config=config)
    assert outcome["status"] == "DATA_UNAVAILABLE"
    assert outcome["publish"] == {
        "ok": False, "reason": "all_market_benchmark_missing", "integrity": "stale_previous_retained",
        "served_session": PREVIOUS, "attempted_session": SESSION.isoformat(), "missing_benchmarks": ["SPY"],
    }
    assert outcome["served_session"] == PREVIOUS
    assert snapshot_path(tmp_path).read_bytes() == before
    assert _listing(tmp_path) == ["batch.json"]


@pytest.mark.parametrize("case", ["no_session_bar", "not_in_directory"])
def test_the_real_loader_without_spy_is_refused_at_full_coverage_of_the_rest(monkeypatch, tmp_path, case):
    directory = _market_directory()
    if case == "no_session_bar":  # 31 of 32 eligible (96.9%)
        _install_provider(monkeypatch, directory, _grouped(late=("SPY",)))
    else:  # SPY is not on the list at all: 31 of 31
        _install_provider(monkeypatch, [row for row in directory if row["ticker"] != "SPY"], _grouped(late=()))
    refusal = _run_to_gate(monkeypatch, tmp_path, session=END)
    assert refusal is not None, "published without SPY"
    assert refusal["publish"]["reason"] == "all_market_benchmark_missing"
    assert refusal["publish"]["missing_benchmarks"] == ["SPY"]
    coverage = refusal["coverage"]
    assert (coverage["complete_bar_count"], coverage["eligible_count"]) == ((31, 32) if case == "no_session_bar" else (31, 31))


@pytest.mark.parametrize("fund", ["QQQ", "XLE"])
def test_other_benchmark_funds_count_toward_coverage_like_any_member(monkeypatch, tmp_path, fund):
    # QQQ and the ``etfs`` theme funds are ranked only among funds; no stock score reads them.
    panel = build_panel()
    eligible = len(panel)
    del panel[fund]
    _serve_market(monkeypatch, panel, eligible=eligible, complete=len(panel))
    assert _run_to_gate(monkeypatch, tmp_path) is None


def test_a_coverage_refusal_also_names_a_missing_spy(monkeypatch, tmp_path):
    panel = worker.build_synthetic_panel(end=SESSION)
    del panel["SPY"]
    _serve_market(monkeypatch, panel, eligible=100, complete=len(panel))
    refusal = _run_to_gate(monkeypatch, tmp_path)
    assert refusal["publish"]["reason"] == "all_market_coverage_incomplete"
    assert refusal["publish"]["missing_benchmarks"] == ["SPY"]


# ── 3. The 90% boundary on integer counts ────────────────────────────────────


@pytest.mark.parametrize(
    ("eligible", "complete"),
    [
        (100, 89), (100, 90),        # 89% fails, 90% passes
        (10, 8), (10, 9),            # exactly 90%
        (11, 9), (11, 10),           # 0.9 x 11 = 9.9: nine is not enough (no truncation)
        (25, 22), (25, 23),          # 0.9 x 25 = 22.5: rounding half to even would admit 22 (88%)
        (45, 40), (45, 41),          # 0.9 x 45 = 40.5: 40 is 88.9%
        (10_001, 9_000),             # 89.991% prints as "90.0%" and still fails
        (10_001, 9_001),
        (5_112, 4_600), (5_112, 4_601),
        (11_012, 9_910), (11_012, 9_911),
        (1, 0), (1, 1),
        (0, 0),                      # an empty eligible list never publishes
    ],
)
def test_ninety_percent_boundary(monkeypatch, tmp_path, eligible, complete):
    _serve_market(monkeypatch, worker.build_synthetic_panel(end=SESSION), eligible=eligible, complete=complete)
    refusal = _run_to_gate(monkeypatch, tmp_path)
    publishes = eligible > 0 and 10 * complete >= 9 * eligible
    assert (refusal is None) is publishes, (complete, eligible)
    if refusal is not None:
        assert refusal["publish"]["reason"] == "all_market_coverage_incomplete"


def test_ninety_percent_boundary_sweep(monkeypatch, tmp_path):
    """Every list size up to 300 at the smallest passing count and one below it."""
    panel = worker.build_synthetic_panel(end=SESSION)
    for eligible in range(1, 301):
        smallest = (9 * eligible + 9) // 10  # integer ceiling of 0.9 x eligible
        assert 10 * smallest >= 9 * eligible > 10 * (smallest - 1)
        for complete, publishes in ((smallest - 1, False), (smallest, True)):
            _serve_market(monkeypatch, panel, eligible=eligible, complete=complete)
            assert (_run_to_gate(monkeypatch, tmp_path) is None) is publishes, (complete, eligible)


# ── 4. A refused run keeps the previous snapshot and says why ────────────────


def test_a_refused_run_keeps_the_previous_snapshot_and_says_why(monkeypatch, tmp_path):
    before = _publish_previous(tmp_path)
    listing = _listing(tmp_path)
    _serve_market(monkeypatch, worker.build_synthetic_panel(end=SESSION), eligible=5_112, complete=4_600)
    outcome = worker.run_eod_limited_job(session=SESSION, root=tmp_path, refresh_context=False)

    assert outcome["status"] == "DATA_UNAVAILABLE"
    assert outcome["session"] == SESSION.isoformat() and outcome["served_session"] == PREVIOUS
    assert outcome["publish"] == {
        "ok": False, "reason": "all_market_coverage_incomplete", "integrity": "stale_previous_retained",
        "served_session": PREVIOUS, "attempted_session": SESSION.isoformat(),
    }
    assert (outcome["coverage"]["complete_bar_count"], outcome["coverage"]["eligible_count"]) == (4_600, 5_112)
    assert outcome["available_variants"] == ALL_VIEWS
    # Same file, same bytes, nothing written beside it (no diagnostics generation, no temp file).
    assert snapshot_path(tmp_path).read_bytes() == before
    assert _listing(tmp_path) == listing == ["batch.json"]
    assert read_batch(tmp_path)["served_session"] == PREVIOUS
    served = read_variant("balanced", "mid", root=tmp_path)
    assert served["served_session"] == PREVIOUS and served["previous_marker"] is True


def test_a_refused_first_run_writes_nothing(monkeypatch, tmp_path):
    _serve_market(monkeypatch, worker.build_synthetic_panel(end=SESSION), eligible=100, complete=89)
    outcome = worker.run_eod_limited_job(session=SESSION, root=tmp_path, refresh_context=False)
    assert outcome["status"] == "DATA_UNAVAILABLE" and outcome["served_session"] is None
    assert outcome["publish"]["reason"] == "all_market_coverage_incomplete"
    assert outcome["publish"]["integrity"] == "unavailable"
    assert not snapshot_path(tmp_path).exists()


# ── 5. No partial or mixed batch becomes the served snapshot ─────────────────


def _new_batch() -> dict:
    return {"purpose": PURPOSE_LIVE, "served_session": SESSION.isoformat(),
            "attempted_session": SESSION.isoformat(), "compute_version": COMPUTE_VERSION,
            "variants": {"balanced|mid": _scored(session=SESSION.isoformat(), purpose=PURPOSE_LIVE)}}


def _fail_batch_replace(monkeypatch, root: Path) -> None:
    """``os.replace`` onto batch.json fails; other renames (diagnostics) still work."""
    original = os.replace
    target = snapshot_path(root)

    def replace(source, destination, *args, **kwargs):
        if Path(destination) == target:
            raise OSError(28, "No space left on device")
        return original(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "replace", replace)


def test_publish_batch_only_ever_renames_a_whole_file_into_place(monkeypatch, tmp_path):
    before = _publish_previous(tmp_path)
    _fail_batch_replace(monkeypatch, tmp_path)
    outcome = publish_batch(_new_batch(), root=tmp_path)
    assert outcome == {"ok": False, "publish_failed": True, "integrity": "stale_previous_retained",
                       "served_session": PREVIOUS, "attempted_session": SESSION.isoformat()}
    assert snapshot_path(tmp_path).read_bytes() == before
    assert _listing(tmp_path) == ["batch.json"]  # the complete temp file was removed
    assert read_batch(tmp_path)["served_session"] == PREVIOUS


def test_a_half_written_batch_never_replaces_the_served_one(monkeypatch, tmp_path):
    before = _publish_previous(tmp_path)
    written: list[str] = []
    original = os.fdopen

    class HalfWriter:
        """Writes half of the document, then fails like a full disk."""

        def __init__(self, handle):
            self.handle = handle

        def __getattr__(self, name):
            return getattr(self.handle, name)

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            self.handle.close()
            return False

        def write(self, text):
            self.handle.write(text[: len(text) // 2])
            self.handle.flush()
            written.append(self.handle.name)
            raise OSError(28, "No space left on device")

    def fdopen(fd, mode="r", *args, **kwargs):
        handle = original(fd, mode, *args, **kwargs)
        return HalfWriter(handle) if "w" in mode else handle  # readers of the old batch are untouched

    monkeypatch.setattr(os, "fdopen", fdopen)
    outcome = publish_batch(_new_batch(), root=tmp_path)
    assert written and outcome["ok"] is False and outcome["integrity"] == "stale_previous_retained"
    assert snapshot_path(tmp_path).read_bytes() == before
    assert _listing(tmp_path) == ["batch.json"]  # the half-written temp file was removed


def test_an_unencodable_batch_raises_before_any_file_is_touched(tmp_path):
    before = _publish_previous(tmp_path)
    broken = _new_batch()
    broken["variants"]["balanced|mid"]["score"] = float("nan")  # JSON without NaN is enforced
    with pytest.raises(ValueError):
        publish_batch(broken, root=tmp_path)
    assert snapshot_path(tmp_path).read_bytes() == before
    assert _listing(tmp_path) == ["batch.json"]


@pytest.mark.parametrize("failure", ["fifth_view_raises", "one_view_scores_one_security_short"])
def test_a_run_that_fails_while_scoring_leaves_the_old_batch_byte_identical(monkeypatch, tmp_path, failure):
    before = _publish_previous(tmp_path)
    panel = worker.build_synthetic_panel(end=SESSION)
    _serve_market(monkeypatch, panel, eligible=len(panel), complete=len(panel))
    if failure == "fifth_view_raises":
        calls = _fast_scoring(monkeypatch, panel, fail_at_view=5)
        match = "injected scoring failure"
    else:
        calls = _fast_scoring(monkeypatch, panel, short_view=7)
        match = "all_market_scoring_coverage_incomplete"
    with pytest.raises(RuntimeError, match=match):
        worker.run_eod_limited_job(session=SESSION, root=tmp_path, refresh_context=False)
    assert len(calls) == (5 if failure == "fifth_view_raises" else 9)
    assert snapshot_path(tmp_path).read_bytes() == before
    assert _listing(tmp_path) == ["batch.json"]  # the new diagnostics generation was discarded


def test_a_failed_publication_after_scoring_keeps_the_old_batch(monkeypatch, tmp_path):
    before = _publish_previous(tmp_path)
    panel = worker.build_synthetic_panel(end=SESSION)
    _serve_market(monkeypatch, panel, eligible=len(panel), complete=len(panel))
    _fast_scoring(monkeypatch, panel)
    _fail_batch_replace(monkeypatch, tmp_path)
    outcome = worker.run_eod_limited_job(session=SESSION, root=tmp_path, refresh_context=False)
    assert outcome["status"] == "PUBLISH_FAILED"
    assert outcome["publish"]["integrity"] == "stale_previous_retained"
    assert outcome["served_session"] == PREVIOUS
    assert snapshot_path(tmp_path).read_bytes() == before
    assert _listing(tmp_path) == ["batch.json"]


@pytest.mark.parametrize(
    ("previous_session", "previous_version"),
    [(SESSION.isoformat(), COMPUTE_VERSION), (SESSION.isoformat(), OLD_VERSION), (PREVIOUS, COMPUTE_VERSION)],
)
def test_a_live_batch_holds_nine_views_of_one_session_and_one_version(
    monkeypatch, tmp_path, previous_session, previous_version,
):
    _publish_previous(tmp_path, session=previous_session, compute_version=previous_version, legacy_view=True)
    panel = worker.build_synthetic_panel(end=SESSION)
    _serve_market(monkeypatch, panel, eligible=len(panel), complete=len(panel))
    _fast_scoring(monkeypatch, panel)
    outcome = worker.run_eod_limited_job(session=SESSION, root=tmp_path, refresh_context=False)
    assert outcome["status"] == "RAN"
    batch = read_batch(tmp_path)
    assert (batch["served_session"], batch["compute_version"]) == (SESSION.isoformat(), COMPUTE_VERSION)
    assert sorted(batch["variants"]) == batch["available_variants"] == ALL_VIEWS
    for key, view in batch["variants"].items():
        # Even an identical session and version never carries an old view into a live batch.
        assert "previous_marker" not in view, key
        assert (view["session_date"], view["compute_version"]) == (SESSION.isoformat(), COMPUTE_VERSION), key


@pytest.mark.parametrize(
    ("previous_session", "previous_version", "kept"),
    [(SESSION.isoformat(), COMPUTE_VERSION, True), (SESSION.isoformat(), OLD_VERSION, False),
     (PREVIOUS, COMPUTE_VERSION, False)],
)
def test_an_injected_panel_keeps_old_views_only_of_the_same_session_and_version(
    tmp_path, previous_session, previous_version, kept,
):
    _publish_previous(tmp_path, session=previous_session, compute_version=previous_version, legacy_view=True)
    outcome = worker.run_eod_limited_job(
        session=SESSION, panel=worker.build_synthetic_panel(sessions=5, end=SESSION), root=tmp_path,
        themes=["semiconductors"], algorithms=["A_trend_quality"], refresh_context=False,
    )
    assert outcome["status"] == "RAN"
    batch = read_batch(tmp_path)
    assert ("aggressive|long" in batch["variants"]) is kept and ("balanced|all" in batch["variants"]) is kept
    assert "balanced|mid" in batch["variants"] and "previous_marker" not in batch["variants"]["balanced|mid"]
    for key, view in batch["variants"].items():
        assert (view["session_date"], view["compute_version"]) == (batch["served_session"], COMPUTE_VERSION), key
