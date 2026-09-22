"""Fallback diagnostics must be bounded and omit provider exception text."""

from __future__ import annotations

import logging

import pytest

from app import failure_diagnostics as diagnostics


@pytest.fixture(autouse=True)
def clear_diagnostic_keys():
    diagnostics._seen.clear()
    yield
    diagnostics._seen.clear()


def test_fallback_log_is_redacted_deduplicated_and_expires(monkeypatch, caplog):
    now = [1000.0]
    monkeypatch.setattr(diagnostics, "_clock", lambda: now[0])
    secret = "https://provider.example/?api_key=private-token"

    with caplog.at_level(logging.WARNING, logger=diagnostics.__name__):
        diagnostics.record_fallback_failure("stocks_macro_compute", RuntimeError(secret), symbol=" aapl ")
        diagnostics.record_fallback_failure("stocks_macro_compute", RuntimeError(secret), symbol="AAPL")
        diagnostics.record_fallback_failure("stocks_macro_compute", ValueError(secret), symbol="AAPL")
        now[0] += diagnostics._interval_seconds
        diagnostics.record_fallback_failure("stocks_macro_compute", RuntimeError(secret), symbol="AAPL")

    assert len(caplog.records) == 3
    assert caplog.records[0].getMessage() == (
        "fallback_failure stage=stocks_macro_compute symbol=AAPL error_type=RuntimeError"
    )
    assert all(record.exc_info is None for record in caplog.records)
    assert secret not in caplog.text


def test_fallback_log_rejects_untrusted_fields_and_bounds_key_table(monkeypatch, caplog):
    now = [0.0]
    monkeypatch.setattr(diagnostics, "_clock", lambda: now[0])
    monkeypatch.setattr(diagnostics, "_max_keys", 2)
    strange_error = type("https://private.example/secret", (Exception,), {})

    with caplog.at_level(logging.WARNING, logger=diagnostics.__name__):
        diagnostics.record_fallback_failure("invalid/stage", strange_error(), symbol="ABC?token=private")
        diagnostics.record_fallback_failure("second_stage", RuntimeError("private"), symbol="AAPL")
        diagnostics.record_fallback_failure("third_stage", RuntimeError("private"), symbol="MSFT")
        assert len(caplog.records) == 2
        now[0] += diagnostics._interval_seconds
        diagnostics.record_fallback_failure("third_stage", RuntimeError("private"), symbol="MSFT")

    assert len(caplog.records) == 3
    assert len(diagnostics._seen) <= 2
    assert "private" not in caplog.text
    assert "https" not in caplog.text
    assert "stage=unknown symbol=- error_type=UnknownError" in caplog.text


def test_logging_failure_cannot_replace_original_fallback(monkeypatch):
    def broken_logger(*_args, **_kwargs):
        raise OSError("logging unavailable")

    monkeypatch.setattr(diagnostics._logger, "warning", broken_logger)
    diagnostics.record_fallback_failure("stocks_intraday_analysis", RuntimeError("provider failed"))
