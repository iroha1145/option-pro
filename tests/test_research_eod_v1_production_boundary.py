from __future__ import annotations

from pathlib import Path

from app.services.research_eod_v1.eod_shadow import publish_snapshot, read_snapshot


def test_production_main_does_not_mount_research_router() -> None:
    text = Path("backend/app/main.py").read_text(encoding="utf-8")
    assert "research_eod_v1.router" not in text
    assert "include_router(research_eod_v1" not in text
    assert "research_eod_v1" not in text


def test_failed_publish_keeps_previous_snapshot(tmp_path, monkeypatch) -> None:
    path = tmp_path / "snap.json"
    first = publish_snapshot(path, session_date="2026-09-15", config_hash="h1", universe_version="u", rows=[{"security_id": "AAA"}])
    assert read_snapshot(path)["session_date"] == first["session_date"]

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    from app.services.research_eod_v1 import eod_shadow

    monkeypatch.setattr(eod_shadow, "atomic_write_json", boom)
    retained = publish_snapshot(path, session_date="2026-09-16", config_hash="h2", universe_version="u", rows=[{"security_id": "BBB"}])
    assert retained["integrity"] == "stale_previous_retained"
    assert read_snapshot(path)["session_date"] == "2026-09-15"
