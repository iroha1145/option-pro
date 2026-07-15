from __future__ import annotations

import io
import json
import stat
from pathlib import Path

import pytest
from dotenv import dotenv_values

from app.access import owner_password_hash_is_valid
from app.tools import personal_secrets
from app import legacy_env_adapter
from app.api.settings import settings_status


def _set_stdin(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setattr(personal_secrets.sys, "stdin", io.StringIO(value + "\n"))


def test_option_pro_secret_allowlist_is_exact() -> None:
    expected = {
        "OPENAI_API_KEY",
        "APP_PASSWORD_HASH",
        "INTERNAL_API_TOKEN",
        "FINNHUB_API_KEY",
        "DATA_DIR",
    }
    assert set(personal_secrets.SECRET_KEYS) == expected
    assert legacy_env_adapter.SECRET_KEYS == expected


def test_browser_settings_expose_only_option_pro_configuration_booleans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        "OPENAI_API_KEY": "sk-never-return-this",
        "FINNHUB_API_KEY": "finnhub-never-return-this",
        "INTERNAL_API_TOKEN": "internal-never-return-this",
        "MASSIVE_API_KEY": "not-an-option-pro-setting",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)

    report = settings_status()
    assert report == {
        "openai": {"configured": True},
        "finnhub": {"configured": True},
        "internal_api": {"configured": True},
    }
    serialized = json.dumps(report)
    assert all(value not in serialized for value in values.values())


def test_secret_updates_are_private_atomic_and_never_echo_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "secrets.env"
    monkeypatch.setattr(personal_secrets, "DEFAULT_SECRETS_PATH", path)
    first_secret = "sk-test-secret-never-print"
    _set_stdin(monkeypatch, first_secret)

    assert personal_secrets.main(["set", "OPENAI_API_KEY"]) == 0
    first_output = capsys.readouterr()
    assert first_secret not in first_output.out
    assert first_secret not in first_output.err
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert dotenv_values(path)["OPENAI_API_KEY"] == first_secret

    second_secret = "finnhub-test-secret"
    _set_stdin(monkeypatch, second_secret)
    assert personal_secrets.main(["set", "FINNHUB_API_KEY"]) == 0
    second_output = capsys.readouterr()
    assert second_secret not in second_output.out
    assert second_secret not in second_output.err

    backups = list(tmp_path.glob("secrets.env.bak.*"))
    assert len(backups) == 1
    assert stat.S_IMODE(backups[0].stat().st_mode) == 0o600
    assert dotenv_values(backups[0])["OPENAI_API_KEY"] == first_secret


def test_status_and_validation_return_only_configuration_booleans(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "secrets.env"
    monkeypatch.setattr(personal_secrets, "DEFAULT_SECRETS_PATH", path)
    secret = "sk-status-must-not-leak"
    personal_secrets.atomic_write({"OPENAI_API_KEY": secret}, path)

    assert personal_secrets.main(["status"]) == 0
    status_output = capsys.readouterr().out
    assert secret not in status_output
    status = json.loads(status_output)
    assert status["OPENAI_API_KEY"] == {"configured": True}
    assert status["APP_PASSWORD_HASH"] == {"configured": False}

    assert personal_secrets.main(["validate"]) == 0
    validate_output = capsys.readouterr().out
    assert secret not in validate_output
    validation = json.loads(validate_output)
    assert validation["file"] == {"exists": True, "permission_0600": True}
    assert validation["secrets"]["OPENAI_API_KEY"] == {
        "configured": True,
        "format_valid": True,
    }


def test_validate_fails_for_a_world_readable_secrets_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "secrets.env"
    secret = "sk-permission-test-secret"
    path.write_text(f"OPENAI_API_KEY={secret}\n", encoding="utf-8")
    path.chmod(0o644)
    monkeypatch.setattr(personal_secrets, "DEFAULT_SECRETS_PATH", path)

    assert personal_secrets.main(["validate"]) == 1
    output = capsys.readouterr()
    assert secret not in output.out
    assert secret not in output.err
    report = json.loads(output.out)
    assert report["file"] == {"exists": True, "permission_0600": False}


def test_owner_password_is_hashed_before_it_reaches_secrets_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "secrets.env"
    monkeypatch.setattr(personal_secrets, "DEFAULT_SECRETS_PATH", path)
    password = "a-long-owner-password"
    _set_stdin(monkeypatch, password)

    assert personal_secrets.main(["set", "APP_PASSWORD_HASH"]) == 0
    output = capsys.readouterr()
    assert password not in output.out
    assert password not in output.err
    written = str(dotenv_values(path)["APP_PASSWORD_HASH"])
    assert password not in written
    assert owner_password_hash_is_valid(written)


def test_data_directory_uses_absolute_path_validation() -> None:
    assert personal_secrets._normalized_value("DATA_DIR", "/data") == "/data"
    assert personal_secrets._format_valid("DATA_DIR", "/data") is True
    with pytest.raises(ValueError, match="absolute"):
        personal_secrets._normalized_value("DATA_DIR", "relative/data")


def test_shell_interface_never_passes_a_secret_value_to_python() -> None:
    script = (personal_secrets.REPOSITORY_ROOT / "personal.sh").read_text(
        encoding="utf-8"
    )
    assert '"$python_bin" -m app.tools.personal_secrets "$@"' not in script
    assert '"$python_bin" -m app.tools.personal_secrets "$command_name" "$key"' in script
    assert "Secret values must be entered through standard input." in script
