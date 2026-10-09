from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.access import request_owner_access_context
from app.services.ai_jobs import repository as job_module
from app.services.catalysts import local_intelligence as local
from app.services.catalysts.local_intelligence import LocalCatalystIntelligence
from app.services.catalysts.personal_service import PersonalCatalystService
from test_catalyst_local_intelligence import (
    _apply_news, _focus_result, _news_change, _news_result, _stack,
)
from test_claude_catalyst_compatibility import _finish_claude_with_sources


@pytest.fixture(autouse=True)
def owner_context():
    with request_owner_access_context(True):
        yield


@pytest.fixture
def clock(monkeypatch):
    now = [datetime(2026, 10, 9, 5, 41, 10, tzinfo=timezone.utc)]
    monkeypatch.setattr(local, "_utc_now", lambda: now[0])
    monkeypatch.setattr(job_module, "_utcnow", lambda: now[0])
    monkeypatch.setattr(local, "macro_conditions_context", lambda: None)
    return now


def legacy_key(members, *, truncate=True):
    representative = min(members, key=lambda r: (" ".join(sorted(local._title_tokens(r["raw_title"]))), r["news_id"]))
    kind = local._event_type(representative["raw_title"], representative.get("raw_summary"))
    tickers = sorted({ticker for row in members for ticker in row.get("canonical_tickers", [])})
    tokens = sorted(local._title_tokens(representative["raw_title"]))
    signature = "-".join(tokens[:10] if truncate else tokens) or f"news-{representative['news_id']}"
    return f"{kind}:{','.join(tickers[:5] if truncate else tickers)}:{signature}"


def prepared_items(engine, revision):
    return engine._hotspots_for_revision(revision, limit=100)[1]


def memberships(engine, revision):
    with engine._connect() as connection:
        rows = connection.execute(
            """SELECT g.event_group_id,g.event_group_version,g.news_identities_json
               FROM catalyst_local_hotspot_items i JOIN catalyst_local_event_groups g
                 ON g.event_group_id=i.event_group_id AND g.event_group_version=i.event_group_version
               WHERE i.prepared_revision=? ORDER BY i.ordinal""", (revision,),
        ).fetchall()
    return [(row["event_group_id"], row["event_group_version"], sorted(x["news_id"] for x in json.loads(row["news_identities_json"]))) for row in rows]


def test_real_two_headline_collision_persists_both_distinct_clusters(tmp_path, clock):
    fixture = json.loads((Path(__file__).parents[1] / "evidence/collision-repro.json").read_text())
    rows = fixture["rows"]
    clusters = local._cluster_rows(rows)
    assert len(clusters) == 2
    assert len({legacy_key(c) for c in clusters}) == 1
    assert "evt_" + hashlib.sha256(legacy_key(clusters[0]).encode()).hexdigest()[:32] == fixture["legacy_event_group_id"]
    etl, ai, engine = _stack(tmp_path)
    changes = []
    for row in rows:
        change = _news_change(row["change_sequence"], row["news_id"], available_at=datetime.fromisoformat(row["source_available_at"].replace("Z", "+00:00")), title=row["raw_title"], tickers=())
        change["news"].update(published_at=row["published_at"], fetched_at=row["fetched_at"])
        changes.append(change)
    _apply_news(etl, changes, as_of=clock[0])
    outcome = engine.reconcile(allow_scheduled_jobs=False)
    groups = memberships(engine, outcome["prepared_revision"])
    assert len(groups) == 2 and len({row[0] for row in groups}) == 2
    assert sorted(row[2] for row in groups) == [[192140], [192183]]
    assert len(prepared_items(engine, outcome["prepared_revision"])) == 2
    with ai._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM ai_jobs").fetchone()[0] == 0


def test_greedy_bridge_equal_full_signatures_persist_separately(tmp_path, clock):
    specs = [
        (1, "alpha beta gamma delta epsilon", ["AAA"]),
        (2, "alpha beta gamma delta epsilon", ["BBB"]),
        (3, "beta gamma delta epsilon zulu", ["AAA", "BBB"]),
        (4, "alpha gamma delta epsilon yankee", ["BBB"]),
        (5, "alpha delta epsilon yankee xray", ["AAA", "BBB"]),
    ]
    rows = [{"news_id": n, "raw_title": title, "raw_summary": "", "canonical_tickers": tickers,
             "source_available_at": local._iso(clock[0] - timedelta(minutes=10) + timedelta(seconds=n))} for n, title, tickers in specs]
    clusters = local._cluster_rows(rows)
    assert [[r["news_id"] for r in c] for c in clusters] == [[1, 3], [2, 4, 5]]
    assert len({legacy_key(c, truncate=False) for c in clusters}) == 1
    etl, _, engine = _stack(tmp_path, canonical_tickers=("AAA", "BBB"))
    _apply_news(etl, [_news_change(n, n, title=title, tickers=tickers, summary="Public fixture event.", available_at=clock[0] - timedelta(minutes=10) + timedelta(seconds=n)) for n, title, tickers in specs], as_of=clock[0])
    first = engine.reconcile()["prepared_revision"]
    groups = memberships(engine, first)
    assert sorted(row[2] for row in groups) == [[1, 3], [2, 4, 5]]
    assert len({row[0] for row in groups}) == 2
    original = engine._active_revisions
    engine._active_revisions = lambda *args, **kwargs: list(reversed(original(*args, **kwargs)))
    repeated = engine.reconcile()["prepared_revision"]
    assert repeated == first
    assert memberships(engine, repeated) == groups


def test_newer_member_and_body_revision_keep_group_identity_and_advance_version(tmp_path, clock):
    etl, _, engine = _stack(tmp_path)
    title = "Nvidia launches Blackwell platform"
    _apply_news(etl, [_news_change(1, 900, available_at=clock[0] - timedelta(minutes=20), title=title)], as_of=clock[0])
    first_revision = engine.reconcile()["prepared_revision"]
    first = memberships(engine, first_revision)[0]
    clock[0] += timedelta(minutes=2)
    _apply_news(etl, [_news_change(2, 901, available_at=clock[0] - timedelta(seconds=20), title=title + " capacity", tickers=("NVDA", "AMD"))], as_of=clock[0])
    second_revision = engine.reconcile()["prepared_revision"]
    second = memberships(engine, second_revision)[0]
    assert len(memberships(engine, second_revision)) == 1
    assert second[0] == first[0] and second[1] == first[1] + 1
    assert second[2] == [900, 901]
    clock[0] += timedelta(minutes=2)
    _apply_news(etl, [_news_change(3, 900, available_at=clock[0] - timedelta(seconds=20), title=title, summary="Updated production figures and shipment details.")], as_of=clock[0])
    third_revision = engine.reconcile()["prepared_revision"]
    third = memberships(engine, third_revision)[0]
    assert third[0] == first[0] and third[1] == second[1] + 1
    assert memberships(engine, first_revision) == [first]
    assert memberships(engine, second_revision) == [second]


def test_anchor_expiry_starts_new_identity_without_rewriting_prior_snapshot(tmp_path, clock):
    etl, _, engine = _stack(tmp_path)
    _apply_news(etl, [
        _news_change(1, 900, available_at=clock[0] - timedelta(hours=71), title="Nvidia launches Blackwell platform"),
        _news_change(2, 901, available_at=clock[0] - timedelta(hours=1), title="Nvidia launches Blackwell platform"),
    ], as_of=clock[0])
    old_revision = engine.reconcile()["prepared_revision"]
    old = memberships(engine, old_revision)[0]
    assert old[2] == [900, 901]
    clock[0] += timedelta(hours=2)
    # The fixture advances wall time instantly; expire the short monotonic
    # cache as it would already have expired after two real hours.
    local._reset_revision_cache()
    new_revision = engine.reconcile()["prepared_revision"]
    new = memberships(engine, new_revision)[0]
    assert new[2] == [901] and new[0] != old[0]
    assert memberships(engine, old_revision) == [old]


def test_late_lower_news_id_starts_a_new_anchor_without_losing_members(tmp_path, clock):
    etl, _, engine = _stack(tmp_path)
    title = "Nvidia launches Blackwell platform"
    _apply_news(etl, [_news_change(1, 900, title=title, available_at=clock[0] - timedelta(minutes=10))], as_of=clock[0])
    old = memberships(engine, engine.reconcile()["prepared_revision"])[0]
    clock[0] += timedelta(minutes=1)
    _apply_news(etl, [_news_change(2, 899, title=title, available_at=clock[0] - timedelta(seconds=10))], as_of=clock[0])
    new = memberships(engine, engine.reconcile()["prepared_revision"])[0]
    assert new[0] != old[0] and new[2] == [899, 900]


def test_duplicate_proposal_is_rejected_before_any_database_write(tmp_path, clock):
    etl, _, engine = _stack(tmp_path)
    _apply_news(etl, [_news_change(1, 900, available_at=clock[0] - timedelta(minutes=10))], as_of=clock[0])
    revision = engine.reconcile()["prepared_revision"]
    with engine._connect() as connection:
        plan = engine._plan_hotspots(connection, now=clock[0])
        assert len(plan) == 1
        duplicate = {**copy.deepcopy(plan[0]), "input_hash": "f" * 64}
        statements = []
        connection.execute("BEGIN IMMEDIATE")
        connection.set_trace_callback(statements.append)
        with pytest.raises(ValueError, match="hotspot_plan_duplicate_event_group_id"):
            engine._commit_hotspots(connection, [plan[0], duplicate], expected_base_revision=revision, now=clock[0])
        connection.rollback()
        assert not any(sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE")) for sql in statements)
        assert connection.execute("SELECT COUNT(*) FROM catalyst_local_event_groups").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM catalyst_local_hotspot_revisions").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM catalyst_local_hotspot_items").fetchone()[0] == 1


@pytest.mark.parametrize("already_published", [True, False])
def test_legacy_group_cycle_and_paid_receipts_survive_new_identity(tmp_path, clock, monkeypatch, already_published):
    etl, ai, legacy = _stack(tmp_path)
    _apply_news(etl, [_news_change(1, 900, available_at=clock[0] - timedelta(minutes=10))], as_of=clock[0])
    with monkeypatch.context() as old_code:
        old_code.setattr(local, "_cluster_key", legacy_key)
        legacy.reconcile()
        news = legacy.request_analysis(900, force=False)
        _finish_claude_with_sources(ai, news["job_id"], _news_result(news_id=900, change_sequence=1, content_hash="hash-900-1"), [])
        old_revision = legacy.reconcile()["prepared_revision"]
        old_group = prepared_items(legacy, old_revision)[0]
        cycle = legacy.request_market_focus_cycle(expected_prepared_revision=old_revision)
        result = _focus_result(ai, cycle)
        result.update(no_new_material_catalyst=False, dominant_events=[{"event_group_id": old_group["event_group_id"], "summary": "新品发布带来交付预期。", "affected_sectors": ["半导体"]}])
        _finish_claude_with_sources(ai, cycle["job_id"], result, [])
        if already_published:
            legacy.reconcile()
    paid_before = {job_id: ai.get_job(job_id) for job_id in (news["job_id"], cycle["job_id"])}
    with legacy._connect() as connection:
        historical_groups = [dict(r) for r in connection.execute("SELECT * FROM catalyst_local_event_groups ORDER BY event_group_id,event_group_version")]
    current = LocalCatalystIntelligence(legacy.db_path, ai, mode="manual", canonical_tickers=("NVDA",), news_model="gpt-5.6-luna", news_reasoning="max", focus_model="claude-sonnet-5-5", focus_reasoning="xhigh")
    new_revision = current.reconcile(allow_scheduled_jobs=False)["prepared_revision"]
    assert new_revision > old_revision
    assert prepared_items(current, new_revision)[0]["event_group_id"] != old_group["event_group_id"]
    assert prepared_items(current, old_revision)[0] == old_group
    with current._connect() as connection:
        for expected in historical_groups:
            stored = dict(connection.execute("SELECT * FROM catalyst_local_event_groups WHERE event_group_id=? AND event_group_version=?", (expected["event_group_id"], expected["event_group_version"])).fetchone())
            assert stored == expected
    for job_id, before in paid_before.items():
        after = ai.get_job(job_id)
        assert after["provider_result_json"] == before["provider_result_json"]
        assert after["budget_charge_microusd"] == before["budget_charge_microusd"] > 0
        assert after["result_json"] == before["result_json"]
    service = PersonalCatalystService.__new__(PersonalCatalystService)
    with request_owner_access_context(False):
        public = service._project_focus_cycle_for_access(current.market_focus_cycle(cycle["cycle_id"]), include_owner_state=False)
    assert public["result"] == result
    assert public["verification_status"] == "legacy_unverified"
    assert current.request_analysis(900, force=False)["job_id"] == news["job_id"]
    with ai._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM ai_jobs").fetchone()[0] == 2
