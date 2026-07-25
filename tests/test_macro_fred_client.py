"""FRED client and series-unit behaviour. Fully offline via httpx MockTransport.

No test here reaches api.stlouisfed.org, Massive, Yahoo, MacroLens or OpenAI.
"""

from __future__ import annotations

import datetime as dt
import json
import logging

import httpx
import pytest

from app.services.macro_conditions.fred_client import (
    FRED_ORIGIN,
    MAX_ATTEMPTS,
    MAX_RESPONSE_BYTES,
    FredClient,
)
from app.services.macro_conditions.models import MacroError
from app.services.macro_conditions.registry import (
    SERIES_BY_ID,
    UnitsMismatch,
    frequency_at_most,
    scale_to_canonical,
)
from app.services.macro_conditions.repository import (
    HISTORY_BASIS_BACKFILL,
    MacroRepository,
)
from macro_fixtures import synthetic_metadata


KEY = "abcdef0123456789abcdef0123456789"
WALCL = SERIES_BY_ID["WALCL"]
SOFR = SERIES_BY_ID["SOFR"]


def _json_response(payload: dict, status: int = 200) -> httpx.Response:
    body = json.dumps(payload).encode("utf-8")
    return httpx.Response(
        status,
        content=body,
        headers={"Content-Type": "application/json"},
    )


def _metadata_payload(
    series_id: str = "WALCL",
    *,
    units: str = "Millions of U.S. Dollars",
    frequency_short: str = "W",
) -> dict:
    return {
        "seriess": [
            {
                "id": series_id,
                "units": units,
                "frequency_short": frequency_short,
                "last_updated": "2026-07-24 15:31:02-05",
                "realtime_start": "2026-07-24",
                "realtime_end": "9999-12-31",
            }
        ]
    }


def _client(handler, *, sleeps: list[float] | None = None) -> FredClient:
    recorded = sleeps if sleeps is not None else []
    return FredClient(
        KEY,
        transport=httpx.MockTransport(handler),
        sleep=recorded.append,
    )


# ---------------------------------------------------------------------------
# 1-2 happy paths
# ---------------------------------------------------------------------------


def test_metadata_and_observations_parse_and_convert_to_canonical_units() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith(FRED_ORIGIN)
        if request.url.path == "/fred/series":
            return _json_response(_metadata_payload())
        return _json_response(
            {
                "observations": [
                    {"date": "2026-07-01", "value": "6800500"},
                    {"date": "2026-07-08", "value": "6810000"},
                ]
            }
        )

    with _client(handler) as client:
        metadata = client.metadata(WALCL)
        assert metadata.scale_to_canonical == 0.001
        assert metadata.canonical_unit == "usd_billions"
        assert metadata.source_last_updated
        observations = client.observations(
            WALCL,
            metadata,
            start=dt.date(2026, 6, 1),
            end=dt.date(2026, 7, 24),
        )
    assert [item.observation_date for item in observations] == [
        dt.date(2026, 7, 1),
        dt.date(2026, 7, 8),
    ]
    # Millions → billions, so the raw 6_800_500 becomes 6800.5.
    assert observations[0].value == pytest.approx(6800.5)


# ---------------------------------------------------------------------------
# 3-6 value parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", [".", "", "  ", "NaN", "inf", "-inf", "abc"])
def test_missing_and_non_finite_values_become_none(raw: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/fred/series":
            return _json_response(_metadata_payload("SOFR", units="Percent", frequency_short="D"))
        return _json_response({"observations": [{"date": "2026-07-20", "value": raw}]})

    with _client(handler) as client:
        fetch = client.fetch(SOFR, start=dt.date(2026, 7, 1), end=dt.date(2026, 7, 24))
    assert len(fetch.observations) == 1
    assert fetch.observations[0].value is None


def test_duplicate_observation_dates_resolve_deterministically() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/fred/series":
            return _json_response(_metadata_payload("SOFR", units="Percent", frequency_short="D"))
        return _json_response(
            {
                "observations": [
                    {"date": "2026-07-20", "value": "4.30"},
                    {"date": "2026-07-20", "value": "."},
                    {"date": "2026-07-20", "value": "4.35"},
                ]
            }
        )

    with _client(handler) as client:
        fetch = client.fetch(SOFR, start=dt.date(2026, 7, 1), end=dt.date(2026, 7, 24))
    # One row per date; a real value always beats a missing one, last wins.
    assert len(fetch.observations) == 1
    assert fetch.observations[0].value == pytest.approx(4.35)


# ---------------------------------------------------------------------------
# 5-6 metadata drift
# ---------------------------------------------------------------------------


def test_units_outside_the_expected_family_are_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(_metadata_payload(units="Percent"))

    with _client(handler) as client:
        with pytest.raises(MacroError) as excinfo:
            client.metadata(WALCL)
    assert excinfo.value.code == "fred_units_mismatch"


def test_a_higher_publication_frequency_than_registered_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(_metadata_payload(frequency_short="D"))

    with _client(handler) as client:
        with pytest.raises(MacroError) as excinfo:
            client.metadata(WALCL)
    assert excinfo.value.code == "fred_schema_mismatch"


def test_a_different_series_id_in_the_response_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(_metadata_payload("WRESBAL"))

    with _client(handler) as client:
        with pytest.raises(MacroError) as excinfo:
            client.metadata(WALCL)
    assert excinfo.value.code == "fred_schema_mismatch"


def test_unknown_units_are_never_silently_converted() -> None:
    with pytest.raises(UnitsMismatch):
        scale_to_canonical("Zorkmids", "usd_amount")
    with pytest.raises(UnitsMismatch):
        scale_to_canonical("", "usd_amount")
    assert scale_to_canonical("Billions of U.S. Dollars", "usd_amount") == 1.0
    assert scale_to_canonical("Millions of U.S. Dollars", "usd_amount") == 0.001
    assert scale_to_canonical("Index Jan 2006=100", "index") == 1.0


def test_frequency_rank_comparison() -> None:
    assert frequency_at_most("W", "W") is True
    assert frequency_at_most("M", "W") is True
    assert frequency_at_most("D", "W") is False
    assert frequency_at_most("?", "W") is False


# ---------------------------------------------------------------------------
# 7-13 transport behaviour
# ---------------------------------------------------------------------------


def test_rate_limiting_respects_retry_after_then_gives_up() -> None:
    attempts = 0
    sleeps: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(429, headers={"Retry-After": "3"})

    with _client(handler, sleeps=sleeps) as client:
        with pytest.raises(MacroError) as excinfo:
            client.metadata(WALCL)
    assert excinfo.value.code == "fred_rate_limited"
    assert attempts == MAX_ATTEMPTS
    assert sleeps == [3.0, 3.0]


def test_server_errors_retry_with_bounded_backoff() -> None:
    attempts = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < MAX_ATTEMPTS:
            return httpx.Response(503)
        return _json_response(_metadata_payload())

    with _client(handler, sleeps=sleeps) as client:
        metadata = client.metadata(WALCL)
    assert metadata.series_id == "WALCL"
    assert attempts == MAX_ATTEMPTS
    assert sleeps and all(0 < value <= 8 for value in sleeps)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_non_throttling_client_errors_do_not_retry(status: int) -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(status)

    with _client(handler) as client:
        with pytest.raises(MacroError) as excinfo:
            client.metadata(WALCL)
    assert excinfo.value.code == "fred_unavailable"
    assert attempts == 1


def test_non_json_content_type_is_a_schema_mismatch_without_retry() -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, text="<html>nope</html>", headers={"Content-Type": "text/html"})

    with _client(handler) as client:
        with pytest.raises(MacroError) as excinfo:
            client.metadata(WALCL)
    assert excinfo.value.code == "fred_schema_mismatch"
    assert attempts == 1


def test_malformed_json_is_a_schema_mismatch_without_retry() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"{not json",
            headers={"Content-Type": "application/json"},
        )

    with _client(handler) as client:
        with pytest.raises(MacroError) as excinfo:
            client.metadata(WALCL)
    assert excinfo.value.code == "fred_schema_mismatch"


def test_oversize_response_is_rejected_by_declared_length_and_by_body() -> None:
    def declared(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"{}",
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(MAX_RESPONSE_BYTES + 1),
            },
        )

    with _client(declared) as client:
        with pytest.raises(MacroError) as excinfo:
            client.metadata(WALCL)
    assert excinfo.value.code == "fred_response_too_large"

    oversize = json.dumps({"padding": "x" * (MAX_RESPONSE_BYTES + 16)}).encode("utf-8")

    def body(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=oversize,
            headers={"Content-Type": "application/json"},
        )

    with _client(body) as client:
        with pytest.raises(MacroError) as excinfo:
            client.metadata(WALCL)
    assert excinfo.value.code == "fred_response_too_large"


def test_timeouts_are_reported_as_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with _client(handler) as client:
        with pytest.raises(MacroError) as excinfo:
            client.metadata(WALCL)
    assert excinfo.value.code == "fred_unavailable"


def test_redirects_are_refused_so_the_origin_cannot_move() -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(302, headers={"Location": "https://evil.example/fred/series"})

    with _client(handler) as client:
        with pytest.raises(MacroError) as excinfo:
            client.metadata(WALCL)
    assert excinfo.value.code == "fred_unavailable"
    assert attempts == 1


def test_client_refuses_to_start_without_a_key() -> None:
    with pytest.raises(MacroError) as excinfo:
        FredClient("   ")
    assert excinfo.value.code == "fred_api_key_missing"


# ---------------------------------------------------------------------------
# 14 secret hygiene
# ---------------------------------------------------------------------------


def test_logs_record_only_a_series_id_and_a_safe_error_code(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    caplog.set_level(logging.DEBUG, logger="optix.macro.fred")
    with _client(handler) as client:
        with pytest.raises(MacroError):
            client.metadata(WALCL)
    text = caplog.text
    assert "WALCL" in text
    assert "fred_unavailable" in text
    assert KEY not in text
    assert "api_key" not in text
    assert FRED_ORIGIN not in text


def test_a_failing_series_never_aborts_the_rest_of_the_batch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        series_id = request.url.params.get("series_id")
        if series_id == "WTREGEN":
            return httpx.Response(500)
        family = SERIES_BY_ID[series_id].expected_units_family
        units = {
            "usd_amount": "Millions of U.S. Dollars",
            "percent": "Percent",
            "index": "Index",
            "usd_per_barrel": "Dollars per Barrel",
            "usd_per_mmbtu": "Dollars per Million BTU",
        }[family]
        if request.url.path == "/fred/series":
            return _json_response(
                _metadata_payload(
                    series_id,
                    units=units,
                    frequency_short=SERIES_BY_ID[series_id].expected_frequency,
                )
            )
        return _json_response({"observations": [{"date": "2026-07-20", "value": "1.0"}]})

    specs = [SERIES_BY_ID[name] for name in ("WALCL", "WTREGEN", "SOFR")]
    with _client(handler) as client:
        fetched, failures = client.fetch_many(
            specs,
            start=dt.date(2026, 7, 1),
            end=dt.date(2026, 7, 24),
        )
    assert set(fetched) == {"WALCL", "SOFR"}
    assert failures == {"WTREGEN": "fred_unavailable"}


# ---------------------------------------------------------------------------
# 15-19 unit conversion and revision storage
# ---------------------------------------------------------------------------


def test_billions_are_not_divided_a_second_time() -> None:
    metadata = synthetic_metadata("WALCL")
    assert metadata.scale_to_canonical == 0.001
    already_billions = scale_to_canonical("Billions of U.S. Dollars", "usd_amount")
    assert already_billions == 1.0


def test_revision_changes_append_a_row_and_unchanged_values_only_touch_last_seen(
    tmp_path,
) -> None:
    from app.services.macro_conditions.models import SeriesObservation

    repository = MacroRepository(tmp_path / "macro-conditions.db")
    repository.initialize()
    metadata = synthetic_metadata("WALCL")
    day = dt.date(2026, 7, 1)

    first = repository.record_series_revisions(
        metadata,
        [SeriesObservation("WALCL", day, 6800.5)],
        history_basis=HISTORY_BASIS_BACKFILL,
        observed_at="2026-07-24T00:00:00Z",
    )
    assert first == {"inserted": 1, "unchanged": 0}

    same = repository.record_series_revisions(
        metadata,
        [SeriesObservation("WALCL", day, 6800.5)],
        history_basis=HISTORY_BASIS_BACKFILL,
        observed_at="2026-07-25T00:00:00Z",
    )
    assert same == {"inserted": 0, "unchanged": 1}

    revised = repository.record_series_revisions(
        metadata,
        [SeriesObservation("WALCL", day, 6801.25)],
        history_basis=HISTORY_BASIS_BACKFILL,
        observed_at="2026-07-26T00:00:00Z",
    )
    assert revised == {"inserted": 1, "unchanged": 0}

    with repository.read() as connection:
        rows = connection.execute(
            """SELECT value, first_seen_at, last_seen_at FROM macro_series_revisions
               WHERE series_id='WALCL' ORDER BY first_seen_at""",
        ).fetchall()
    # The superseded revision is retained; the trail is immutable.
    assert [row["value"] for row in rows] == [6800.5, 6801.25]
    assert rows[0]["last_seen_at"] == "2026-07-25T00:00:00Z"
    # The newest revision is the active one.
    active = repository.active_series("WALCL")
    assert [row["value"] for row in active] == [6801.25]


def test_incremental_refresh_is_idempotent(tmp_path) -> None:
    from app.services.macro_conditions.models import SeriesObservation

    repository = MacroRepository(tmp_path / "macro-conditions.db")
    repository.initialize()
    metadata = synthetic_metadata("SOFR")
    observations = [
        SeriesObservation("SOFR", dt.date(2026, 7, 20) + dt.timedelta(days=index), 4.3 + index / 100)
        for index in range(3)
    ]
    for _ in range(3):
        repository.record_series_revisions(
            metadata,
            observations,
            history_basis=HISTORY_BASIS_BACKFILL,
            observed_at="2026-07-24T00:00:00Z",
        )
    with repository.read() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM macro_series_revisions WHERE series_id='SOFR'"
        ).fetchone()[0]
    assert count == 3
    assert len(repository.active_series("SOFR")) == 3
