import json
import math

import pytest

from app import public_home_snapshot, stock_pull_snapshot
from app.api import sectors, stocks, strength


@pytest.mark.parametrize("module", [public_home_snapshot, stock_pull_snapshot, sectors, stocks, strength])
@pytest.mark.parametrize(("raw", "message"), [
    ('{"outer":{"x":1,"x":2}}', "duplicate JSON key: x"),
    ('{"x":NaN}', "non-finite JSON value: NaN"),
    ('{"x":Infinity}', "non-finite JSON value: Infinity"),
    ('{"x":-Infinity}', "non-finite JSON value: -Infinity"),
])
def test_snapshot_json_hooks_preserve_rejection_messages(module, raw, message):
    with pytest.raises(ValueError) as error:
        json.loads(raw, object_pairs_hook=module._reject_duplicate_json_keys,
                   parse_constant=module._reject_non_finite_json)
    assert str(error.value) == message


@pytest.mark.parametrize("module", [public_home_snapshot, stock_pull_snapshot, sectors, stocks, strength])
def test_snapshot_json_hooks_preserve_valid_ordered_objects(module):
    value = json.loads('{"b":false,"a":[null,1,2.5,{"x":"value"}]}',
                       object_pairs_hook=module._reject_duplicate_json_keys,
                       parse_constant=module._reject_non_finite_json)
    assert list(value) == ["b", "a"]
    assert value == {"b": False, "a": [None, 1, 2.5, {"x": "value"}]}


def _nested(depth):
    value = None
    for _ in range(depth):
        value = [value]
    return value


@pytest.mark.parametrize("module", [stocks, strength])
def test_api_finite_json_tree_keeps_64_level_contract(module):
    validate = module._is_finite_json_tree
    assert validate(_nested(64))
    assert not validate(_nested(65))
    assert validate({"value": [True, None, 10 ** 400, 1.5, "text"]})
    for value in (math.nan, math.inf, -math.inf, {1: "invalid key"}, (1, 2), object()):
        assert not validate(value)


@pytest.mark.parametrize(("module", "accepts_large_tree"), [(public_home_snapshot, True), (stock_pull_snapshot, False)])
def test_bounded_snapshot_trees_keep_separate_depth_and_node_limits(module, accepts_large_tree):
    validate = module._valid_json_tree
    assert validate(_nested(12))
    assert not validate(_nested(13))
    # 110,012 nodes: between the stock-pull and public-home limits.
    assert validate([[0] * 10_000 for _ in range(11)]) is accepts_large_tree
    assert not validate([[0] * 10_000 for _ in range(26)])
