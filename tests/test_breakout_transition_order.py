"""Same-time edges use persisted causal order, never hash or state rank order."""
from datetime import timedelta
import sqlite3

from app.services.breakouts.repository import BreakoutRepository
from test_breakout_event_time_semantics import START, _event, _publish


def _chain(states, at, *, event_id="event-time-semantics", prefix="edge"):
    return [
        {"event_id": event_id, "transition_id": f"{prefix}-{len(states) - index:03d}",
         "from_state": source, "to_state": target, "reason": f"test-step-{index}", "evidence_at": at}
        for index, (source, target) in enumerate(zip(states, states[1:]))
    ]


def test_legacy_same_time_transitions_retain_insert_order_after_read_only_restart(tmp_path):
    path = tmp_path / "timeline.db"
    writer = BreakoutRepository(path)
    writer.initialize()
    states = ["DISCOVERED", "WATCHING", "TRIGGERED", "CONFIRMED", "HOLDING", "RETESTING", "RETEST_HELD", "REACCELERATING", "HOLDING"]
    _publish(writer, START, [_event(state=states[-1], observed_at=START, previous_state=states[0])],
        transitions=_chain(states, START))
    with sqlite3.connect(path) as connection:
        # Old persisted JSON has no sequence. The row itself already records
        # ordering; a read must recover it without a migration or write.
        assert all('"transition_sequence"' not in row[0] for row in connection.execute("SELECT transition_json FROM breakout_transitions"))
        before = list(connection.execute("SELECT transition_id,transition_json FROM breakout_transitions ORDER BY rowid"))
    event = BreakoutRepository(path, read_only=True).get_event("event-time-semantics")
    transitions = event["transitions"]
    assert [transitions[0]["from_state"], *[edge["to_state"] for edge in transitions]] == states
    assert [edge["transition_sequence"] for edge in transitions] == sorted(edge["transition_sequence"] for edge in transitions)
    with sqlite3.connect(path) as connection:
        assert list(connection.execute("SELECT transition_id,transition_json FROM breakout_transitions ORDER BY rowid")) == before


def test_later_same_time_retest_chain_stays_after_trigger_chain_and_replay_is_idempotent(tmp_path):
    path = tmp_path / "multiple-scans.db"
    writer = BreakoutRepository(path)
    writer.initialize()
    first_states = ["DISCOVERED", "WATCHING", "TRIGGERED", "CONFIRMED", "HOLDING"]
    _publish(writer, START, [_event(state="HOLDING", observed_at=START, previous_state="DISCOVERED")],
        transitions=_chain(first_states, START, prefix="z-first"))
    later = START + timedelta(minutes=5)
    second_states = ["HOLDING", "RETESTING", "RETEST_HELD", "REACCELERATING", "HOLDING"]
    later_event = _event(state="HOLDING", observed_at=later, previous_state="HOLDING", triggered_at=START)
    second_edges = _chain(second_states, later, prefix="a-second")
    _publish(writer, later, [later_event], transitions=second_edges)
    _publish(writer, later + timedelta(seconds=1), [later_event], transitions=second_edges)
    transitions = BreakoutRepository(path, read_only=True).get_event("event-time-semantics")["transitions"]
    assert [transitions[0]["from_state"], *[edge["to_state"] for edge in transitions]] == first_states + second_states[1:]
    assert len({edge["transition_sequence"] for edge in transitions}) == len(transitions)
