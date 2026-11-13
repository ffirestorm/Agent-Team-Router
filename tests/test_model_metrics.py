import copy

import pytest

from atr.runtime.metrics import summarize_models


def test_missing_usage_and_complete_totals():
    result = summarize_models([{"attempts": [
        {"model": "b", "usage": {"input_tokens": 10, "output_tokens": 0,
                                  "cached_input_tokens": 4, "cost": 1.5}},
        {"model": "b", "usage": {"input_tokens": None, "output_tokens": 2,
                                  "cached_input_tokens": 1}},
        {"model": "a", "seconds": 0, "usage": {"input_tokens": 5, "output_tokens": 1,
                                                   "cached_input_tokens": 3, "cost": 0}},
    ]}])
    a, b = result["models"]
    assert [a["model"], b["model"]] == ["a", "b"]
    assert a["cost"] == 0 and a["seconds_missing_attempts"] == 0
    assert a["token_usage"]["input_tokens"]["total"] == 5
    assert b["cost"] is None and b["cost_missing_attempts"] == 1
    assert b["token_usage"]["input_tokens"] == {"known_subtotal": 10, "missing_attempts": 1, "total": None}
    assert b["token_usage"]["output_tokens"]["total"] == 2
    assert b["token_usage"]["cached_input_tokens"]["total"] == 5
    assert b["seconds_missing_attempts"] == 2
    assert result["note"] and "difficulty" in result["note"]


def test_record_identity_failures_escalation_and_invalid_attempts():
    records = [
        {"id": "same", "attempts": [{"model": "x", "failure": "quality"},
                                    {"model": "x", "kind": "escalation", "status": "succeeded"},
                                    {"model": "y", "failure": "quality"}, None]},
        {"id": "same", "attempts": [{"model": "x", "failure": "quality", "kind": "escalation"}]},
        {"attempts": [{"model": "x", "failure": ""}, {}]},
        {"attempts": None}, {"attempts": {}}, {},
    ]
    before = copy.deepcopy(records)
    result = summarize_models(records)
    assert records == before
    rows = {row["model"]: row for row in result["models"]}
    x = rows["x"]
    assert (x["tasks"], x["attempts"], x["succeeded_attempts"]) == (3, 4, 1)
    assert x["attempt_success_rate"] == .25 and x["failures"] == {"quality": 2}
    assert (x["upgraded_attempts"], x["succeeded_upgraded_attempts"]) == (2, 1)
    assert rows["unknown"]["tasks"] == 1
    assert summarize_models([])["models"] == []


@pytest.mark.parametrize("invalid", [None, True, False, -1, float("nan"), float("inf"), -float("inf"), "1"])
def test_invalid_numbers_are_missing(invalid):
    row = summarize_models([{"attempts": [
        {"seconds": invalid, "usage": {"cost": invalid, "input_tokens": invalid,
                                       "output_tokens": invalid, "cached_input_tokens": invalid}},
        {"seconds": 2, "usage": {"cost": 3, "input_tokens": 4,
                                  "output_tokens": 5, "cached_input_tokens": 1}},
    ]}])["models"][0]
    assert row["elapsed_seconds"] == 2 and row["seconds_missing_attempts"] == 1
    assert row["cost"] is None and row["cost_missing_attempts"] == 1
    for key, subtotal in (("input_tokens", 4), ("output_tokens", 5), ("cached_input_tokens", 1)):
        assert row["token_usage"][key] == {"known_subtotal": subtotal, "missing_attempts": 1, "total": None}
