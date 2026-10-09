"""The canonical JSON bytes are pinned: hashes, identities and stored rows use them."""

from __future__ import annotations

import hashlib
import math

import pytest

from app.json_validation import canonical_json_text

SAMPLE = {
    "中文": "é",
    "f": [0.1, 1e21, -0.0, 1.0],
    "n": None,
    "b": True,
    "nested": {"z": 1, "a": [1, {"y": 2}]},
}
EXPECTED = '{"b":true,"f":[0.1,1e+21,-0.0,1.0],"n":null,"nested":{"a":[1,{"y":2}],"z":1},"中文":"é"}'
EXPECTED_SHA256 = "8f60268da8b0df863a616418cbf5c8191e6398dc2b87d30e3bfc381561dc0cb3"


def test_canonical_json_text_bytes_are_pinned() -> None:
    text = canonical_json_text(SAMPLE)

    assert text == EXPECTED
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == EXPECTED_SHA256


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_canonical_json_text_rejects_non_finite_numbers(value: float) -> None:
    with pytest.raises(ValueError):
        canonical_json_text({"x": value})


def test_every_canonical_writer_emits_the_pinned_bytes() -> None:
    from app.api.signals import _canonical_evidence_bytes
    from app.services.ai_jobs.models import earnings_input_hash
    from app.services.ai_jobs.repository import AIJobRepository
    from app.services.breakouts.research import _serialize_cell
    from app.services.eod_limited.market_data import _hash_json
    from app.services.macro_conditions.repository import _stable_json
    from app.worker.state import _details_json

    assert _canonical_evidence_bytes(SAMPLE) == EXPECTED.encode("utf-8")
    assert AIJobRepository._canonical_json(SAMPLE, max_bytes=10_000, error_code="x") == EXPECTED
    assert AIJobRepository._canonical_payload(SAMPLE) == EXPECTED
    assert _serialize_cell(SAMPLE) == EXPECTED
    assert _stable_json(SAMPLE) == EXPECTED
    assert _details_json(SAMPLE) == EXPECTED
    assert _hash_json(SAMPLE) == EXPECTED_SHA256
    # Value computed by the pre-merge inline serializer on main 72f426f0.
    assert earnings_input_hash(
        {
            "ticker": "AAPL",
            "name": "苹果",
            "eps_estimate": 1.5,
            "market_cap": 3e12,
            "earnings_date": "2026-10-30",
        }
    ) == "573686d257cc102fbc815ce47a159f3b4d8afbfe935cf006be3c891011b34721"
