from __future__ import annotations

from pathlib import Path

from app import runtime_environment
from app.runtime_environment import load_runtime_environment


def test_runtime_environment_keeps_exports_and_uses_canonical_file_order(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / ".env"
    machine = tmp_path / "machine.env"
    secrets = tmp_path / "secrets.env"
    legacy.write_text("HOST_BIND=legacy\nOPENAI_API_KEY=legacy-secret\n", encoding="utf-8")
    machine.write_text("HOST_BIND=127.0.0.1\nPORT=2000\n", encoding="utf-8")
    secrets.write_text("OPENAI_API_KEY=canonical-secret\n", encoding="utf-8")
    environment = {"PORT": "9000"}

    loaded = load_runtime_environment(
        (legacy, machine, secrets),
        environ=environment,
    )

    assert loaded == (legacy, machine, secrets)
    assert environment == {
        "HOST_BIND": "127.0.0.1",
        "OPENAI_API_KEY": "canonical-secret",
        "PORT": "9000",
    }


def test_runtime_environment_default_uses_the_current_canonical_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    isolated = tmp_path / "machine.env"
    isolated.write_text("DATA_DIR=/isolated-data\n", encoding="utf-8")
    monkeypatch.setattr(runtime_environment, "RUNTIME_ENV_FILES", (isolated,))
    environment: dict[str, str] = {}

    loaded = runtime_environment.load_runtime_environment(environ=environment)

    assert loaded == (isolated,)
    assert environment == {"DATA_DIR": "/isolated-data"}
