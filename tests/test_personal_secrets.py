from __future__ import annotations

import io
import json
import multiprocessing
import re
import stat
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from dotenv import dotenv_values
from fastapi.testclient import TestClient

import app.main as main
from app.access import owner_password_hash_is_valid
from app.tools import personal_secrets
from app import legacy_env_adapter
from app.api.settings import settings_status


def _set_stdin(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setattr(personal_secrets.sys, "stdin", io.StringIO(value + "\n"))


class _FakeValidationResponse:
    def __init__(self, status: int = 200, body: bytes = b"{}") -> None:
        self.status = status
        self.body = body
        self.read_sizes: list[int] = []

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        return None

    def getcode(self) -> int:
        return self.status

    def read(self, size: int) -> bytes:
        self.read_sizes.append(size)
        return self.body[:size]


def _process_secret_mutation(
    path_text: str,
    key: str,
    value: str | None,
    start,
    ready,
    done,
    results,
) -> None:
    ready.set()
    start.wait()
    try:
        personal_secrets._mutate_secret(Path(path_text), key, value)
    except BaseException as exc:
        results.put((False, type(exc).__name__, str(exc)))
    else:
        results.put((True, key, value is not None))
    finally:
        done.set()


def test_option_pro_secret_allowlist_is_exact() -> None:
    expected = {
        "OPENAI_API_KEY",
        "APP_PASSWORD_HASH",
        "INTERNAL_API_TOKEN",
        "FINNHUB_API_KEY",
        "MARKETDATA_TOKEN",
        "MASSIVE_API_KEY",
    }
    assert set(personal_secrets.SECRET_KEYS) == expected
    assert legacy_env_adapter.SECRET_KEYS == expected


def test_interactive_secret_input_uses_hidden_terminal_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _InteractiveInput(io.StringIO):
        def isatty(self) -> bool:
            return True

    sentinel = "secret-read-through-getpass"
    monkeypatch.setattr(personal_secrets.sys, "stdin", _InteractiveInput("echoed"))
    monkeypatch.setattr(personal_secrets.getpass, "getpass", lambda _prompt: sentinel)

    assert personal_secrets._read_secret() == sentinel


def test_browser_settings_expose_only_option_pro_configuration_booleans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        "OPENAI_API_KEY": "sk-never-return-this",
        "FINNHUB_API_KEY": "finnhub-never-return-this",
        "MARKETDATA_TOKEN": "market-never-return-this",
        "INTERNAL_API_TOKEN": "internal-never-return-this",
        "MASSIVE_API_KEY": "not-an-option-pro-setting",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)

    report = settings_status()
    assert report == {
        "openai": {"configured": True},
        "finnhub": {"configured": True},
        "marketdata": {"configured": True},
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
    lock_path = personal_secrets._lock_path(path)
    assert lock_path.parent == path.parent
    assert stat.S_ISREG(lock_path.stat().st_mode)
    assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600


def test_secret_lock_rejects_symlinks_non_regular_files_and_unsafe_permissions(
    tmp_path: Path,
) -> None:
    symlink_dir = tmp_path / "symlink"
    symlink_dir.mkdir()
    symlink_path = symlink_dir / "secrets.env"
    lock_path = personal_secrets._lock_path(symlink_path)
    target = symlink_dir / "lock-target"
    target.write_text("", encoding="utf-8")
    target.chmod(0o600)
    lock_path.symlink_to(target)
    with pytest.raises(OSError):
        personal_secrets.atomic_write(
            {"OPENAI_API_KEY": "sk-symlink-lock-rejected"},
            symlink_path,
        )

    directory_dir = tmp_path / "directory"
    directory_dir.mkdir()
    directory_path = directory_dir / "secrets.env"
    personal_secrets._lock_path(directory_path).mkdir()
    with pytest.raises((OSError, ValueError)):
        personal_secrets.atomic_write(
            {"OPENAI_API_KEY": "sk-directory-lock-rejected"},
            directory_path,
        )

    mode_dir = tmp_path / "mode"
    mode_dir.mkdir()
    mode_path = mode_dir / "secrets.env"
    mode_lock = personal_secrets._lock_path(mode_path)
    mode_lock.write_text("", encoding="utf-8")
    mode_lock.chmod(0o644)
    with pytest.raises(ValueError, match="0600"):
        personal_secrets.atomic_write(
            {"OPENAI_API_KEY": "sk-mode-lock-rejected"},
            mode_path,
        )


def test_secret_cross_process_lock_blocks_until_the_full_transaction_finishes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "secrets.env"
    personal_secrets.atomic_write(
        {"OPENAI_API_KEY": "sk-lock-blocking-original"},
        path,
    )
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    ready = context.Event()
    done = context.Event()
    results = context.Queue()
    process = context.Process(
        target=_process_secret_mutation,
        args=(
            str(path),
            "FINNHUB_API_KEY",
            "finnhub-lock-blocking-value",
            start,
            ready,
            done,
            results,
        ),
    )

    with personal_secrets._exclusive_secret_lock(path):
        process.start()
        assert ready.wait(timeout=5)
        start.set()
        time.sleep(0.25)
        assert done.is_set() is False
        assert process.is_alive()

    assert done.wait(timeout=5)
    process.join(timeout=5)
    assert process.exitcode == 0
    assert results.get(timeout=2) == (True, "FINNHUB_API_KEY", True)
    values = dotenv_values(path)
    assert values["OPENAI_API_KEY"] == "sk-lock-blocking-original"
    assert values["FINNHUB_API_KEY"] == "finnhub-lock-blocking-value"


def test_concurrent_set_and_remove_preserve_every_independent_update(
    tmp_path: Path,
) -> None:
    path = tmp_path / "secrets.env"
    original_marketdata_token = "marketdata-token-before"
    replacement_marketdata_token = "marketdata-token-after"
    personal_secrets.atomic_write(
        {
            "OPENAI_API_KEY": "sk-concurrent-remove-me",
            "MARKETDATA_TOKEN": original_marketdata_token,
        },
        path,
    )
    operations = (
        ("FINNHUB_API_KEY", "finnhub-concurrent-value"),
        ("INTERNAL_API_TOKEN", "internal-concurrent-value"),
        ("OPENAI_API_KEY", None),
        ("MARKETDATA_TOKEN", replacement_marketdata_token),
    )
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = []
    ready_events = []
    done_events = []
    for key, value in operations:
        ready = context.Event()
        done = context.Event()
        process = context.Process(
            target=_process_secret_mutation,
            args=(str(path), key, value, start, ready, done, results),
        )
        process.start()
        processes.append(process)
        ready_events.append(ready)
        done_events.append(done)
    assert all(event.wait(timeout=5) for event in ready_events)
    start.set()
    assert all(event.wait(timeout=10) for event in done_events)
    for process in processes:
        process.join(timeout=5)
        assert process.exitcode == 0
    outcomes = [results.get(timeout=2) for _ in operations]
    assert all(outcome[0] is True for outcome in outcomes), outcomes

    values = dotenv_values(path)
    assert "OPENAI_API_KEY" not in values
    assert values["FINNHUB_API_KEY"] == "finnhub-concurrent-value"
    assert values["INTERNAL_API_TOKEN"] == "internal-concurrent-value"
    assert values["MARKETDATA_TOKEN"] == replacement_marketdata_token


def test_failed_update_releases_lock_and_does_not_echo_the_secret(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "secrets.env"
    monkeypatch.setattr(personal_secrets, "DEFAULT_SECRETS_PATH", path)
    personal_secrets.atomic_write(
        {"OPENAI_API_KEY": "sk-existing-private-value"},
        path,
    )
    real_replace = personal_secrets.os.replace
    failed_secret = "failed-secret-must-not-leak"

    def fail_replace(_source, _destination):
        raise OSError("simulated replacement failure")

    monkeypatch.setattr(personal_secrets.os, "replace", fail_replace)
    _set_stdin(monkeypatch, failed_secret)
    assert personal_secrets.main(["set", "FINNHUB_API_KEY"]) == 2
    failed_output = capsys.readouterr()
    assert failed_secret not in failed_output.out
    assert failed_secret not in failed_output.err

    monkeypatch.setattr(personal_secrets.os, "replace", real_replace)
    recovered_secret = "recovered-secret-value"
    _set_stdin(monkeypatch, recovered_secret)
    assert personal_secrets.main(["set", "INTERNAL_API_TOKEN"]) == 0
    recovered_output = capsys.readouterr()
    assert recovered_secret not in recovered_output.out
    assert recovered_secret not in recovered_output.err
    assert dotenv_values(path)["INTERNAL_API_TOKEN"] == recovered_secret


def test_secret_backup_and_replacement_are_complete_before_becoming_visible(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "secrets.env"
    old_payload = b"OPENAI_API_KEY=sk-old-private-value\n"
    new_values = {
        "OPENAI_API_KEY": "sk-old-private-value",
        "FINNHUB_API_KEY": "new-private-value",
    }
    personal_secrets.atomic_write(
        {"OPENAI_API_KEY": "sk-old-private-value"},
        path,
    )
    assert path.read_bytes() == old_payload

    real_link = personal_secrets.os.link
    real_replace = personal_secrets.os.replace
    observations: list[str] = []

    def inspect_link(source, destination, **kwargs):
        source_path = Path(source)
        destination_path = Path(destination)
        assert not destination_path.exists()
        assert stat.S_IMODE(source_path.stat().st_mode) == 0o600
        assert source_path.read_bytes() == old_payload
        real_link(source, destination, **kwargs)
        assert stat.S_IMODE(destination_path.stat().st_mode) == 0o600
        assert destination_path.read_bytes() == old_payload
        observations.append("backup")

    def inspect_replace(source, destination):
        source_path = Path(source)
        assert stat.S_IMODE(source_path.stat().st_mode) == 0o600
        assert source_path.read_bytes() == personal_secrets._serialize(new_values)
        real_replace(source, destination)
        assert stat.S_IMODE(Path(destination).stat().st_mode) == 0o600
        observations.append("replace")

    monkeypatch.setattr(personal_secrets.os, "link", inspect_link)
    monkeypatch.setattr(personal_secrets.os, "replace", inspect_replace)

    backup = personal_secrets.atomic_write(new_values, path)

    assert backup is not None
    assert observations == ["backup", "replace"]
    assert backup.read_bytes() == old_payload
    assert path.read_bytes() == personal_secrets._serialize(new_values)
    assert list(tmp_path.glob(".*.tmp")) == []


def test_secret_write_failure_keeps_private_complete_original_and_backup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "secrets.env"
    original = {"OPENAI_API_KEY": "sk-original-private-value"}
    personal_secrets.atomic_write(original, path)
    original_payload = path.read_bytes()

    def fail_replace(_source, _destination):
        raise OSError("simulated atomic replacement failure")

    monkeypatch.setattr(personal_secrets.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated atomic replacement failure"):
        personal_secrets.atomic_write(
            {**original, "FINNHUB_API_KEY": "replacement-value"},
            path,
        )

    assert path.read_bytes() == original_payload
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    backups = list(tmp_path.glob("secrets.env.bak.*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original_payload
    assert stat.S_IMODE(backups[0].stat().st_mode) == 0o600
    assert list(tmp_path.glob(".*.tmp")) == []


def test_secret_update_rejects_symlinks_and_non_private_existing_files(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.env"
    target.write_text("OPENAI_API_KEY=sk-do-not-follow\n", encoding="utf-8")
    target.chmod(0o600)
    linked = tmp_path / "secrets.env"
    linked.symlink_to(target)

    with pytest.raises(OSError):
        personal_secrets.atomic_write(
            {"OPENAI_API_KEY": "sk-replacement-value"},
            linked,
        )
    assert target.read_text(encoding="utf-8") == (
        "OPENAI_API_KEY=sk-do-not-follow\n"
    )
    assert list(tmp_path.glob("secrets.env.bak.*")) == []

    linked.unlink()
    linked.write_text("OPENAI_API_KEY=sk-world-readable\n", encoding="utf-8")
    linked.chmod(0o644)
    with pytest.raises(ValueError, match="0600"):
        personal_secrets.atomic_write(
            {"OPENAI_API_KEY": "sk-replacement-value"},
            linked,
        )
    assert stat.S_IMODE(linked.stat().st_mode) == 0o644
    assert list(tmp_path.glob("secrets.env.bak.*")) == []


def test_secret_rejects_environment_interpolation_without_echoing_value(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "secrets.env"
    monkeypatch.setattr(personal_secrets, "DEFAULT_SECRETS_PATH", path)
    unsafe_secret = "sk-test-${HOME}-must-not-print"
    _set_stdin(monkeypatch, unsafe_secret)

    assert personal_secrets.main(["set", "OPENAI_API_KEY"]) == 2
    output = capsys.readouterr()
    assert unsafe_secret not in output.out
    assert unsafe_secret not in output.err
    assert not path.exists()
    assert personal_secrets._format_valid("OPENAI_API_KEY", unsafe_secret) is False


def test_cli_does_not_echo_a_secret_mistaken_for_a_key_name(
    capsys: pytest.CaptureFixture[str],
) -> None:
    mistaken_key = "sk-proj-key-name-sentinel-never-print"

    assert personal_secrets.main(["remove", mistaken_key]) == 2

    output = capsys.readouterr()
    assert mistaken_key not in output.out + output.err
    assert "unsupported Secret key" in output.err


def test_status_returns_only_booleans_and_validation_returns_safe_states(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "secrets.env"
    monkeypatch.setattr(personal_secrets, "DEFAULT_SECRETS_PATH", path)
    secret = "sk-status-must-not-leak"
    personal_secrets.atomic_write({"OPENAI_API_KEY": secret}, path)
    response = _FakeValidationResponse(
        body=b'response-body-must-not-leak'
    )
    monkeypatch.setattr(
        personal_secrets,
        "_open_validation_request",
        lambda _request: response,
    )

    assert personal_secrets.main(["status"]) == 0
    status_output = capsys.readouterr().out
    assert secret not in status_output
    status = json.loads(status_output)
    assert status["OPENAI_API_KEY"] == {"configured": True}
    assert status["APP_PASSWORD_HASH"] == {"configured": False}

    assert personal_secrets.main(["validate"]) == 0
    validate_output = capsys.readouterr().out
    assert secret not in validate_output
    assert "response-body-must-not-leak" not in validate_output
    assert "https://" not in validate_output
    validation = json.loads(validate_output)
    assert validation["file"] == {"exists": True, "permission_0600": True}
    assert validation["secrets"]["OPENAI_API_KEY"] == {
        "configured": True,
        "format_valid": True,
        "connection_checked": True,
        "connection_skipped": False,
        "connection_ok": True,
        "reason": "reachable",
        "http_status": 200,
    }
    assert response.read_sizes == [4096]


def test_validation_uses_only_fixed_free_read_endpoints_and_headers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "secrets.env"
    monkeypatch.setattr(personal_secrets, "DEFAULT_SECRETS_PATH", path)
    monkeypatch.setenv("MACROLENS_URL", "https://macrolens.example:8443/")
    secrets = {
        "OPENAI_API_KEY": "sk-openai-validation-sentinel",
        "FINNHUB_API_KEY": "finnhub-validation-sentinel",
        "INTERNAL_API_TOKEN": "internal-validation-sentinel",
    }
    personal_secrets.atomic_write(secrets, path)
    requests: list[urllib.request.Request] = []
    responses: list[_FakeValidationResponse] = []

    def fake_open(request: urllib.request.Request) -> _FakeValidationResponse:
        requests.append(request)
        response = _FakeValidationResponse(
            body=b"x" * (personal_secrets._VALIDATION_READ_LIMIT_BYTES + 512)
        )
        responses.append(response)
        return response

    monkeypatch.setattr(personal_secrets, "_open_validation_request", fake_open)

    assert personal_secrets.main(["validate"]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert all(secret not in output.out for secret in secrets.values())
    assert "https://" not in output.out
    assert [request.full_url for request in requests] == [
        "https://api.openai.com/v1/models",
        "https://finnhub.io/api/v1/quote?symbol=AAPL",
        "https://macrolens.example:8443/internal/v1/health",
    ]
    assert all(request.get_method() == "GET" for request in requests)
    assert "/responses" not in " ".join(request.full_url for request in requests)

    openai_headers = {
        key.lower(): value for key, value in requests[0].header_items()
    }
    finnhub_headers = {
        key.lower(): value for key, value in requests[1].header_items()
    }
    macrolens_headers = {
        key.lower(): value for key, value in requests[2].header_items()
    }
    assert openai_headers == {
        "authorization": f"Bearer {secrets['OPENAI_API_KEY']}"
    }
    assert finnhub_headers == {
        "x-finnhub-token": secrets["FINNHUB_API_KEY"]
    }
    assert macrolens_headers == {
        "authorization": f"Bearer {secrets['INTERNAL_API_TOKEN']}"
    }
    assert all(response.read_sizes == [4096] for response in responses)


def test_validation_opener_disables_proxies_rejects_redirects_and_uses_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handlers: list[object] = []
    opened: list[tuple[urllib.request.Request, int]] = []
    response = _FakeValidationResponse()

    class FakeOpener:
        def open(
            self,
            request: urllib.request.Request,
            *,
            timeout: int,
        ) -> _FakeValidationResponse:
            opened.append((request, timeout))
            return response

    def fake_build_opener(*received_handlers):
        handlers.extend(received_handlers)
        return FakeOpener()

    monkeypatch.setattr(
        personal_secrets.urllib.request,
        "build_opener",
        fake_build_opener,
    )
    request = urllib.request.Request(
        "https://api.openai.com/v1/models",
        method="GET",
    )

    assert personal_secrets._open_validation_request(request) is response
    assert opened == [(request, 3)]
    assert len(handlers) == 2
    assert isinstance(handlers[0], urllib.request.ProxyHandler)
    assert handlers[0].proxies == {}
    assert isinstance(handlers[1], personal_secrets._RejectRedirects)
    assert handlers[1].redirect_request(
        request,
        None,
        302,
        "private redirect message",
        {},
        "https://redirect.example/private",
    ) is None


@pytest.mark.parametrize(
    ("status", "expected_reason"),
    (
        (302, "redirect_rejected"),
        (401, "authentication_failed"),
        (404, "endpoint_not_found"),
        (429, "rate_limited"),
        (503, "service_unavailable"),
    ),
)
def test_validation_redacts_http_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    status: int,
    expected_reason: str,
) -> None:
    path = tmp_path / "secrets.env"
    monkeypatch.setattr(personal_secrets, "DEFAULT_SECRETS_PATH", path)
    secret = "sk-http-failure-secret-sentinel"
    personal_secrets.atomic_write({"OPENAI_API_KEY": secret}, path)
    response_body = b"private-response-body-sentinel"

    def fail_open(_request: urllib.request.Request):
        raise urllib.error.HTTPError(
            "https://private-error-url.example/path",
            status,
            "private-exception-message-sentinel",
            None,
            io.BytesIO(response_body),
        )

    monkeypatch.setattr(personal_secrets, "_open_validation_request", fail_open)

    assert personal_secrets.main(["validate"]) == 1
    output = capsys.readouterr()
    serialized = output.out + output.err
    assert secret not in serialized
    assert "private-response-body-sentinel" not in serialized
    assert "private-exception-message-sentinel" not in serialized
    assert "private-error-url" not in serialized
    assert "https://" not in serialized
    item = json.loads(output.out)["secrets"]["OPENAI_API_KEY"]
    assert item["connection_checked"] is True
    assert item["connection_ok"] is False
    assert item["connection_skipped"] is False
    assert item["reason"] == expected_reason
    assert item["http_status"] == status


def test_validation_redacts_network_exceptions_and_has_no_fake_status_code(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "secrets.env"
    monkeypatch.setattr(personal_secrets, "DEFAULT_SECRETS_PATH", path)
    secret = "finnhub-network-failure-sentinel"
    personal_secrets.atomic_write({"FINNHUB_API_KEY": secret}, path)

    def fail_open(_request: urllib.request.Request):
        raise urllib.error.URLError("private-network-exception-sentinel")

    monkeypatch.setattr(personal_secrets, "_open_validation_request", fail_open)

    assert personal_secrets.main(["validate"]) == 1
    output = capsys.readouterr()
    serialized = output.out + output.err
    assert secret not in serialized
    assert "private-network-exception-sentinel" not in serialized
    assert "https://" not in serialized
    item = json.loads(output.out)["secrets"]["FINNHUB_API_KEY"]
    assert item["reason"] == "connection_failed"
    assert item["connection_checked"] is True
    assert item["connection_ok"] is False
    assert "http_status" not in item


@pytest.mark.parametrize(
    ("origin", "expected_reason"),
    (
        (None, "macrolens_url_missing"),
        ("http://macrolens.example", "macrolens_url_invalid"),
        ("https://user:password@macrolens.example", "macrolens_url_invalid"),
        ("https://macrolens.example/untrusted", "macrolens_url_invalid"),
        ("https://macrolens.example?target=other", "macrolens_url_invalid"),
        ("https://macrolens.example#fragment", "macrolens_url_invalid"),
        ("https://macrolens.example:", "macrolens_url_invalid"),
    ),
)
def test_configured_macrolens_token_requires_a_safe_https_origin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    origin: str | None,
    expected_reason: str,
) -> None:
    path = tmp_path / "secrets.env"
    monkeypatch.setattr(personal_secrets, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(personal_secrets, "DEFAULT_SECRETS_PATH", path)
    token = "internal-url-validation-sentinel"
    personal_secrets.atomic_write({"INTERNAL_API_TOKEN": token}, path)
    if origin is None:
        monkeypatch.delenv("MACROLENS_URL", raising=False)
    else:
        monkeypatch.setenv("MACROLENS_URL", origin)

    def unexpected_network(_request: urllib.request.Request):
        pytest.fail("unsafe MacroLens configuration attempted a network request")

    monkeypatch.setattr(
        personal_secrets,
        "_open_validation_request",
        unexpected_network,
    )

    assert personal_secrets.main(["validate"]) == 1
    output = capsys.readouterr()
    serialized = output.out + output.err
    assert token not in serialized
    if origin is not None:
        assert origin not in serialized
    item = json.loads(output.out)["secrets"]["INTERNAL_API_TOKEN"]
    assert item["connection_checked"] is False
    assert item["connection_skipped"] is True
    assert item["connection_ok"] is False
    assert item["reason"] == expected_reason


def test_macrolens_validation_uses_files_only_when_environment_is_absent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    (repository_root / ".env").write_text(
        "MACROLENS_URL=https://dotenv-macrolens.example:9443/\n",
        encoding="utf-8",
    )
    path = tmp_path / "secrets.env"
    monkeypatch.setattr(personal_secrets, "REPOSITORY_ROOT", repository_root)
    monkeypatch.setattr(personal_secrets, "DEFAULT_SECRETS_PATH", path)
    monkeypatch.delenv("MACROLENS_URL", raising=False)
    token = "internal-dotenv-fallback-sentinel"
    personal_secrets.atomic_write({"INTERNAL_API_TOKEN": token}, path)
    requests: list[urllib.request.Request] = []

    def fake_open(request: urllib.request.Request) -> _FakeValidationResponse:
        requests.append(request)
        return _FakeValidationResponse()

    monkeypatch.setattr(personal_secrets, "_open_validation_request", fake_open)

    assert personal_secrets.main(["validate"]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert token not in output.out
    assert "dotenv-macrolens.example" not in output.out
    assert [request.full_url for request in requests] == [
        "https://dotenv-macrolens.example:9443/internal/v1/health"
    ]
    item = json.loads(output.out)["secrets"]["INTERNAL_API_TOKEN"]
    assert item["reason"] == "reachable"
    assert item["connection_ok"] is True

    requests.clear()
    monkeypatch.setenv("MACROLENS_URL", "")
    assert personal_secrets.main(["validate"]) == 1
    explicit_empty = capsys.readouterr()
    assert explicit_empty.err == ""
    assert token not in explicit_empty.out
    assert requests == []
    item = json.loads(explicit_empty.out)["secrets"]["INTERNAL_API_TOKEN"]
    assert item["reason"] == "macrolens_url_missing"
    assert item["connection_checked"] is False
    assert item["connection_ok"] is False


def test_password_hash_and_marketdata_token_use_local_validation_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "secrets.env"
    monkeypatch.setattr(personal_secrets, "DEFAULT_SECRETS_PATH", path)
    password_hash = personal_secrets._normalized_value(
        "APP_PASSWORD_HASH",
        "local-validation-owner-password",
    )
    personal_secrets.atomic_write(
        {
            "APP_PASSWORD_HASH": password_hash,
            "MARKETDATA_TOKEN": "marketdata-local-token",
        },
        path,
    )

    def unexpected_network(_request: urllib.request.Request):
        pytest.fail("local-only validation attempted a network request")

    monkeypatch.setattr(
        personal_secrets,
        "_open_validation_request",
        unexpected_network,
    )

    assert personal_secrets.main(["validate"]) == 0
    output = capsys.readouterr()
    assert password_hash not in output.out + output.err
    report = json.loads(output.out)
    for key in ("APP_PASSWORD_HASH", "MARKETDATA_TOKEN"):
        assert report["secrets"][key] == {
            "configured": True,
            "format_valid": True,
            "connection_checked": False,
            "connection_skipped": True,
            "connection_ok": True,
            "reason": "local_validation_only",
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

    def unexpected_network(_request: urllib.request.Request):
        pytest.fail("a world-readable Secret file reached the network")

    monkeypatch.setattr(
        personal_secrets,
        "_open_validation_request",
        unexpected_network,
    )

    assert personal_secrets.main(["validate"]) == 1
    output = capsys.readouterr()
    assert secret not in output.out
    assert secret not in output.err
    report = json.loads(output.out)
    assert report["file"] == {"exists": True, "permission_0600": False}
    assert report["secrets"]["OPENAI_API_KEY"]["reason"] == (
        "file_permissions_invalid"
    )
    assert report["secrets"]["OPENAI_API_KEY"]["connection_skipped"] is True


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


def test_owner_password_hash_is_a_compose_literal_and_round_trips(
    tmp_path: Path,
) -> None:
    path = tmp_path / "secrets.env"
    password_hash = personal_secrets._normalized_value(
        "APP_PASSWORD_HASH",
        "compose-literal-owner-password",
    )

    personal_secrets.atomic_write(
        {"APP_PASSWORD_HASH": password_hash},
        path,
    )

    assert path.read_text(encoding="utf-8") == (
        f"APP_PASSWORD_HASH='{password_hash}'\n"
    )
    assert personal_secrets._read_values(path)["APP_PASSWORD_HASH"] == password_hash
    assert owner_password_hash_is_valid(password_hash)


def test_marketdata_token_is_managed_as_a_server_only_secret() -> None:
    assert personal_secrets._normalized_value(
        "MARKETDATA_TOKEN", "market-token-value"
    ) == "market-token-value"
    assert personal_secrets._format_valid(
        "MARKETDATA_TOKEN", "market-token-value"
    ) is True


def test_shell_interface_never_passes_a_secret_value_to_python() -> None:
    script = (personal_secrets.REPOSITORY_ROOT / "personal.sh").read_text(
        encoding="utf-8"
    )
    assert "PYTHON_BIN" not in script
    assert 'python_bin="python3"' not in script
    assert "build --quiet backend </dev/null >/dev/null" in script
    assert "APP_COMMIT=\"$cli_identity\" APP_VERSION=\"$cli_identity\"" in script
    assert 'run_secret_python rw auto app.tools.personal_secrets "$command_name" "$key"' in script
    assert 'run_secret_python ro disabled app.tools.personal_secrets "$command_name"' in script
    assert "run_doctor" in script
    assert '--volume "$root:/app:$mount_mode"' in script
    assert '--user "$secret_container_user"' in script
    assert '*name=rootless*) secret_container_user="0:0"' in script
    assert "Docker user namespace remapping" in script
    assert "Secret values must be entered through standard input." in script
    assert "FINNHUB_API_KEY|MARKETDATA_TOKEN|MASSIVE_API_KEY|INTERNAL_API_TOKEN" in script
    assert "--force-recreate" in script
    assert ' restart "${running[@]}"' not in script
    assert '"$root/scripts/compose.sh" ps' in script
    assert '"$root/scripts/compose.sh" up' in script
    assert "--no-build --pull never" in script
    assert "--wait --wait-timeout 180" in script
    assert ".personal-operation.lock" in script
    assert 'image_reference" != "option-pro:$image_commit' in script
    assert "docker compose" not in script
    assert 'export COMPOSE_ENV_FILES=".env"' not in script
    assert "app.tools.validate_personal_deployment" in script
    assert "umask 077" in script


def test_real_owner_surfaces_never_return_secret_sentinels(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sentinels = {
        "OPENAI_API_KEY": "sk-boundary-openai-sentinel",
        "FINNHUB_API_KEY": "boundary-finnhub-sentinel",
        "MARKETDATA_TOKEN": "boundary-marketdata-sentinel",
        "INTERNAL_API_TOKEN": "boundary-internal-sentinel",
    }
    for key, value in sentinels.items():
        monkeypatch.setenv(key, value)
    # INTERNAL_API_TOKEN is only valid as one half of the canonical
    # MacroLens connection pair. Keep this leak test on a valid runtime.
    monkeypatch.setenv("MACROLENS_URL", "https://macrolens.invalid")

    paths = (
        "/health",
        "/ready",
        "/api/access/status",
        "/api/settings",
        "/api/runtime-settings",
        "/api/runtime-settings/history",
        "/api/worker/status",
        "/api/worker/actions",
        "/api/catalysts/status",
        "/api/catalysts/feed",
        "/api/catalysts/calendar",
        "/api/catalysts/hotspots/status",
        "/api/catalysts/hotspots",
        "/api/catalysts/market-focus-cycles/latest",
        "/",
        "/static/js/deck-api.js",
        "/static/js/deck-catalysts.js",
    )
    main._rl_buckets.clear()
    client = TestClient(
        main.app,
        base_url="http://localhost",
        client=("127.0.0.1", 50000),
        raise_server_exceptions=False,
    )
    try:
        for path in paths:
            main._rl_buckets.clear()
            response = client.get(path)
            assert response.status_code in {200, 404, 409, 503}, (
                path,
                response.text,
            )
            exposed = response.content + repr(tuple(response.headers.items())).encode()
            for sentinel in sentinels.values():
                assert sentinel.encode() not in exposed
        cookie_text = repr(tuple(client.cookies.items()))
        assert all(sentinel not in cookie_text for sentinel in sentinels.values())
        assert all(sentinel not in caplog.text for sentinel in sentinels.values())
    finally:
        client.close()


def test_every_registered_api_error_response_hides_secret_sentinels(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sentinels = {
        "OPENAI_API_KEY": "sk-all-routes-openai-sentinel",
        "FINNHUB_API_KEY": "all-routes-finnhub-sentinel",
        "MARKETDATA_TOKEN": "all-routes-marketdata-sentinel",
        "INTERNAL_API_TOKEN": "all-routes-internal-sentinel",
        "APP_PASSWORD_HASH": "all-routes-password-sentinel",
        "DATA_DIR": "/all-routes-data-sentinel",
    }
    for key, value in sentinels.items():
        monkeypatch.setenv(key, value)

    parameter_values = {
        "ticker": "AAPL",
        "sector_id": "technology",
        "news_id": "1",
        "job_id": "aij_probe",
        "cycle_id": "mfc_probe",
        "request_id": "req_probe",
        "event_id": "evt_probe",
        "action_type": "retention",
    }

    def probe_path(template: str) -> str:
        return re.sub(
            r"\{([^}]+)\}",
            lambda match: parameter_values.get(match.group(1), "probe"),
            template,
        )

    operations: list[tuple[str, str]] = []
    for template, definition in main.app.openapi()["paths"].items():
        for method in definition:
            normalized = method.upper()
            if normalized in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                operations.append((normalized, probe_path(template)))
    assert len(operations) >= 60

    # A public test address is rejected by the real private-network gateway
    # before any route can contact a provider or mutate local state. This lets
    # the test enumerate every registered API error surface safely.
    main._rl_buckets.clear()
    client = TestClient(
        main.app,
        base_url="http://localhost",
        client=("203.0.113.10", 50001),
        raise_server_exceptions=False,
    )
    try:
        for method, path in operations:
            response = client.request(
                method,
                path,
                headers={
                    "Content-Type": "application/json",
                    "Origin": "http://localhost",
                    "X-Optix-Action": "1",
                },
                content=b"{}" if method != "GET" else None,
            )
            exposed = response.content + repr(tuple(response.headers.items())).encode()
            for key, sentinel in sentinels.items():
                assert key.encode() not in exposed, (method, path, key)
                assert sentinel.encode() not in exposed, (method, path, key)
        assert all(key not in caplog.text for key in sentinels)
        assert all(sentinel not in caplog.text for sentinel in sentinels.values())
    finally:
        client.close()
