"""The runtime contract's schema derivations are memoized (feed latency).

Serving 114 news items generated 342 Pydantic JSON schemas -- 2.9 of that
endpoint's 4.6 seconds -- because schema_identity, max_input_tokens_for and
max_tool_calls_for each rebuild the request, and the feed asks per item.

Two properties have to hold for the cache to be safe, and both are asserted
here rather than assumed:

1. The identity hash is unchanged. It is stored alongside jobs; if it moved,
   every stored job would suddenly look like it was produced under a different
   contract.
2. The cached schema cannot be written through. Callers put it into a paid API
   request payload, so handing them all one shared mutable dict would let one
   caller corrupt it for the rest.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from app.services.ai_jobs import runtime as rt
from app.services.ai_jobs.models import result_model_for

JOB_TYPES = (
    "news_impact",
    "earnings_impact",
    "market_focus",
    "signal_analysis",
    "option_alerts",
)


def _identity_the_long_way(job_type: str) -> str:
    """Recompute the identity with the schema straight from Pydantic.

    Deliberately does not reuse the cached accessor: this is the pre-cache
    derivation, so agreeing with it is evidence and not a tautology.
    """

    request = rt.build_runtime_request(job_type, {})
    payload = {
        "instructions": request.instructions,
        "result_validation_contract": rt.RESULT_VALIDATION_CONTRACT_VERSION,
        "schema": result_model_for(job_type).model_json_schema(mode="validation"),
        "schema_name": request.schema_name,
        "max_input_tokens": rt.max_input_tokens_for(job_type),
        "max_output_tokens": rt.max_output_tokens_for(job_type),
        "max_tool_calls": rt.max_tool_calls_for(job_type),
        "use_web_search": request.use_web_search,
    }
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("job_type", JOB_TYPES)
def test_caching_the_schema_does_not_move_the_identity_hash(job_type: str) -> None:
    assert rt.schema_identity(job_type)[1] == _identity_the_long_way(job_type)


@pytest.mark.parametrize("job_type", JOB_TYPES)
def test_the_json_round_trip_preserves_the_schema_exactly(job_type: str) -> None:
    """The cache stores a string; loading it back must reproduce the schema.

    A round trip through JSON would turn tuples into lists. Pydantic does not
    emit tuples today, and this asserts it rather than trusting it.
    """

    assert rt.build_runtime_request(job_type, {}).schema == result_model_for(
        job_type
    ).model_json_schema(mode="validation")


@pytest.mark.parametrize("job_type", JOB_TYPES)
def test_two_callers_never_share_one_schema_object(job_type: str) -> None:
    first = rt.build_runtime_request(job_type, {}).schema
    second = rt.build_runtime_request(job_type, {}).schema
    assert first == second
    assert first is not second, "callers share one mutable dict"

    first["__written_through__"] = True
    third = rt.build_runtime_request(job_type, {}).schema
    assert "__written_through__" not in third, "the cache was poisoned"


def test_an_unsupported_job_type_still_raises_rather_than_caching_a_failure() -> None:
    for _ in range(2):
        with pytest.raises(ValueError):
            rt.schema_identity("not_a_job_type")


def test_only_the_model_derived_schema_is_cached() -> None:
    """The mutable policy table must stay live in the identity.

    Memoizing schema_identity itself is the tempting version of this change and
    it is wrong: the identity folds in max_output_tokens_for, which reads a
    mutable module dict, and a stored job produced under a different policy must
    not keep looking current. Only the model's JSON schema is immutable at
    runtime, so only that is cached.
    """

    assert hasattr(rt._validation_schema_json, "cache_clear"), (
        "the schema derivation is the one that should be memoized"
    )
    for name in ("schema_identity", "max_input_tokens_for", "max_tool_calls_for"):
        assert not hasattr(getattr(rt, name), "cache_clear"), (
            f"{name} reads the mutable policy table; caching it freezes the identity"
        )
